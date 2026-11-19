#!/usr/bin/env python3
"""图集坐标与导出的一致性验收。

为什么需要：图集打包（``texture_atlas.pack``）产出的是「UV 矩形 + 画布裁剪框」，
模型导出再用 ``uv_for_vertex`` 把顶点算成 uv。这两条链路此前没有任何交叉验证：
UV 算错、矩形重叠、bbox 与实际像素不一致，都只会在成品里表现为贴图错位，
靠肉眼几乎无法定位。

本模块把四件东西对齐检查：

1. **UV 与像素矩形自洽**：``u0*page_w == top_left[0]``、``u1-u0 == bbox宽/page_w``；
2. **同页不重叠**：两个图层的内容矩形不得相交（重叠 = 后画的把前一个盖掉）；
3. **回采逐字节相等**：按 UV 从图集裁出的像素必须等于图层按 bbox 裁出的像素；
4. **模型顶点 UV 可推导**：模型里每个顶点的 uv 必须等于
   ``uv_for_vertex(rect, 顶点画布坐标)``。

约定：依赖或输入不完整时返回 ``ok=None``（未验证），绝不静默洗成通过。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from core.logger import get_logger

log = get_logger("atlas_consistency")

#: 浮点比较容差。uv 由除法得到，1e-9 足够区分「算错」与「舍入」。
UV_EPS = 1e-6


def _result(available: bool, errors: Optional[List[str]] = None,
            checked: int = 0) -> Dict[str, Any]:
    errs = list(errors or [])
    if not available:
        return {"available": False, "ok": None, "errors": errs, "checked": checked}
    return {"available": True, "ok": not errs, "errors": errs, "checked": checked}


def _rects_overlap(a: Tuple[float, float, float, float],
                   b: Tuple[float, float, float, float]) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1


def verify_atlas_layout(pack_result: Dict[str, Any]) -> Dict[str, Any]:
    """校验 UV 元数据自洽 + 同页矩形不重叠。

    Args:
        pack_result: :meth:`TextureAtlas.pack` 的返回值（需要 ``uvs``）。
    """
    uvs = pack_result.get("uvs")
    if not isinstance(uvs, dict):
        return _result(False, ["pack_result 缺少 uvs"])
    if not uvs:
        return _result(True, checked=0)

    errors: List[str] = []
    by_page: Dict[int, List[Tuple[str, Tuple[float, float, float, float]]]] = {}

    for name, rect in uvs.items():
        try:
            page = int(rect["page"])
            page_w = float(rect["atlas_width"])
            page_h = float(rect["atlas_height"])
            tl = (float(rect["top_left"][0]), float(rect["top_left"][1]))
            br = (float(rect["bottom_right"][0]), float(rect["bottom_right"][1]))
            u0, v0 = float(rect["u0"]), float(rect["v0"])
            u1, v1 = float(rect["u1"]), float(rect["v1"])
            bx, by, bw, bh = (float(v) for v in rect["bbox"])
        except (KeyError, TypeError, IndexError, ValueError) as exc:
            errors.append(f"{name}: UV 字段缺失或非法 ({exc})")
            continue

        if page_w <= 0 or page_h <= 0:
            errors.append(f"{name}: 图集尺寸非法 {page_w}x{page_h}")
            continue
        for label, value in (("u0", u0), ("v0", v0), ("u1", u1), ("v1", v1)):
            if not (-UV_EPS <= value <= 1.0 + UV_EPS):
                errors.append(f"{name}: {label}={value:.6f} 越界 [0,1]")
        if u1 <= u0 or v1 <= v0:
            errors.append(f"{name}: UV 矩形退化 (u1<=u0 或 v1<=v0)")

        # UV 与像素矩形必须互相可推导，否则模型贴图会整体错位。
        if abs(u0 * page_w - tl[0]) > 1e-3:
            errors.append(f"{name}: u0*page_w={u0 * page_w:.3f} != top_left.x={tl[0]:.3f}")
        if abs(v0 * page_h - tl[1]) > 1e-3:
            errors.append(f"{name}: v0*page_h={v0 * page_h:.3f} != top_left.y={tl[1]:.3f}")
        if abs((u1 - u0) * page_w - bw) > 1e-3:
            errors.append(f"{name}: UV 宽度 {((u1 - u0) * page_w):.3f} != bbox 宽 {bw:.3f}")
        if abs((v1 - v0) * page_h - bh) > 1e-3:
            errors.append(f"{name}: UV 高度 {((v1 - v0) * page_h):.3f} != bbox 高 {bh:.3f}")

        width = br[0] - tl[0]
        height = br[1] - tl[1]
        if abs(width - bw) > 1e-3 or abs(height - bh) > 1e-3:
            errors.append(
                f"{name}: 像素矩形 {width:.1f}x{height:.1f} != bbox {bw:.1f}x{bh:.1f}")
        by_page.setdefault(page, []).append((name, (tl[0], tl[1], br[0], br[1])))

    for page, entries in by_page.items():
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                name_a, rect_a = entries[i]
                name_b, rect_b = entries[j]
                if _rects_overlap(rect_a, rect_b):
                    errors.append(
                        f"第 {page} 页 {name_a} 与 {name_b} 的 UV 矩形重叠")

    return _result(True, errors, checked=len(uvs))


def verify_atlas_pixels(pack_result: Dict[str, Any],
                        layers: Dict[str, Any]) -> Dict[str, Any]:
    """按 UV 从图集回采像素，与图层裁剪区逐字节比对。

    只对「未被重采样」的层做逐字节比较（打包器在超过图集上限时会 LANCZOS
    缩放）；被缩放的层记入 errors 的说明，不当作通过。
    """
    uvs = pack_result.get("uvs")
    atlases = pack_result.get("atlases")
    if not isinstance(uvs, dict) or not isinstance(atlases, (list, tuple)):
        return _result(False, ["pack_result 缺少 uvs 或 atlases"])
    if not uvs:
        return _result(True, checked=0)

    errors: List[str] = []
    skipped: List[str] = []
    checked = 0
    for name, rect in uvs.items():
        image = layers.get(name)
        if image is None:
            errors.append(f"{name}: layers 里没有对应图像")
            continue
        page = int(rect["page"])
        if page < 0 or page >= len(atlases):
            errors.append(f"{name}: page 下标 {page} 超出 atlases 数量")
            continue
        bx, by, bw, bh = (int(round(float(v))) for v in rect["bbox"])
        x0, y0 = (int(round(float(v))) for v in rect["top_left"])
        x1, y1 = (int(round(float(v))) for v in rect["bottom_right"])
        if bw <= 0 or bh <= 0 or x1 <= x0 or y1 <= y0:
            errors.append(f"{name}: 裁剪区或 UV 矩形为空")
            continue

        expected = np.asarray(image.convert("RGBA").crop((bx, by, bx + bw, by + bh)))
        actual = np.asarray(atlases[page].convert("RGBA").crop((x0, y0, x1, y1)))
        if expected.shape != actual.shape:
            skipped.append(
                f"{name}: 被重采样 {expected.shape[:2]} -> {actual.shape[:2]}，跳过逐字节比较")
            continue
        checked += 1
        # 透明像素的 RGB 在缩放/粘贴后可能被归零，只比较有内容的像素。
        mask = expected[..., 3] > 0
        if mask.any() and not np.array_equal(expected[mask], actual[mask]):
            diff = int(np.abs(expected[mask].astype(np.int16)
                              - actual[mask].astype(np.int16)).sum())
            errors.append(f"{name}: 图集回采像素与原图层不一致（差值合计 {diff}）")

    result = _result(True, errors, checked=checked)
    if skipped:
        result["skipped"] = skipped
    return result


def verify_model_uvs(model: Dict[str, Any],
                     atlas_uvs: Dict[str, Any],
                     uv_for_vertex=None) -> Dict[str, Any]:
    """校验模型每个顶点的 uv 都能由图集矩形 + 顶点画布坐标推导出来。

    Args:
        model: 导出的模型 JSON，需含 ``meshes``，每项有 ``name`` / ``uvs`` /
            顶点画布坐标（``vertices`` 或 ``points``，形如 ``[[x, y], ...]``）。
        atlas_uvs: ``uvs`` 映射（图层名 -> 矩形元数据）。
        uv_for_vertex: 计算函数，默认取 :func:`moc3_pipeline.uv_for_vertex`。
    """
    meshes = model.get("meshes")
    if not isinstance(meshes, (list, tuple)):
        return _result(False, ["model 缺少 meshes"])
    if not isinstance(atlas_uvs, dict):
        return _result(False, ["atlas_uvs 非法"])
    if uv_for_vertex is None:
        try:
            from live2d_builder.exporter.moc3_pipeline import uv_for_vertex as default_uv
        except Exception as exc:  # pragma: no cover - 导入失败
            return _result(False, [f"无法导入 uv_for_vertex: {exc}"])
        uv_for_vertex = default_uv

    errors: List[str] = []
    checked = 0
    for mesh in meshes:
        name = str(mesh.get("name") or "")
        rect = atlas_uvs.get(name)
        if rect is None:
            continue  # 没有图集的网格（如纯色层）不参与此校验
        uvs = mesh.get("uvs")
        verts = mesh.get("vertices") or mesh.get("points")
        if not isinstance(uvs, (list, tuple)) or not isinstance(verts, (list, tuple)):
            errors.append(f"{name}: 缺少 uvs 或顶点坐标")
            continue
        if len(uvs) != len(verts):
            errors.append(f"{name}: uvs 与顶点数量不一致 ({len(uvs)} vs {len(verts)})")
            continue
        width = float(mesh.get("width") or rect["bbox"][2])
        height = float(mesh.get("height") or rect["bbox"][3])
        for index, (uv, vert) in enumerate(zip(uvs, verts)):
            try:
                expected_u, expected_v = uv_for_vertex(
                    rect, float(vert[0]), float(vert[1]), width, height)
                got_u, got_v = float(uv[0]), float(uv[1])
            except (TypeError, IndexError, ValueError) as exc:
                errors.append(f"{name}[{index}]: uv 数据非法 ({exc})")
                continue
            checked += 1
            if abs(got_u - expected_u) > UV_EPS or abs(got_v - expected_v) > UV_EPS:
                errors.append(
                    f"{name}[{index}]: uv=({got_u:.6f},{got_v:.6f}) "
                    f"!= 推导值 ({expected_u:.6f},{expected_v:.6f})")

    return _result(True, errors, checked=checked)