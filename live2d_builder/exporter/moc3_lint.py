"""moc3 section 图的自洽性静态校验（纯 Python，无 IO）。

拦截三类问题：
  1. section 长度与 count info 期望不符
  2. begin_index / count 组合越界
  3. 引用型索引悬空

目的：在把数据交给会崩溃的 Cubism Core 之前拦下非法结构。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from live2d_builder.exporter import moc3_sections as ms
from live2d_builder.exporter.moc3_container import Moc3Container


@dataclass(frozen=True)
class LintIssue:
    """一条自洽性问题。"""
    section: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - 便于阅读
        return f"[{self.section}] {self.message}"


# (目标 section, begin 字段, count 字段, 每条记录占多少个目标元素)
#
# 字段语义按官方 Haru 差分核实，与字面直觉相反：
#   art_mesh.position_index_counts = 唯一顶点数 -> uv 段每条记录占 2 个浮点数
#   art_mesh.vertex_counts         = 索引条目数 -> position_index 段每条记录占 1 个索引
_RANGE_RULES = (
    ("uv.xys", "art_mesh.uv_begin_indices",
     "art_mesh.position_index_counts", 2),
    ("position_index.indices", "art_mesh.position_index_begin_indices",
     "art_mesh.vertex_counts", 1),
    ("keys.values",
     "keyform_binding.keys_begin_indices",
     "keyform_binding.keys_counts", 1),
)

# (被引用数组, 持索引的 section)
_REF_RULES = (
    ("part.ids", "art_mesh.parent_part_indices"),
    ("art_mesh.ids", "draw_order_group_object.indices"),
    # keyform_binding_index 里的条目引用的是 binding 编号，不是参数编号：
    # 参数与 binding 的归属由 parameter.keyform_binding_{begin,counts} 划定。
    # 曾误写为按 parameter.ids 计数，在同参数多 binding 时会假报警。
    ("keyform_binding.keys_counts", "keyform_binding_index.indices"),
    ("keyform_binding_band.counts", "part.keyform_binding_band_indices"),
    ("keyform_binding_band.counts", "art_mesh.keyform_binding_band_indices"),
)


def lint_document(doc: Moc3Container) -> List[LintIssue]:
    """返回全部自洽性问题；空列表表示通过。"""
    issues: List[LintIssue] = []
    issues.extend(_check_lengths(doc))
    issues.extend(_check_ranges(doc))
    issues.extend(_check_references(doc))
    issues.extend(_check_binding_chain(doc))
    issues.extend(_check_binding_layout(doc))
    issues.extend(_check_keyform_product(doc))
    issues.extend(_check_deformer_grids(doc))
    issues.extend(_check_deformer_types(doc))
    issues.extend(_check_deformer_bands(doc))
    issues.extend(_check_additional_section(doc))
    return issues


def _check_deformer_types(doc: Moc3Container) -> List[LintIssue]:
    """类型计数与子表下标必须自洽。

    内核把 `specific_indices` 当作**各自类型内**的下标使用：类型数对不上、
    或下标越界，都会让整份文档被判不一致（此前 ladder 实验里
    「有 warp 类型却没有 warp 子表」报的就是 `Data section is invalid`）。
    官方 Haru：97 个变形器 = 66 warp + 31 rotation，无越界。
    """
    issues: List[LintIssue] = []
    types = doc.get("deformer.types")
    if not types:
        return issues
    specific = doc.get("deformer.specific_indices")
    n_warp = sum(1 for t in types if t == 0)
    n_rot = sum(1 for t in types if t == 1)
    counts = doc.counts
    if n_warp != counts[ms.CountIdx.WARP_DEFORMERS]:
        issues.append(LintIssue(
            "deformer.types",
            f"type=0 的条目 {n_warp} 个，但 WARP_DEFORMERS="
            f"{counts[ms.CountIdx.WARP_DEFORMERS]}"))
    if n_rot != counts[ms.CountIdx.ROTATION_DEFORMERS]:
        issues.append(LintIssue(
            "deformer.types",
            f"type=1 的条目 {n_rot} 个，但 ROTATION_DEFORMERS="
            f"{counts[ms.CountIdx.ROTATION_DEFORMERS]}"))
    limits = {0: n_warp, 1: n_rot}
    for i, (t, sp) in enumerate(zip(types, specific)):
        if t not in (0, 1):
            issues.append(LintIssue(
                "deformer.types", f"第 {i} 个变形器类型 {t} 未知"))
            continue
        if not 0 <= sp < limits[t]:
            issues.append(LintIssue(
                "deformer.specific_indices",
                f"第 {i} 个变形器的子表下标 {sp} 越界"
                f"（{'warp' if t == 0 else 'rotation'} 子表长 {limits[t]}）"))
    return issues


def _check_deformer_bands(doc: Moc3Container) -> List[LintIssue]:
    """公共 deformer 表的绑定带必须与其子表一致（官方 Haru 97/97 成立）。"""
    issues: List[LintIssue] = []
    types = doc.get("deformer.types")
    specific = doc.get("deformer.specific_indices")
    common_band = doc.get("deformer.keyform_binding_band_indices")
    if not types:
        return issues
    warp_band = doc.get("warp_deformer.keyform_binding_band_indices")
    rot_band = doc.get("rotation_deformer.keyform_binding_band_indices")
    for i, (t, sp) in enumerate(zip(types, specific)):
        sub = warp_band[sp] if t == 0 else rot_band[sp]
        if sub != common_band[i]:
            issues.append(LintIssue(
                "deformer.keyform_binding_band_indices",
                f"第 {i} 个变形器公共表带 {common_band[i]} 与"
                f"{'warp' if t == 0 else 'rotation'}子表带 {sub} 不一致"))
    return issues


def _check_additional_section(doc: Moc3Container) -> List[LintIssue]:
    """V3_03 起，deformer 表非空时 additional 段必须有内容。

    实测（tools/probe_deformer_header_variants.py）：该段为空时内核报的是
    "Header section is invalid" —— 完全不提变形器，极易误诊。
    """
    issues: List[LintIssue] = []
    if doc.version < ms.MocVersion.V3_03:
        return issues
    n_deformers = doc.counts[ms.CountIdx.DEFORMERS]
    extra = doc.get("additional.quad_transforms")
    if n_deformers and not extra:
        issues.append(LintIssue(
            "additional.quad_transforms",
            f"deformer 表有 {n_deformers} 项但该段为空，"
            f"官方内核会判 Header section is invalid"))
    elif extra and len(extra) != n_deformers:
        issues.append(LintIssue(
            "additional.quad_transforms",
            f"该段有 {len(extra)} 项，与变形器数 {n_deformers} 不符"))
    return issues


def _check_lengths(doc: Moc3Container) -> List[LintIssue]:
    """每个 section 的实际长度必须等于 count info 的期望值。

    runtime 类 section 由运行期填充，不参与校验。
    """
    issues: List[LintIssue] = []
    for entry in ms.build_layout(doc.version):
        if entry.count_idx < 0 or entry.elem_type == "runtime":
            continue
        expected = doc.counts[entry.count_idx]
        actual = len(doc.get(entry.name))
        if actual != expected:
            issues.append(LintIssue(
                entry.name,
                f"长度 {actual} 与 count info 期望 {expected} 不符",
            ))
    return issues


def _check_ranges(doc: Moc3Container) -> List[LintIssue]:
    issues: List[LintIssue] = []
    for target, begin_field, count_field, scale in _RANGE_RULES:
        begins = doc.get(begin_field)
        if not begins:
            continue
        counts = doc.get(count_field)
        limit = len(doc.get(target))
        for i, begin in enumerate(begins):
            span = scale * counts[i] if i < len(counts) else 0
            if begin < 0 or begin > limit or begin + span > limit:
                issues.append(LintIssue(
                    begin_field,
                    f"第 {i} 项 begin={begin} span={span} 越界（目标长度 {limit}）",
                ))
    return issues


def _check_references(doc: Moc3Container) -> List[LintIssue]:
    issues: List[LintIssue] = []
    for ref_name, holder in _REF_RULES:
        values = doc.get(holder)
        if not values:
            continue
        limit = len(doc.get(ref_name))
        for i, value in enumerate(values):
            if value >= limit:
                issues.append(LintIssue(
                    holder,
                    f"第 {i} 项引用 {value} 越界（{ref_name} 长度 {limit}）",
                ))
    return issues


def _check_binding_chain(doc: Moc3Container) -> List[LintIssue]:
    """参数可以只声明不绑定（实测官方接受），但一旦存在绑定就必须链完整。"""
    issues: List[LintIssue] = []
    params = doc.counts[ms.CountIdx.PARAMETERS]
    bindings = doc.counts[ms.CountIdx.KEYFORM_BINDINGS]
    if bindings > 0 and params == 0:
        issues.append(LintIssue(
            "keyform_binding", "存在绑定但没有任何参数，绑定链悬空"))
    if params > 0 and bindings > 0:
        if not doc.get("keys.values"):
            issues.append(LintIssue(
                "keys.values", "存在绑定但没有任何键值，绑定链不完整"))
        if not doc.get("keyform_binding.keys_counts"):
            issues.append(LintIssue(
                "keyform_binding.keys_counts", "存在绑定但缺少键计数"))
    if params > 0 and not doc.get("parameter.keyform_binding_counts"):
        issues.append(LintIssue(
            "parameter.keyform_binding_counts", "参数缺少绑定数量声明"))
    if doc.counts[ms.CountIdx.ART_MESHES] > 0:
        if not doc.get("art_mesh_keyform.opacities"):
            issues.append(LintIssue(
                "art_mesh_keyform.opacities", "存在网格但缺少网格关键形"))
    if doc.counts[ms.CountIdx.PARTS] > 0:
        if not doc.get("part_keyform.draw_orders"):
            issues.append(LintIssue(
                "part_keyform.draw_orders", "存在部件但缺少部件关键形"))
    issues.extend(_check_chain_tiling(doc))
    return issues


def _check_chain_tiling(doc: Moc3Container) -> List[LintIssue]:
    """绑定链各层的总数必须严丝合缝（对官方 Haru 实测得到的关系）。

    对象.band_index -> band{begin,count} -> binding_index
      -> binding{keys_begin,keys_count} -> keys.values
    """
    issues: List[LintIssue] = []

    def total(field):
        return sum(doc.get(field))

    bands = doc.get("keyform_binding_band.counts")
    binding_index = doc.get("keyform_binding_index.indices")
    if total("keyform_binding_band.counts") != len(binding_index):
        issues.append(LintIssue(
            "keyform_binding_band.counts",
            f"带内绑定索引总数 {total('keyform_binding_band.counts')} "
            f"!= keyform_binding_index 长度 {len(binding_index)}"))

    bindings = doc.get("keyform_binding.keys_counts")
    if total("parameter.keyform_binding_counts") != len(bindings):
        issues.append(LintIssue(
            "parameter.keyform_binding_counts",
            "参数声明的绑定总数与 keyform_binding 条目数不符"))

    if total("keyform_binding.keys_counts") != len(doc.get("keys.values")):
        issues.append(LintIssue(
            "keyform_binding.keys_counts",
            "键总数与 keys.values 长度不符"))

    # 带区间必须落在 binding_index 之内
    for i, (begin, count) in enumerate(zip(
            doc.get("keyform_binding_band.begin_indices"), bands)):
        if begin < 0 or begin + count > len(binding_index):
            issues.append(LintIssue(
                "keyform_binding_band.begin_indices",
                f"第 {i} 带 begin={begin} count={count} 越界"
                f"（binding_index 长度 {len(binding_index)}）"))

    # 实测：对象引用不存在的带（含 -1）会被官方内核判为 Data section invalid
    n_bands = len(bands)
    for holder in ("part.keyform_binding_band_indices",
                   "art_mesh.keyform_binding_band_indices",
                   "deformer.keyform_binding_band_indices"):
        for i, value in enumerate(doc.get(holder)):
            if value < 0 or value >= n_bands:
                issues.append(LintIssue(
                    holder,
                    f"第 {i} 项引用带 {value}，但带索引必须在 [0, {n_bands}) 内"
                    f"（官方不使用 -1）"))
    return issues


# (group, keyform 计数字段, keyform 起始字段, 带索引字段, 总数的 count 槽位)
_KEYFORM_HOLDERS = (
    ("part", "part.keyform_counts", "part.keyform_begin_indices",
     "part.keyform_binding_band_indices", ms.CountIdx.PART_KEYFORMS),
    ("art_mesh", "art_mesh.keyform_counts", "art_mesh.keyform_begin_indices",
     "art_mesh.keyform_binding_band_indices", ms.CountIdx.ART_MESH_KEYFORMS),
    # 变形器同样是「带 + 关键形」的持有者（官方 Haru 97/97 与 636/636 关键形核实）
    ("warp_deformer", "warp_deformer.keyform_counts",
     "warp_deformer.keyform_begin_indices",
     "warp_deformer.keyform_binding_band_indices",
     ms.CountIdx.WARP_DEFORMER_KEYFORMS),
    ("rotation_deformer", "rotation_deformer.keyform_counts",
     "rotation_deformer.keyform_begin_indices",
     "rotation_deformer.keyform_binding_band_indices",
     ms.CountIdx.ROTATION_DEFORMER_KEYFORMS),
)


def _check_deformer_grids(doc: Moc3Container) -> List[LintIssue]:
    """warp 变形器控制网格的三条关系 —— 变异实验证明内核确实校验前两条。

    对官方 Haru 逐条核实成立（`tools/deformer_invariant_battery.py`）。把合法
    Haru 的 `vertex_counts` 改 1、或把 rows/cols 改成与顶点数不自洽，都会立刻被
    内核判不一致（`tools/mutate_haru_deformers.py`），因此这两条值得在写出前拦。
    """
    issues: List[LintIssue] = []
    n_warp = doc.counts[ms.CountIdx.WARP_DEFORMERS]
    if n_warp <= 0:
        return issues

    rows = doc.get("warp_deformer.rows")
    cols = doc.get("warp_deformer.cols")
    verts = doc.get("warp_deformer.vertex_counts")
    begins = doc.get("warp_deformer.keyform_begin_indices")
    counts = doc.get("warp_deformer.keyform_counts")

    for i in range(n_warp):
        expected = (rows[i] + 1) * (cols[i] + 1)
        if verts[i] != expected:
            issues.append(LintIssue(
                "warp_deformer.vertex_counts",
                f"第 {i} 个 warp 变形器 rows={rows[i]} cols={cols[i]} 应有 "
                f"{expected} 个控制点，实为 {verts[i]}"))

    if sum(counts) != doc.counts[ms.CountIdx.WARP_DEFORMER_KEYFORMS]:
        issues.append(LintIssue(
            "warp_deformer.keyform_counts",
            f"关键形总数 {sum(counts)} 与 count info "
            f"{doc.counts[ms.CountIdx.WARP_DEFORMER_KEYFORMS]} 不符"))
    for i in range(1, n_warp):
        if begins[i] != begins[i - 1] + counts[i - 1]:
            issues.append(LintIssue(
                "warp_deformer.keyform_begin_indices",
                f"第 {i} 个 warp 变形器的关键形起点 {begins[i]} 未紧接前一个 "
                f"({begins[i - 1]}+{counts[i - 1]})"))

    positions = doc.get("keyform_position.xys")
    pos_begin = doc.get("warp_deformer_keyform.keyform_position_begin_indices")
    for idx in range(len(pos_begin) - 1):
        owner = next((i for i in range(n_warp)
                      if begins[i] <= idx < begins[i] + counts[i]), None)
        if owner is None:
            continue
        want = -(-2 * verts[owner] // 16) * 16      # align16(2 * 控制点数)
        got = pos_begin[idx + 1] - pos_begin[idx]
        if got != want:
            issues.append(LintIssue(
                "warp_deformer_keyform.keyform_position_begin_indices",
                f"第 {idx} 个控制网格间距 {got}，应为 {want}"
                f"（align16(2*{verts[owner]})）"))
        if pos_begin[idx] + 2 * verts[owner] > len(positions):
            issues.append(LintIssue(
                "warp_deformer_keyform.keyform_position_begin_indices",
                f"第 {idx} 个控制网格越出 keyform_position.xys"
                f"（起点 {pos_begin[idx]}，需要 {2 * verts[owner]} 个浮点数，"
                f"池长 {len(positions)}）"))
    return issues


def _check_binding_layout(doc: Moc3Container) -> List[LintIssue]:
    """绑定布局不变量（多参数带的关键前提）。

    轴序（``index = Σ_j i_j·Π_{m<j}k_m``，第一个 binding 步长为 1）**无法从字节读出** ——
    它是列表顺序本身，没有可校验的冗余字段；所以这里校验的是它成立所需的**结构前提**：

    1. 每个 binding 必须**恰好**被一个参数的 [begin, begin+count) 区间覆盖一次，
       否则该参数名下的绑定链断裂，参数不会驱动任何形状；
    2. 同一个带内不得重复引用同一 binding；
    3. 同一个带内的多个 binding 必须属于**不同参数** —— 一个轴一个参数，
       同参数占两个轴会让轴序失去意义（实测定论见
       docs/reviews/2026-09-20-moc3-export-risk-review.md 的 F-06）。
    """
    issues: List[LintIssue] = []
    n = len(doc.get("keyform_binding.keys_counts"))
    if n == 0:
        return issues
    owner: List[int] = [-1] * n
    for p, (begin, count) in enumerate(zip(
            doc.get("parameter.keyform_binding_begin_indices"),
            doc.get("parameter.keyform_binding_counts"))):
        for j in range(begin, begin + count):
            if not 0 <= j < n:
                issues.append(LintIssue(
                    "parameter.keyform_binding_begin_indices",
                    f"参数 {p} 的绑定区间 [{begin}, {begin + count}) 越界"
                    f"（共 {n} 个绑定）"))
                continue
            if owner[j] != -1:
                issues.append(LintIssue(
                    "parameter.keyform_binding_begin_indices",
                    f"绑定 {j} 被参数 {owner[j]} 与 {p} 重复覆盖"))
            owner[j] = p
    for j, o in enumerate(owner):
        if o == -1:
            issues.append(LintIssue(
                "parameter.keyform_binding_begin_indices",
                f"绑定 {j} 不属于任何参数的区间（参数名下的绑定链断裂，"
                f"该参数不会驱动形状）"))

    begins = doc.get("keyform_binding_band.begin_indices")
    counts = doc.get("keyform_binding_band.counts")
    binding_index = doc.get("keyform_binding_index.indices")
    for band, (begin, count) in enumerate(zip(begins, counts)):
        refs = list(binding_index[begin:begin + count])
        if len(set(refs)) != len(refs):
            issues.append(LintIssue(
                "keyform_binding_index.indices",
                f"第 {band} 带重复引用了同一 binding（{refs}）"))
            continue
        owners = [owner[j] for j in refs if 0 <= j < n]
        if len(set(owners)) != len(owners):
            issues.append(LintIssue(
                "keyform_binding_index.indices",
                f"第 {band} 带的多个 binding 属于同一参数（轴应由不同参数驱动）："
                f"{refs} -> 参数 {owners}"))
    return issues


def _check_keyform_product(doc: Moc3Container) -> List[LintIssue]:
    """关键形数必须等于对象带内各绑定键数之积（官方 Haru 84/84 网格核实）。

    官方内核不会拒绝违反本规则的字节串（最小模型实测通过），但那样
    参数根本不会驱动形状 —— 属于「静默不生效」的 rig，必须在写出前拦下。
    空带（band 0）的积为 1，即静态对象只有一个关键形。
    """
    issues: List[LintIssue] = []
    band_begins = doc.get("keyform_binding_band.begin_indices")
    band_counts = doc.get("keyform_binding_band.counts")
    binding_index = doc.get("keyform_binding_index.indices")
    keys_counts = doc.get("keyform_binding.keys_counts")

    for (group, count_field, begin_field, band_field,
         total_idx) in _KEYFORM_HOLDERS:
        kf_counts = doc.get(count_field)
        if not kf_counts:
            continue
        kf_begins = doc.get(begin_field)
        band_indices = doc.get(band_field)
        total = doc.counts[total_idx]
        for i, count in enumerate(kf_counts):
            product = 1
            band = band_indices[i]
            for bi in binding_index[band_begins[band]:
                                    band_begins[band] + band_counts[band]]:
                product *= keys_counts[bi]
            if product != count:
                issues.append(LintIssue(
                    count_field,
                    f"第 {i} 个 {group} 有 {count} 个关键形，但其带（band {band}）"
                    f"内绑定键数之积为 {product}；参数不会按预期驱动形状"))
            begin = kf_begins[i] if i < len(kf_begins) else 0
            if begin < 0 or begin + count > total:
                issues.append(LintIssue(
                    begin_field,
                    f"第 {i} 项 begin={begin} count={count} 越界"
                    f"（{group} 关键形总数 {total}）"))
    return issues
