"""任意规模绑定数据 -> moc3 section 图（静态 + 参数形变键型编译路径）。

与 moc3_builder 的最小模型共用容器与校验层，区别在于本模块处理真实规模：
任意数量部件 / 网格 / 参数，顶点与 UV 由调用方给出。

已按官方内核实测确定的结构规则：
  * **顶点位置以单位坐标存储**（= 画布像素 / pixels_per_unit，相对原点）；
    RigSpec 的输入契约仍是画布像素，换算只发生在写出时。canvasInfo 各字段
    仍是像素（与官方 Haru 一致）。
  * 对象引用的带索引必须真实存在，**-1 会被判为 Data section invalid**；
    不被参数驱动的对象统一引用 band 0（空带）。
  * art_mesh.position_index_counts = 唯一顶点数（uv 段占 2x 个浮点数），
    art_mesh.vertex_counts = 索引条目数（position_index 段占该值个索引）。

参数形变键型（对官方 Haru 全量 84 网格核实，见 tools/probe_haru_keyforms.py）：
  * binding i 与参数 i 一一对应；parameter.keyform_binding_{begin,counts}
    划出属于该参数的 binding 编号。
  * 对象的 keyform 数 == 其带内各 binding 键数之积（单参数即键数）。
  * keyform_position_begin_indices 全部为 16 浮点数（64 字节）的倍数，
    每个关键形占 2x唯一顶点数 个浮点数并补齐到 16 的倍数。
  * 一个带可引用多个 binding（官方存在 2-3 个的带），但多参数带的
    keyform 轴序尚未核实 —— 本模块只编译单参数带，多参数即报错。
  * 同一参数可以持有多个 binding：键值布局不同的网格各自成组，
    parameter.keyform_binding_{begin,counts} 划出该参数名下的连续 binding。

变形器（warp / rotation）尚未支持：出现即报错，不静默丢弃。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Union

from live2d_builder.exporter import moc3_sections as ms
from live2d_builder.exporter.moc3_container import CanvasInfo, Moc3Container

# position_index 是有符号 16 位索引，且按网格局部编号
_MAX_MESH_VERTICES = 32767

# 官方导出器对每个关键形的顶点位置按 64 字节（16 个 f32）对齐
_KEYFORM_POS_ALIGN = 16


class UnsupportedRig(Exception):
    """绑定数据包含本模块尚未支持的形态。"""


@dataclass(frozen=True)
class MeshSpec:
    """一个画布网格：自带部件、自带一份顶点/UV/三角化。"""
    mesh_id: str
    vertices: Sequence[Sequence[float]]        # 画布像素坐标（相对画布原点）
    triangles: Sequence[Sequence[int]]         # 指向本网格 vertices 的三元组
    uvs: Sequence[Sequence[float]]             # 与 vertices 等长的 (u, v)
    draw_order: float = 0.0
    opacity: float = 1.0
    texture_index: int = 0
    part_id: str = ""
    deformer_id: str = ""                      # 挂在该 warp 变形器下（空=不挂）
    keyform_parameter_id: str = ""             # 单参数驱动（须在 parameters 声明）
    keyform_shapes: Sequence["KeyformShape"] = ()   # 逐键形状；非空则启用参数形变
    # 多参数带：((参数 id, 键值...), ...)，**第一个轴步长为 1**（F-06 实测定论，
    # 见 tools/probe_multiband_axis_order.py）。给出时 keyform_shapes 必须按展平序
    # index = Σ_j i_j·Π_{m<j}k_m 排列，且 keyform_parameter_id 可留空。
    keyform_axes: Sequence[Sequence] = ()

    @property
    def effective_part_id(self) -> str:
        return self.part_id or f"Part_{self.mesh_id}"


@dataclass(frozen=True)
class KeyformShape:
    """参数取 key_value 时本网格的形状（顶点数须与网格一致）。"""
    key_value: float
    vertices: Sequence[Sequence[float]]
    opacity: Optional[float] = None            # 未指定则沿用网格 opacity
    draw_order: Optional[float] = None         # 未指定则沿用网格 draw_order


@dataclass(frozen=True)
class DeformerGrid:
    """参数取 key_value 时变形器控制网格的点阵。"""
    key_value: float
    points: Sequence[Sequence[float]]          # (rows+1)*(cols+1) 个画布像素坐标


@dataclass(frozen=True)
class WarpDeformerSpec:
    """warp 变形器：(rows+1)x(cols+1) 控制点网格，驱动挂在它下面的网格。

    rows/cols 是**格子数**，顶点数是 (rows+1)*(cols+1) —— 对官方 Haru 实测确认。
    单个 grid 表示不随参数变化的静态网格；多个 grid 必须给出驱动参数。
    """
    deformer_id: str
    rows: int
    cols: int
    grids: Sequence[DeformerGrid]
    parameter_id: str = ""
    parent_part_id: str = ""                   # 空字符串挂到第一个部件
    parent_deformer_id: str = ""               # 空字符串表示根变形器
    opacity: float = 1.0

    @property
    def vertex_count(self) -> int:
        return (self.rows + 1) * (self.cols + 1)

    @property
    def key_values(self) -> tuple:
        return tuple(float(g.key_value) for g in self.grids)

    @property
    def deformer_type(self) -> int:
        return 0                                   # 官方 Haru: 0 = warp


@dataclass(frozen=True)
class RotationKeyform:
    """参数取 key_value 时 rotation 变形器的姿态。

    ``angle`` 是**角度制、正值 = 模型空间逆时针**（官方 Haru 的 253 个 rotation
    键形取值 −205.4..205.4，常见 179.1/180/181 这类整数，弧度制不可能长这样；
    朝向由 ``tools/probe_rotation_pixels.py`` 实测：+90° 把 x∈[0,2],y∈[0,1.5]
    的三角形转成 x∈[−1.5,0],y∈[0,2]）。

    ``origin`` 是**想绕的枢轴**，与网格顶点同一空间（**画布像素、原点居中、y 向上**）；
    编译器按实测的 ``p' = R·p + offset`` 关系换算成 ``(I − R)·origin`` 写入
    （``tools/probe_rotation_offset.py`` 在 0°/30°/60°/−45° 四个角度上逐像素验证）。
    ``scale`` 1.0 = 不放缩（2.0 实测两轴各翻倍，0 会把子树压没，负值等价于再转 180°）。
    ``reflect_*`` 实测在中性值下置 1 会让整棵子树不画，官方 Haru 也全为 0，
    所以产物侧必须保持 0（语义未定，不做臆测）。
    """
    key_value: float
    angle: float = 0.0
    origin: Sequence[float] = (0.0, 0.0)
    scale: float = 1.0
    reflect_x: int = 0
    reflect_y: int = 0
    opacity: float = 1.0


@dataclass(frozen=True)
class RotationDeformerSpec:
    """rotation 变形器：整棵子树绕 origin 刚体转 angle 度。

    与 warp 不同，挂在它下面的网格顶点**照旧存模型空间坐标**（官方 Haru：
    rotation 下 4 个网格顶点在像素模型空间，而 warp 下 80 个在 (s,t)）。
    单个 keyform = 不随参数变化；多个 keyform 必须给出驱动参数。
    """
    deformer_id: str
    keyforms: Sequence[RotationKeyform]
    base_angle: float = 0.0
    parameter_id: str = ""
    parent_part_id: str = ""
    parent_deformer_id: str = ""
    opacity: float = 1.0

    @property
    def key_values(self) -> tuple:
        return tuple(float(k.key_value) for k in self.keyforms)

    @property
    def deformer_type(self) -> int:
        return 1                                   # 官方 Haru: 1 = rotation


@dataclass(frozen=True)
class ParameterSpec:
    """参数声明；被网格引用时可携带逐键形状。"""
    parameter_id: str
    minimum: float = -30.0
    maximum: float = 30.0
    default: float = 0.0
    decimal_places: int = 2


@dataclass(frozen=True)
class RigSpec:
    """一次编译的完整输入。"""
    meshes: Sequence[MeshSpec]
    parameters: Sequence[ParameterSpec] = ()
    canvas_width: float = 512.0
    canvas_height: float = 512.0
    pixels_per_unit: float = 100.0
    deformers: Sequence[Union[WarpDeformerSpec, "RotationDeformerSpec"]] = ()
    deformer_count: int = 0
    uncompiled_deformers: Sequence[str] = ()   # 未能表达进 moc3 的变形器说明
    # 已编译但只是静态姿态的变形器（条目里没有参数绑定）。单列出来是为了
    # 不让「变形器已编译」被误读成「它会跟着参数动」。
    static_deformers: Sequence[str] = ()

    def validate(self) -> None:
        if self.deformer_count:
            raise UnsupportedRig(
                f"变形器数量与规格不符（{self.deformer_count} 个未给出规格），"
                f"编译结果会丢失绑定信息")
        if not self.meshes:
            raise UnsupportedRig("至少需要一个网格")
        param_by_id = {p.parameter_id: p for p in self.parameters}
        part_ids = {m.effective_part_id for m in self.meshes}
        deformer_ids = {d.deformer_id for d in self.deformers}
        seen = set()
        for mesh in self.meshes:
            if mesh.mesh_id in seen:
                raise UnsupportedRig(f"网格 id 重复: {mesh.mesh_id}")
            seen.add(mesh.mesh_id)
            _validate_mesh(mesh)
            if mesh.keyform_shapes:
                _validate_keyforms(mesh, param_by_id)
            if mesh.deformer_id and mesh.deformer_id not in deformer_ids:
                raise UnsupportedRig(
                    f"{mesh.mesh_id}: 引用了不存在的变形器 {mesh.deformer_id}")
        self._validate_deformers(param_by_id, part_ids)
        _validate_parameters(self.parameters)

    def _validate_deformers(self, param_by_id: Dict[str, ParameterSpec],
                            part_ids: set) -> None:
        seen: set = set()
        ids = []
        for deformer in self.deformers:
            if deformer.deformer_id in seen:
                raise UnsupportedRig(f"变形器 id 重复: {deformer.deformer_id}")
            seen.add(deformer.deformer_id)
            ids.append(deformer.deformer_id)
        for deformer in self.deformers:
            if isinstance(deformer, WarpDeformerSpec):
                self._validate_warp(deformer)
            else:
                self._validate_rotation(deformer)
            parent_part = deformer.parent_part_id or (
                self.meshes[0].effective_part_id if self.meshes else "")
            if parent_part not in part_ids:
                raise UnsupportedRig(
                    f"{deformer.deformer_id}: 父部件 {parent_part} 不存在")
            if deformer.parent_deformer_id:
                if deformer.parent_deformer_id not in ids:
                    raise UnsupportedRig(
                        f"{deformer.deformer_id}: 父变形器 "
                        f"{deformer.parent_deformer_id} 不存在")
                if deformer.parent_deformer_id == deformer.deformer_id:
                    raise UnsupportedRig(
                        f"{deformer.deformer_id}: 父变形器不能是自身")
            if len(deformer.key_values) > 1:
                if not deformer.parameter_id:
                    raise UnsupportedRig(
                        f"{deformer.deformer_id}: 多个键形必须给出驱动参数")
                _validate_deformer_keys(deformer, param_by_id)

    @staticmethod
    def _validate_warp(deformer: WarpDeformerSpec) -> None:
        if deformer.rows < 1 or deformer.cols < 1:
            raise UnsupportedRig(f"{deformer.deformer_id}: rows/cols 必须 >= 1")
        if not deformer.grids:
            raise UnsupportedRig(f"{deformer.deformer_id}: 至少要一个控制网格")
        for grid in deformer.grids:
            if len(grid.points) != deformer.vertex_count:
                raise UnsupportedRig(
                    f"{deformer.deformer_id}: 网格点数 {len(grid.points)} "
                    f"!= (rows+1)x(cols+1) = {deformer.vertex_count}")
            for x, y in grid.points:
                if not (math.isfinite(x) and math.isfinite(y)):
                    raise UnsupportedRig(
                        f"{deformer.deformer_id}: 控制点坐标非有限值")

    @staticmethod
    def _validate_rotation(deformer: RotationDeformerSpec) -> None:
        if not deformer.keyforms:
            raise UnsupportedRig(
                f"{deformer.deformer_id}: 至少要一个键形，否则画面没有可插值姿态")
        for keyform in deformer.keyforms:
            ox, oy = keyform.origin
            if not all(math.isfinite(v) for v in
                       (keyform.angle, keyform.scale, ox, oy,
                        keyform.key_value)):
                raise UnsupportedRig(
                    f"{deformer.deformer_id}: 键形字段必须为有限值")


def _validate_deformer_keys(deformer: WarpDeformerSpec,
                            param_by_id: Dict[str, ParameterSpec]) -> None:
    param = param_by_id.get(deformer.parameter_id)
    if param is None:
        raise UnsupportedRig(
            f"{deformer.deformer_id}: 参数 {deformer.parameter_id} 未声明")
    values = deformer.key_values
    if not all(a < b for a, b in zip(values, values[1:])):
        raise UnsupportedRig(
            f"{deformer.deformer_id}: 键值必须严格递增（{list(values)}）")
    for v in values:
        if not param.minimum <= v <= param.maximum:
            raise UnsupportedRig(
                f"{deformer.deformer_id}: 键值 {v} 超出参数 "
                f"{param.parameter_id} 的区间")


def _validate_mesh(mesh: MeshSpec) -> None:
    count = len(mesh.vertices)
    if count < 3:
        raise UnsupportedRig(f"{mesh.mesh_id}: 顶点数 {count} 少于 3")
    if count > _MAX_MESH_VERTICES:
        raise UnsupportedRig(
            f"{mesh.mesh_id}: 顶点数 {count} 超过有符号 16 位索引上限 "
            f"{_MAX_MESH_VERTICES}")
    if len(mesh.uvs) != count:
        raise UnsupportedRig(
            f"{mesh.mesh_id}: UV 数 {len(mesh.uvs)} 与顶点数 {count} 不符")
    for x, y in mesh.vertices:
        if not (math.isfinite(x) and math.isfinite(y)):
            raise UnsupportedRig(f"{mesh.mesh_id}: 顶点坐标非有限值")
    for u, v in mesh.uvs:
        if not (math.isfinite(u) and math.isfinite(v)):
            raise UnsupportedRig(f"{mesh.mesh_id}: UV 坐标非有限值")
    for tri in mesh.triangles:
        if len(tri) != 3:
            raise UnsupportedRig(f"{mesh.mesh_id}: 三角形不是三元组")
        for index in tri:
            if not 0 <= index < count:
                raise UnsupportedRig(
                    f"{mesh.mesh_id}: 三角形索引 {index} 越界（顶点数 {count}）")


def _validate_parameters(parameters: Sequence[ParameterSpec]) -> None:
    seen = set()
    for param in parameters:
        if param.parameter_id in seen:
            raise UnsupportedRig(f"参数 id 重复: {param.parameter_id}")
        seen.add(param.parameter_id)
        if not param.minimum < param.maximum:
            raise UnsupportedRig(
                f"{param.parameter_id}: min 必须小于 max")
        if not param.minimum <= param.default <= param.maximum:
            raise UnsupportedRig(
                f"{param.parameter_id}: default 超出 min/max 区间")


def _mesh_axes(mesh: MeshSpec) -> tuple:
    """网格的驱动轴：``((参数 id, 键值元组), ...)``，**第一个轴步长为 1**。

    多轴必须显式给出 ``keyform_axes``（轴序由实测定论钉住，不猜）；单轴沿用
    ``keyform_parameter_id``，键值取自逐键形状。
    """
    if mesh.keyform_axes:
        return tuple((str(pid), tuple(float(v) for v in keys))
                     for pid, keys in mesh.keyform_axes)
    return ((mesh.keyform_parameter_id,
             tuple(float(k.key_value) for k in mesh.keyform_shapes)),)


def keyform_grid_size(axes) -> int:
    """展平后的关键形数 = 各轴键数之积。"""
    size = 1
    for _, keys in axes:
        size *= len(keys)
    return size


def _validate_keyforms(mesh: MeshSpec,
                       param_by_id: Dict[str, ParameterSpec]) -> None:
    axes = _mesh_axes(mesh)
    # 先查「至少两个键形」——顺序与文案都保持与旧行为一致。
    if len(mesh.keyform_shapes) < 2:
        raise UnsupportedRig(
            f"{mesh.mesh_id}: 参数形变至少需要 2 个键形（当前 "
            f"{len(mesh.keyform_shapes)}），无参数形变请走静态路径")
    for pid, keys in axes:
        if not pid:
            raise UnsupportedRig(
                f"{mesh.mesh_id}: 给了 keyform_shapes 但缺少 keyform_parameter_id")
        param = param_by_id.get(pid)
        if param is None:
            raise UnsupportedRig(
                f"{mesh.mesh_id}: 参数 {pid} 未在 RigSpec.parameters 声明")
        if len(keys) < 2:
            raise UnsupportedRig(
                f"{mesh.mesh_id}: 参数 {pid} 至少需要 2 个键值")
        if any(not math.isfinite(v) for v in keys):
            raise UnsupportedRig(f"{mesh.mesh_id}: 参数 {pid} 的键值非有限值")
        if not all(a < b for a, b in zip(keys, keys[1:])):
            raise UnsupportedRig(
                f"{mesh.mesh_id}: 参数 {pid} 的键值必须严格递增（{list(keys)}）")
        for v in keys:
            if not param.minimum <= v <= param.maximum:
                raise UnsupportedRig(
                    f"{mesh.mesh_id}: 键值 {v} 超出参数 {pid} 的 "
                    f"[{param.minimum}, {param.maximum}] 区间")
    expected = keyform_grid_size(axes)
    if len(mesh.keyform_shapes) != expected:
        raise UnsupportedRig(
            f"{mesh.mesh_id}: 键形数 {len(mesh.keyform_shapes)} 与各轴键数之积 "
            f"{expected} 不符（展平序 = 第一个轴步长为 1）")
    vertex_count = len(mesh.vertices)
    for shape in mesh.keyform_shapes:
        if len(shape.vertices) != vertex_count:
            raise UnsupportedRig(
                f"{mesh.mesh_id}: 键值为 {shape.key_value} 的键形顶点数 "
                f"{len(shape.vertices)} 与网格顶点数 {vertex_count} 不符")
        for x, y in shape.vertices:
            if not (math.isfinite(x) and math.isfinite(y)):
                raise UnsupportedRig(f"{mesh.mesh_id}: 键形顶点坐标非有限值")


def keyform_groups(spec: RigSpec) -> tuple:
    """返回 ``(bindings, bands)``。

    * ``binding`` = ``(参数 id, 键值元组)``，**按参数声明顺序去重排列** —— 这样同名
      参数名下的 binding 编号连续，``parameter.keyform_binding_begin/counts`` 才是
      合法区间（同一参数可有多个 binding，不同键布局）。
    * ``band`` = ``binding`` 的有序元组；**带内顺序即 keyform 展平轴序**，第一个
      binding 步长为 1（实测定论：F-06 / tools/probe_multiband_axis_order.py）。
      单参数带就是只含一个 binding 的带。

    来源两类：带逐键形状的网格，以及带多个控制网格的变形器。
    """
    order = [p.parameter_id for p in spec.parameters]
    position = {pid: i for i, pid in enumerate(order)}
    bindings: List[tuple] = []
    seen: Dict[tuple, int] = {}

    def register(pid: str, values: tuple) -> tuple:
        key = (pid, tuple(values))
        if key not in seen:
            seen[key] = len(bindings)
            bindings.append(key)
        return key

    bands: List[tuple] = []
    for mesh in spec.meshes:
        if mesh.keyform_shapes:
            bands.append(tuple(register(pid, values)
                               for pid, values in _mesh_axes(mesh)))
    for deformer in spec.deformers:
        if len(deformer.key_values) > 1:
            bands.append((register(deformer.parameter_id, deformer.key_values),))

    bindings.sort(key=lambda b: position.get(b[0], len(order)))
    # 同一个键值布局（含多轴组合）只占一个带：多个网格/变形器共用同一带。
    unique: List[tuple] = []
    seen_bands: set = set()
    for band in bands:
        if band not in seen_bands:
            seen_bands.add(band)
            unique.append(band)
    return bindings, unique


def compile_static_rig(spec: RigSpec) -> Moc3Container:
    """编译模型：静态几何 + 可选的参数形变键型。"""
    spec.validate()
    doc = Moc3Container(version=ms.MocVersion.V3_03)
    doc.canvas = CanvasInfo(
        pixels_per_unit=spec.pixels_per_unit,
        origin_x=spec.canvas_width / 2.0,
        origin_y=spec.canvas_height / 2.0,
        canvas_width=spec.canvas_width,
        canvas_height=spec.canvas_height,
    )

    meshes = list(spec.meshes)
    params = list(spec.parameters)
    count = len(meshes)
    # warp 与 rotation 是两张独立的子表，各自的数组只列本类型的条目
    warps = [d for d in spec.deformers if isinstance(d, WarpDeformerSpec)]
    rotations = [d for d in spec.deformers
                 if isinstance(d, RotationDeformerSpec)]

    # bindings = 去重的 (参数 id, 键值元组)；bands = 每个被驱动元素一个带，
    # 带内可含多个 binding（多参数带），其顺序即 keyform 展平轴序。
    bindings, bands = keyform_groups(spec)
    band_of_group = {band: 1 + j for j, band in enumerate(bands)}

    uv: List[float] = []
    indices: List[int] = []
    positions: List[float] = []
    uv_begin: List[int] = []
    index_begin: List[int] = []
    vertex_count: List[int] = []
    index_count: List[int] = []
    mesh_keyform_counts: List[int] = []
    mesh_keyform_begin: List[int] = []
    keyform_pos_begin: List[int] = []
    keyform_opacities: List[float] = []
    keyform_draw_orders: List[float] = []
    param_by_id = {p.parameter_id: p for p in params}
    # 挂在 warp 下的网格：顶点写成控制网格内的 (s, t)，而不是模型空间
    # 只有 warp 会改变成员网格的顶点空间（(s,t)）；rotation 下顶点照旧是模型空间
    mesh_rects = {d.deformer_id: reference_rect(d, param_by_id)
                  for d in warps}

    def pool_vertices(mesh: MeshSpec):
        rect = mesh_rects.get(mesh.deformer_id)
        if rect is None:
            return lambda verts: verts
        return lambda verts: to_grid_st(verts, rect)

    def append_keyform_positions(shape_vertices, in_units=True) -> int:
        # 官方导出器把每个关键形的顶点位置起点对齐到 16 个 f32
        pad = -len(positions) % _KEYFORM_POS_ALIGN
        positions.extend([0.0] * pad)
        begin = len(positions)
        unit = 1.0 / spec.pixels_per_unit if in_units else 1.0
        for x, y in shape_vertices:
            # moc3 里存的是**单位坐标**（= 画布像素 / pixels_per_unit，相对原点）。
            # 实测依据见 tools/probe_ppu_convention.py：存画布像素会让模型被放大约
            # ppu 倍而整体移出画面，真实导出在官方运行时里一个像素都画不出来。
            # 例外：挂在 warp 下的网格存 (s, t)，它本身无量纲，不再除以 ppu。
            positions.append(float(x) * unit)
            positions.append(float(y) * unit)
        return begin

    for mesh in meshes:
        to_pool = pool_vertices(mesh)
        # 只有 warp 子网格的顶点被换成无量纲的 (s,t)，不能再除 ppu；
        # rotation 子网格仍是模型像素坐标，照旧换算。
        in_units = mesh.deformer_id not in mesh_rects
        uv_begin.append(len(uv))
        index_begin.append(len(indices))
        for u, v in mesh.uvs:
            uv.append(float(u))
            uv.append(float(v))
        for tri in mesh.triangles:
            indices.extend(int(i) for i in tri)
        vertex_count.append(len(mesh.triangles) * 3)   # 索引条目数
        index_count.append(len(mesh.vertices))         # 唯一顶点数

        mesh_keyform_begin.append(len(keyform_pos_begin))
        if mesh.keyform_shapes:
            mesh_keyform_counts.append(len(mesh.keyform_shapes))
            for shape in mesh.keyform_shapes:
                keyform_pos_begin.append(
                    append_keyform_positions(to_pool(shape.vertices), in_units))
                keyform_opacities.append(
                    mesh.opacity if shape.opacity is None else float(shape.opacity))
                keyform_draw_orders.append(
                    mesh.draw_order if shape.draw_order is None
                    else float(shape.draw_order))
        else:
            mesh_keyform_counts.append(1)
            keyform_pos_begin.append(
                append_keyform_positions(to_pool(mesh.vertices), in_units))
            keyform_opacities.append(float(mesh.opacity))
            keyform_draw_orders.append(float(mesh.draw_order))

    total_keyforms = len(keyform_pos_begin)

    # warp 变形器的控制网格与网格关键形共用同一个位置池（同样 16 f32 对齐）。
    # rotation 变形器没有位置池：它的键形只有角度/原点/缩放，另表存放。
    warp_keyform_begin: List[int] = []
    warp_keyform_pos_begin: List[int] = []
    warp_keyform_opacities: List[float] = []
    running = 0
    for deformer in warps:
        warp_keyform_begin.append(running)
        for grid in deformer.grids:
            warp_keyform_pos_begin.append(append_keyform_positions(grid.points))
            warp_keyform_opacities.append(float(deformer.opacity))
        running += len(deformer.grids)
    total_warp_keyforms = running

    _set_counts(doc, count, params, uv, indices, positions,
                total_keyforms, bindings, bands, len(spec.deformers),
                total_warp_keyforms, len(rotations),
                sum(len(d.keyforms) for d in rotations))
    _write_parts(doc, meshes)
    _write_deformers(doc, spec, warps, rotations, band_of_group,
                     warp_keyform_begin, warp_keyform_pos_begin,
                     warp_keyform_opacities)
    _write_meshes(doc, meshes, vertex_count, index_count, uv_begin, index_begin,
                  mesh_keyform_counts, mesh_keyform_begin, band_of_group,
                  spec)
    _write_geometry(doc, uv, indices, positions,
                    keyform_pos_begin, keyform_opacities, keyform_draw_orders)
    _write_bands(doc, bindings, bands)
    _write_parameters(doc, params, bindings)
    _write_draw_order(doc, count)
    return doc


def _deformer_group(deformer) -> tuple:
    return ((deformer.parameter_id, deformer.key_values),)


def reference_rect(deformer: WarpDeformerSpec,
                   param_by_id: Dict[str, ParameterSpec]) -> tuple:
    """变形器「静止形」控制网格的轴对齐范围 (x0, y0, x1, y1)。

    内核把挂在 warp 下的网格顶点当作该网格内的 (s, t) 归一化坐标，再用控制网格
    映回模型空间（对官方 Haru 实测：warp 下 80 个网格的顶点全在 ~[0,1]，而
    rotation 下的 4 个在像素模型空间）。静止形 = 参数默认值对应的那一形。
    """
    if len(deformer.grids) == 1:
        grid = deformer.grids[0]
    else:
        param = param_by_id.get(deformer.parameter_id)
        if param is None:
            raise UnsupportedRig(
                f"{deformer.deformer_id}: 参数 {deformer.parameter_id} 未声明")
        matched = [g for g in deformer.grids
                   if float(g.key_value) == float(param.default)]
        if len(matched) != 1:
            raise UnsupportedRig(
                f"{deformer.deformer_id}: 需要恰好一个键值等于参数默认值 "
                f"{param.default} 的控制网格作为静止形（当前 {len(matched)} 个）")
        grid = matched[0]
    xs = [float(p[0]) for p in grid.points]
    ys = [float(p[1]) for p in grid.points]
    if max(xs) <= min(xs) or max(ys) <= min(ys):
        raise UnsupportedRig(
            f"{deformer.deformer_id}: 控制网格退化（宽或高为 0），"
            f"无法把顶点换算成 (s, t)")
    _require_axis_lattice(deformer, xs, ys)
    return min(xs), min(ys), max(xs), max(ys)


def _require_axis_lattice(deformer: WarpDeformerSpec,
                          xs: Sequence[float], ys: Sequence[float]) -> None:
    """静止形必须是轴对齐规则点阵，否则线性 (s, t) 换算不成立。"""
    tol = 1e-4 * max(max(xs) - min(xs), max(ys) - min(ys))
    uniq_x = sorted({round(x, 9) for x in xs})
    uniq_y = sorted({round(y, 9) for y in ys})
    kept_x = [x for i, x in enumerate(uniq_x)
              if i == 0 or x - uniq_x[i - 1] > tol]
    kept_y = [y for i, y in enumerate(uniq_y)
              if i == 0 or y - uniq_y[i - 1] > tol]
    if len(kept_x) != deformer.cols + 1 or len(kept_y) != deformer.rows + 1:
        raise UnsupportedRig(
            f"{deformer.deformer_id}: 静止形控制网格不是轴对齐规则点阵"
            f"（x 有 {len(kept_x)} 列、y 有 {len(kept_y)} 行，应为 "
            f"{deformer.cols + 1}x{deformer.rows + 1}），"
            f"内核的 (s, t) 映射无法用线性换算复现")


def to_grid_st(vertices: Sequence[Sequence[float]], rect: tuple) -> List[tuple]:
    """模型空间顶点 -> 参考网格内的 (s, t)。"""
    x0, y0, x1, y1 = rect
    sx, sy = x1 - x0, y1 - y0
    return [((float(x) - x0) / sx, (float(y) - y0) / sy) for x, y in vertices]


def _pivot_to_offset(origin, unit: float, total_angle: float) -> tuple:
    """把「想绕的枢轴」换算成内核要的平移量 (I − R)·p（含 ÷ppu）。"""
    theta = math.radians(float(total_angle))
    cos, sin = math.cos(theta), math.sin(theta)
    px, py = float(origin[0]) * unit, float(origin[1]) * unit
    return (px - (px * cos - py * sin), py - (px * sin + py * cos))


def _write_deformers(doc: Moc3Container, spec: RigSpec,
                     warps: List[WarpDeformerSpec],
                     rotations: List[RotationDeformerSpec],
                     band_of_group: Dict[tuple, int],
                     warp_keyform_begin: List[int],
                     warp_keyform_pos_begin: List[int],
                     warp_keyform_opacities: List[float]) -> None:
    """写公共 deformer 表 + warp / rotation 两张子表（官方 Haru 实测的引用关系）。

    types: 0 = warp，1 = rotation；`specific_indices` 是**各自类型内**的稠密下标
    （官方 Haru 97 个变形器：warp 的 specific 是 0..65，rotation 的是 0..30）。
    子表按类型分别成数组，所以必须只列本类型的条目。
    """
    deformers = list(spec.deformers)
    if not deformers:
        return
    n = len(deformers)
    ids = [d.deformer_id for d in deformers]
    first_part = spec.meshes[0].effective_part_id if spec.meshes else ""
    warp_slot = {d.deformer_id: i for i, d in enumerate(warps)}
    rotation_slot = {d.deformer_id: i for i, d in enumerate(rotations)}
    doc.set("deformer.ids", ids)
    doc.set("deformer.keyform_binding_band_indices", [
        band_of_group[_deformer_group(d)] if len(d.key_values) > 1 else 0
        for d in deformers
    ])
    doc.set("deformer.visibles", [1] * n)
    doc.set("deformer.enables", [1] * n)
    doc.set("deformer.parent_part_indices", [
        _index_of(spec, d.parent_part_id or first_part) for d in deformers])
    doc.set("deformer.parent_deformer_indices", [
        ids.index(d.parent_deformer_id) if d.parent_deformer_id else -1
        for d in deformers
    ])
    doc.set("deformer.types", [d.deformer_type for d in deformers])
    doc.set("deformer.specific_indices", [
        warp_slot[d.deformer_id] if d.deformer_type == 0
        else rotation_slot[d.deformer_id] for d in deformers])

    if warps:
        doc.set("warp_deformer.keyform_binding_band_indices",
                [band_of_group[_deformer_group(d)] if len(d.key_values) > 1 else 0
                 for d in warps])
        doc.set("warp_deformer.keyform_begin_indices", warp_keyform_begin)
        doc.set("warp_deformer.keyform_counts", [len(d.grids) for d in warps])
        doc.set("warp_deformer.vertex_counts", [d.vertex_count for d in warps])
        doc.set("warp_deformer.rows", [d.rows for d in warps])
        doc.set("warp_deformer.cols", [d.cols for d in warps])
        doc.set("warp_deformer_keyform.opacities", warp_keyform_opacities)
        doc.set("warp_deformer_keyform.keyform_position_begin_indices",
                warp_keyform_pos_begin)

    if rotations:
        unit = 1.0 / spec.pixels_per_unit
        begin = 0
        begins: List[int] = []
        angles: List[float] = []
        origin_xs: List[float] = []
        origin_ys: List[float] = []
        scales: List[float] = []
        reflect_xs: List[int] = []
        reflect_ys: List[int] = []
        opacities: List[float] = []
        for deformer in rotations:
            begins.append(begin)
            for keyform in deformer.keyforms:
                angles.append(float(keyform.angle))
                # 实测（tools/probe_rotation_offset.py）内核做的是
                #   p' = R(总角度)·p + origin
                # —— 这个字段是**平移量**，不是枢轴（0°/30°/60°/−45° 四个角度、
                # 八个候选里只有「按原样平移」逐像素命中）。所以要绕模型空间的
                # 枢轴 p，必须写 (I − R)·p。
                # 早前在 60° 上「写 R(−A)·p 恰好命中」是巧合：
                # I − R(A) = R(−A) 只在 cos A = 1/2 时成立 —— 这同时解释了
                # 30°/120° 为何全部不命中。
                offset = _pivot_to_offset(
                    keyform.origin, unit,
                    float(deformer.base_angle) + float(keyform.angle))
                origin_xs.append(offset[0])
                origin_ys.append(offset[1])
                scales.append(float(keyform.scale))
                reflect_xs.append(int(keyform.reflect_x))
                reflect_ys.append(int(keyform.reflect_y))
                opacities.append(float(keyform.opacity))
            begin += len(deformer.keyforms)
        doc.set("rotation_deformer.keyform_binding_band_indices",
                [band_of_group[_deformer_group(d)] if len(d.key_values) > 1 else 0
                 for d in rotations])
        doc.set("rotation_deformer.keyform_begin_indices", begins)
        doc.set("rotation_deformer.keyform_counts",
                [len(d.keyforms) for d in rotations])
        doc.set("rotation_deformer.base_angles",
                [float(d.base_angle) for d in rotations])
        doc.set("rotation_deformer_keyform.angles", angles)
        doc.set("rotation_deformer_keyform.origin_xs", origin_xs)
        doc.set("rotation_deformer_keyform.origin_ys", origin_ys)
        doc.set("rotation_deformer_keyform.scales", scales)
        doc.set("rotation_deformer_keyform.reflect_xs", reflect_xs)
        doc.set("rotation_deformer_keyform.reflect_ys", reflect_ys)
        doc.set("rotation_deformer_keyform.opacities", opacities)

    # V3_03 起的 additional 段：每个变形器一个 quad-transform 标志，0 = 网格形变。
    # 实测：deformer 表非空而该段为空时，内核判 "Header section is invalid"
    # （tools/probe_deformer_header_variants.py、tools/probe_additional_rule.py）。
    doc.set("additional.quad_transforms", [0] * n)


def _index_of(spec: RigSpec, part_id: str) -> int:
    parts = [m.effective_part_id for m in spec.meshes]
    return parts.index(part_id) if part_id in parts else 0


def _group_of(mesh: MeshSpec) -> tuple:
    return _mesh_axes(mesh)


def _set_counts(doc: Moc3Container, count: int, params: List[ParameterSpec],
                uv: List[float], indices: List[int], positions: List[float],
                total_keyforms: int, bindings: List[tuple],
                bands: List[tuple],
                n_deformers: int = 0,
                total_warp_keyforms: int = 0,
                n_rotations: int = 0,
                total_rotation_keyforms: int = 0) -> None:
    c = doc.counts
    c[ms.CountIdx.PARTS] = count
    c[ms.CountIdx.ART_MESHES] = count
    c[ms.CountIdx.PART_KEYFORMS] = count
    c[ms.CountIdx.ART_MESH_KEYFORMS] = total_keyforms
    c[ms.CountIdx.DEFORMERS] = n_deformers
    c[ms.CountIdx.WARP_DEFORMERS] = n_deformers - n_rotations
    c[ms.CountIdx.ROTATION_DEFORMERS] = n_rotations
    c[ms.CountIdx.WARP_DEFORMER_KEYFORMS] = total_warp_keyforms
    c[ms.CountIdx.ROTATION_DEFORMER_KEYFORMS] = total_rotation_keyforms
    c[ms.CountIdx.KEYFORM_POSITIONS] = len(positions)
    c[ms.CountIdx.UVS] = len(uv)
    c[ms.CountIdx.POSITION_INDICES] = len(indices)
    c[ms.CountIdx.PARAMETERS] = len(params)
    # band 0 是空带；此后每个 band 一个带（可引用多个 binding = 多参数带）
    c[ms.CountIdx.KEYFORM_BINDING_BANDS] = 1 + len(bands)
    c[ms.CountIdx.KEYFORM_BINDING_INDICES] = sum(len(b) for b in bands)
    c[ms.CountIdx.KEYFORM_BINDINGS] = len(bindings)
    c[ms.CountIdx.KEYS] = sum(len(values) for _, values in bindings)
    c[ms.CountIdx.DRAW_ORDER_GROUPS] = 1
    c[ms.CountIdx.DRAW_ORDER_GROUP_OBJECTS] = count


def _write_parts(doc: Moc3Container, meshes: List[MeshSpec]) -> None:
    count = len(meshes)
    doc.set("part.ids", [m.effective_part_id for m in meshes])
    doc.set("part.keyform_binding_band_indices", [0] * count)
    doc.set("part.keyform_begin_indices", list(range(count)))
    doc.set("part.keyform_counts", [1] * count)
    doc.set("part.visibles", [1] * count)
    doc.set("part.enables", [1] * count)
    doc.set("part.parent_part_indices", [-1] * count)
    doc.set("part_keyform.draw_orders", [float(m.draw_order) for m in meshes])


def _write_meshes(doc: Moc3Container, meshes: List[MeshSpec],
                  vertex_count: List[int], index_count: List[int],
                  uv_begin: List[int], index_begin: List[int],
                  mesh_keyform_counts: List[int],
                  mesh_keyform_begin: List[int],
                  band_of_group: Dict[tuple, int],
                  spec: Optional[RigSpec] = None) -> None:
    count = len(meshes)
    deformer_ids = [d.deformer_id for d in (spec.deformers if spec else ())]
    doc.set("art_mesh.ids", [m.mesh_id for m in meshes])
    doc.set("art_mesh.keyform_binding_band_indices", [
        band_of_group[_group_of(m)] if m.keyform_shapes else 0 for m in meshes
    ])
    doc.set("art_mesh.keyform_begin_indices", mesh_keyform_begin)
    doc.set("art_mesh.keyform_counts", mesh_keyform_counts)
    doc.set("art_mesh.visibles", [1] * count)
    doc.set("art_mesh.enables", [1] * count)
    doc.set("art_mesh.parent_part_indices", list(range(count)))
    doc.set("art_mesh.parent_deformer_indices", [
        deformer_ids.index(m.deformer_id) if m.deformer_id in deformer_ids else -1
        for m in meshes
    ])
    doc.set("art_mesh.texture_indices", [m.texture_index for m in meshes])
    doc.set("art_mesh.drawable_flags", [0] * count)
    doc.set("art_mesh.position_index_counts", index_count)
    doc.set("art_mesh.vertex_counts", vertex_count)
    doc.set("art_mesh.uv_begin_indices", uv_begin)
    doc.set("art_mesh.position_index_begin_indices", index_begin)
    doc.set("art_mesh.mask_begin_indices", [0] * count)
    doc.set("art_mesh.mask_counts", [0] * count)


def _write_geometry(doc: Moc3Container, uv: List[float], indices: List[int],
                    positions: List[float], keyform_pos_begin: List[int],
                    keyform_opacities: List[float],
                    keyform_draw_orders: List[float]) -> None:
    doc.set("uv.xys", uv)
    doc.set("position_index.indices", indices)
    doc.set("keyform_position.xys", positions)
    doc.set("art_mesh_keyform.opacities", keyform_opacities)
    doc.set("art_mesh_keyform.draw_orders", keyform_draw_orders)
    doc.set("art_mesh_keyform.keyform_position_begin_indices",
            keyform_pos_begin)


def _write_bands(doc: Moc3Container, bindings: List[tuple],
                 bands: List[tuple]) -> None:
    """band 0 为空带；此后每个 band 一个带，**可引用多个 binding**（多参数带）。

    binding 编号 == ``bindings`` 的下标；带内引用顺序
    （``keyform_binding_index.indices``）就是 keyform 的展平轴序。
    """
    index_of = {binding: j for j, binding in enumerate(bindings)}
    begins: List[int] = [0]
    counts: List[int] = [0]
    indices: List[int] = []
    for band in bands:
        begins.append(len(indices))
        counts.append(len(band))
        indices.extend(index_of[b] for b in band)
    doc.set("keyform_binding_band.begin_indices", begins)
    doc.set("keyform_binding_band.counts", counts)
    doc.set("keyform_binding_index.indices", indices)

    keys_begin: List[int] = []
    keys_counts: List[int] = []
    keys_values: List[float] = []
    for _, values in bindings:
        keys_begin.append(len(keys_values))
        keys_counts.append(len(values))
        keys_values.extend(values)
    doc.set("keyform_binding.keys_begin_indices", keys_begin)
    doc.set("keyform_binding.keys_counts", keys_counts)
    doc.set("keys.values", keys_values)


def _write_parameters(doc: Moc3Container, params: List[ParameterSpec],
                      bindings: List[tuple]) -> None:
    if not params:
        return
    count = len(params)
    begins: Dict[str, int] = {}
    counts: Dict[str, int] = {}
    for j, (pid, _) in enumerate(bindings):
        begins.setdefault(pid, j)
        counts[pid] = counts.get(pid, 0) + 1
    doc.set("parameter.ids", [p.parameter_id for p in params])
    doc.set("parameter.min_values", [float(p.minimum) for p in params])
    doc.set("parameter.max_values", [float(p.maximum) for p in params])
    doc.set("parameter.default_values", [float(p.default) for p in params])
    doc.set("parameter.repeats", [0] * count)
    doc.set("parameter.decimal_places", [int(p.decimal_places) for p in params])
    doc.set("parameter.keyform_binding_begin_indices", [
        begins.get(p.parameter_id, 0) for p in params
    ])
    doc.set("parameter.keyform_binding_counts", [
        counts.get(p.parameter_id, 0) for p in params
    ])


def _write_draw_order(doc: Moc3Container, count: int) -> None:
    doc.set("draw_order_group.object_begin_indices", [0])
    doc.set("draw_order_group.object_counts", [count])
    doc.set("draw_order_group.object_total_counts", [count])
    doc.set("draw_order_group.min_draw_orders", [0])
    doc.set("draw_order_group.max_draw_orders", [0])
    doc.set("draw_order_group_object.types", [0] * count)
    doc.set("draw_order_group_object.indices", list(range(count)))
    doc.set("draw_order_group_object.group_indices", [-1] * count)
