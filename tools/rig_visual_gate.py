#!/usr/bin/env python3
"""视觉缺口闸门：把「姿态渲染有没有新开口子」变成可复现的判据。

为什么需要它：G 轨修颈胸接缝时，只按 y 切分让整幅宽的上衣跟着头转，肩线滑出
手臂，Core 姿态在右肩开出 730 像素的新透明区；而当时手边只有「内部洞数」这一
个读数——它把画稿本身固有的空洞和这次新开的口子混在一起（静止帧就有 1026），
于是缺陷被平均掉了。真正该量的是**当前渲染相对基线渲染新出现的透明区**。

判据分两层，都只依赖 PNG 与官方 Core 的渲染结果：

  * ``interior_holes``  轮廓内部、且不与画布边缘连通的透明区（相对量）；
  * ``new_transparent`` 基线不透明、当前透明 —— 直接对应「新开的口子」。

``render_pose`` 走仓库里的 ``drivers.live2d_runtime.moc3_verify.render_probe``，
所以读数与其它验收脚本同源；没有官方 Core 时该检查记 ``BLOCKED``，不静默通过。

用法：
    python tools/rig_visual_gate.py --manifest out/model.model3.json \\
        --pose "ParamAngleZ=30,ParamBodyAngleZ=10" \\
        --baseline-pose-png F:/tmp/base_pose.png \\
        --baseline-rest-sha 7c10ce6c... --out F:/tmp/report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 判定「不透明」的 alpha 阈值。与渲染探针统计 opaque_pixels 的口径一致。
ALPHA_THRESHOLD = 64


def _opaque_mask(alpha: np.ndarray) -> np.ndarray:
    return np.asarray(alpha) > ALPHA_THRESHOLD


def interior_holes(alpha: np.ndarray) -> Dict[str, Any]:
    """轮廓内部、且不与画布边缘连通的透明区。

    与边缘连通的那一片是背景，不是「洞」；只有被不透明像素围住的透明区才算。
    """
    opaque = _opaque_mask(alpha)
    transparent = ~opaque
    labels, count = ndimage.label(transparent)
    if count == 0:
        return {"area": 0, "regions": []}
    border = np.concatenate([labels[0, :], labels[-1, :],
                             labels[:, 0], labels[:, -1]])
    outside = set(int(v) for v in np.unique(border) if v)
    regions: List[Dict[str, Any]] = []
    for label in range(1, count + 1):
        if label in outside:
            continue
        mask = labels == label
        area = int(mask.sum())
        if area <= 0:
            continue
        ys, xs = np.where(mask)
        regions.append({"area": area,
                        "bbox": (int(xs.min()), int(xs.max()),
                                 int(ys.min()), int(ys.max()))})
    regions.sort(key=lambda item: item["area"], reverse=True)
    return {"area": sum(item["area"] for item in regions), "regions": regions}


def new_transparent(base_alpha: np.ndarray,
                    current_alpha: np.ndarray) -> Dict[str, Any]:
    """基线不透明、当前透明的像素 —— 这次改动新开的口子。"""
    base = _opaque_mask(base_alpha)
    current = _opaque_mask(current_alpha)
    changed = base & ~current
    return _summarize(changed)


def changed_pixels(base_rgba: np.ndarray,
                   current_rgba: np.ndarray) -> Dict[str, Any]:
    """静止帧的**可见**差异面积：不透明度翻转，或两侧都不透明但颜色变了。

    取代「SHA 逐字节相等」：几何切分必然改动切分线两侧的抗锯齿，逐字节相等不可达；
    但画面是不是同一张图，可以按可见差异面积度量。
    """
    base = np.asarray(base_rgba)
    current = np.asarray(current_rgba)
    if base.shape != current.shape or base.ndim != 3 or base.shape[2] != 4:
        raise ValueError("RGBA images must have matching dimensions")
    base_opaque = base[:, :, 3] > ALPHA_THRESHOLD
    current_opaque = current[:, :, 3] > ALPHA_THRESHOLD
    flipped = base_opaque != current_opaque
    both = base_opaque & current_opaque
    color_moved = np.any(base[:, :, :3] != current[:, :, :3], axis=2) & both
    return _summarize(flipped | color_moved)


def _summarize(mask: np.ndarray) -> Dict[str, Any]:
    area = int(mask.sum())
    if area == 0:
        return {"area": 0, "bbox": None}
    ys, xs = np.where(mask)
    return {"area": area,
            "bbox": (int(xs.min()), int(xs.max()),
                     int(ys.min()), int(ys.max()))}


#: 只作记录、永不参与判定的检查。静止帧 SHA 属于这一类：它证明"字节完全没动"，
#: 但任何合法的几何改动（例如拆分网格）都会让它变化，把它当门会让正确的修复永远
#: 过不了。可见差异改由 ``rest_changed_px`` 判。
_INFO_CHECKS = frozenset({"rest_sha_match"})

#: 布尔型检查（量的是「是否成立」，没有阈值）。
_BOOLEAN_CHECKS = frozenset({"rest_opaque_match", "ruler_rest_replay_ok",
                             "art_outside_parent_grid_ok"})


def evaluate_checks(measured: Dict[str, Any],
                    thresholds: Dict[str, Any],
                    evidence: Optional[Dict[str, Any]] = None
                    ) -> List[Dict[str, Any]]:
    """把读数映射成 PASS / FAIL / BLOCKED / INFO；``None`` 一律记 BLOCKED。

    ``None`` 意味着这一项压根没量到（缺官方 Core、缺基线产物），不等于通过 ——
    本仓库的验收口径是「未验证不记通过」。``evidence`` 带上失败位置（bbox /
    区域列表），让报告能定位到画面而不是只给一个数字（PRD §8.3「指标服务于定位」）。
    """
    evidence = evidence or {}
    checks: List[Dict[str, Any]] = []
    for name, value in measured.items():
        threshold = thresholds.get(name)
        if name in _INFO_CHECKS:
            status = "INFO"
        elif value is None:
            status = "BLOCKED"
        elif name in _BOOLEAN_CHECKS:
            status = "PASS" if bool(value) else "FAIL"
        elif threshold is None:
            status = "PASS"
        else:
            status = "PASS" if float(value) <= float(threshold) else "FAIL"
        checks.append({"id": name, "status": status, "measured": value,
                       "threshold": threshold, "evidence": evidence.get(name)})
    return checks


def overall_status(checks: List[Dict[str, Any]]) -> str:
    """只看参与判定的检查：全 PASS 才是 VALIDATED；一条都没判定时记 BLOCKED。"""
    gating = [check for check in checks if check["status"] != "INFO"]
    if not gating:
        return "BLOCKED"
    return ("VALIDATED" if all(check["status"] == "PASS" for check in gating)
            else "FAIL")


def _parse_pose(raw: List[str]) -> Dict[str, float]:
    values: Dict[str, float] = {}
    for chunk in raw:
        for token in chunk.split(","):
            token = token.strip()
            if not token:
                continue
            key, _, value = token.partition("=")
            values[key.strip()] = float(value)
    return values


def _load_alpha(path: Path) -> np.ndarray:
    return np.array(Image.open(path).convert("RGBA"))[:, :, 3]


def render_pose(manifest: str, values: Dict[str, float], png: Path,
                width: int, height: int) -> Dict[str, Any]:
    from drivers.live2d_runtime.moc3_verify import render_probe
    params = values or None
    return render_probe(manifest, params, png=str(png),
                        width=width, height=height, prime=False)


def _acceptance_main(argv: List[str]) -> int:
    from rig_acceptance import main as acceptance_main
    return acceptance_main(argv)


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    legacy_options = {"--pose", "--baseline-pose-png", "--baseline-rest-png",
                     "--baseline-rest-sha", "--baseline-rest-opaque"}
    if "--levels" in argv or not any(option in argv for option in legacy_options):
        return _acceptance_main(argv)
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", required=True, help="model3.json 路径")
    parser.add_argument("--pose", action="append", default=[],
                        help="逗号分隔的 Param=value，可重复")
    parser.add_argument("--baseline-pose-png", type=Path, default=None,
                        help="同一姿态下的基线渲染；缺省则新开口子这一项记 BLOCKED")
    parser.add_argument("--baseline-rest-png", type=Path, default=None,
                        help="基线静止帧渲染；缺省则静态差异面积记 BLOCKED")
    parser.add_argument("--baseline-rest-sha", default=None,
                        help="只作记录：静止帧是否逐字节一致")
    parser.add_argument("--baseline-rest-opaque", type=int, default=None)
    parser.add_argument("--max-rest-changed", type=int, default=64,
                        help="静止帧允许的可见差异像素上限")
    parser.add_argument("--max-new-transparent", type=int, default=200,
                        help="姿态下允许的新增透明像素上限")
    parser.add_argument("--width", type=int, default=400)
    parser.add_argument("--height", type=int, default=500)
    parser.add_argument("--out", type=Path, default=Path("rig_visual_report.json"))
    args = parser.parse_args(argv)
    started = datetime.now(timezone.utc)

    out_dir = args.out.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    rest_png = out_dir / "rest.png"
    pose_png = out_dir / "pose.png"

    render_errors = []
    try:
        rest = render_pose(args.manifest, {}, rest_png, args.width, args.height)
    except Exception as exc:
        rest = {"ok": False, "blocker": f"{type(exc).__name__}: {exc}"}
        render_errors.append(rest["blocker"])
    try:
        pose = render_pose(args.manifest, _parse_pose(args.pose), pose_png,
                           args.width, args.height)
    except Exception as exc:
        pose = {"ok": False, "blocker": f"{type(exc).__name__}: {exc}"}
        render_errors.append(pose["blocker"])

    measured: Dict[str, Any] = {
        "rest_sha_match": (None if not rest.get("ok") or not args.baseline_rest_sha
                           else rest["pixels_sha256"] == args.baseline_rest_sha),
        "rest_opaque_match": (None if not rest.get("ok")
                              or args.baseline_rest_opaque is None
                              else rest.get("opaque_pixels") == args.baseline_rest_opaque),
        "rest_changed_px": None,
        "new_transparent_px": None,
    }
    # 洞数只作记录，不单独设闸：静止帧本身就有约千像素画稿固有空洞，把它当判据会
    # 把真实缺陷平均掉。真正判的是「相对基线新开的口子」。
    evidence: Dict[str, Any] = {}
    if rest.get("ok") and rest_png.is_file():
        measured["_rest_sha"] = rest.get("pixels_sha256")
        measured["_rest_holes_px"] = interior_holes(_load_alpha(rest_png))["area"]
        if args.baseline_rest_png and args.baseline_rest_png.is_file():
            baseline_image = Image.open(args.baseline_rest_png)
            current_image = Image.open(rest_png)
            if (baseline_image.size == current_image.size and
                    baseline_image.mode == current_image.mode == "RGBA"):
                diff = changed_pixels(np.array(baseline_image), np.array(current_image))
                measured["rest_changed_px"] = diff["area"]
                evidence["rest_changed_px"] = {"bbox": diff["bbox"]}
            else:
                evidence["rest_changed_px"] = {
                    "baseline_size": baseline_image.size, "baseline_mode": baseline_image.mode,
                    "current_size": current_image.size, "current_mode": current_image.mode}
    if (pose.get("ok") and pose_png.is_file()
            and args.baseline_pose_png and args.baseline_pose_png.is_file()):
        baseline_image = Image.open(args.baseline_pose_png)
        current_image = Image.open(pose_png)
        if (baseline_image.size == current_image.size and
                baseline_image.mode == current_image.mode == "RGBA"):
            diff = new_transparent(np.array(baseline_image)[:, :, 3],
                                   np.array(current_image)[:, :, 3])
            measured["new_transparent_px"] = diff["area"]
            evidence["new_transparent_px"] = {"bbox": diff["bbox"]}
        else:
            measured["new_transparent_px"] = None
            evidence["new_transparent_px"] = {
                "baseline_size": baseline_image.size, "baseline_mode": baseline_image.mode,
                "current_size": current_image.size, "current_mode": current_image.mode}
    if pose.get("ok") and pose_png.is_file():
        measured["_pose_holes_px"] = interior_holes(_load_alpha(pose_png))["area"]

    thresholds = {"new_transparent_px": args.max_new_transparent,
                  "rest_changed_px": args.max_rest_changed}
    checks = evaluate_checks({k: v for k, v in measured.items()
                              if not k.startswith("_")}, thresholds, evidence)
    if render_errors:
        checks.append({"id": "render_execution", "status": "FAIL", "measured": None,
                       "threshold": None, "evidence": None})
    report = {
        "state": overall_status(checks),
        "manifest": str(args.manifest),
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "pose": _parse_pose(args.pose),
        "checks": checks,
        "recorded": {k: v for k, v in measured.items() if k.startswith("_")},
        "renders": {"rest": str(rest_png), "pose": str(pose_png)},
        "blockers": [item for item in (rest.get("blocker"), pose.get("blocker"))
                     if item],
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"{report['state']}: {args.out}")
    for check in checks:
        print(f"  [{check['status']:7s}] {check['id']} = {check['measured']}"
              f" (阈值 {check['threshold']})")
    return 0 if report["state"] == "VALIDATED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
