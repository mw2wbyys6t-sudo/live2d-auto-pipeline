#!/usr/bin/env python3
"""Validate or export a user-trained model for the Live2D pipeline.

Examples:
  python tools/custom_model_tool.py check --dir ~/models/custom/my-depth
  python tools/custom_model_tool.py manifest --out ./my-depth --task depth --name my-depth
  python tools/custom_model_tool.py export --module my_pkg.models:DepthNet \
      --checkpoint ./ckpt.pth --out ./my-depth --name my-depth --task depth \
      --shape 1,3,512,512
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.custom_model import (  # noqa: E402
    TASK_DEPTH,
    TASK_SEGMENTATION,
    load_manifest,
    make_manifest_dict,
)
from core.custom_model.runner import probe, run  # noqa: E402


def _shape(text: str) -> tuple:
    parts = [int(p) for p in text.replace(" ", "").split(",") if p]
    if len(parts) != 4 or any(p <= 0 for p in parts):
        raise argparse.ArgumentTypeError("shape 需要 4 个正整数：N,C,H,W")
    return tuple(parts)


def _floats(count: int):
    def parse(text: str) -> tuple:
        parts = [float(p) for p in text.replace(" ", "").split(",") if p]
        if len(parts) != count:
            raise argparse.ArgumentTypeError(f"需要 {count} 个用逗号分隔的数字")
        return tuple(parts)

    return parse


def _load_labels(path: str | None):
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_manifest(args, out_dir: Path) -> dict:
    data = make_manifest_dict(
        name=args.name,
        task=args.task,
        file=args.file,
        shape=args.shape,
        color_order=args.color_order,
        scale=args.scale,
        mean=args.mean,
        std=args.std,
        output_kind=args.output_kind,
        depth_range=args.depth_range,
        near_is_large=not args.near_is_small,
        labels=_load_labels(args.labels),
        notes=args.notes,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "model.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data


def cmd_check(args) -> int:
    try:
        manifest = load_manifest(str(Path(args.dir)))
    except Exception as exc:
        print(f"[FAIL] 清单不可用：{exc}")
        return 1

    print(f"[OK] 清单：{manifest.name} / {manifest.task}")
    print(f"     输入 {manifest.input.shape} {manifest.input.color_order}")
    print(f"     输出 {manifest.output.kind}")

    try:
        info = probe(manifest)
        print("[OK] onnxruntime 已加载模型")
        print(f"     输入张量：{info['inputs']}")
        print(f"     输出张量：{info['outputs']}")
    except Exception as exc:
        print(f"[FAIL] 模型加载失败：{exc}")
        return 1

    width, height = manifest.input.shape[3], manifest.input.shape[2]
    blank = Image.new("RGB", (width, height), (128, 128, 128))
    try:
        result = run(manifest, blank)
    except Exception as exc:
        print(f"[FAIL] 试推理失败：{exc}")
        return 1

    if manifest.task == TASK_DEPTH:
        print(
            f"[OK] 试推理：深度图 {result.shape} "
            f"min={float(result.min()):.3f} max={float(result.max()):.3f}"
        )
    else:
        print(f"[OK] 试推理：{len(result)} 个部件 {sorted(result)}")
    return 0


def cmd_manifest(args) -> int:
    data = _write_manifest(args, Path(args.out))
    print(f"[OK] 已写出 {Path(args.out) / 'model.json'}")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_export(args) -> int:
    try:
        import torch
    except ImportError:
        print("[FAIL] 未安装 torch，无法从 checkpoint 导出")
        return 1

    module_name, _, class_name = args.module.partition(":")
    if not module_name or not class_name:
        print("[FAIL] --module 需要形如 package.module:ClassName")
        return 1

    model_cls = getattr(importlib.import_module(module_name), class_name)
    model = model_cls()
    state = torch.load(args.checkpoint, map_location="cpu")
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    model.load_state_dict(state)
    model.eval()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = out_dir / args.file
    dummy = torch.randn(*args.shape)

    torch.onnx.export(
        model,
        dummy,
        str(onnx_path),
        input_names=["image"],
        output_names=["output"],
        opset_version=args.opset,
    )
    print(f"[OK] 已导出 {onnx_path}")

    _write_manifest(args, out_dir)
    print(f"[OK] 已写出 {out_dir / 'model.json'}")

    try:
        import onnxruntime as ort

        session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        with torch.no_grad():
            reference = model(dummy).cpu().numpy()
        actual = session.run(None, {"image": dummy.numpy()})[0]
        diff = float(np.abs(reference - actual).max())
        print(f"[OK] PyTorch 与 ONNX 最大绝对误差：{diff:.6f}")
    except Exception as exc:
        print(f"[WARN] ONNX 复核未完成：{exc}")

    try:
        manifest = load_manifest(str(out_dir))
        info = probe(manifest)
        print(f"[OK] 契约校验通过：{info['inputs']} -> {info['outputs']}")
    except Exception as exc:
        print(f"[WARN] 契约校验未通过：{exc}")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--name", default="my-model")
    common.add_argument("--task", choices=[TASK_DEPTH, TASK_SEGMENTATION], default=TASK_DEPTH)
    common.add_argument("--file", default="model.onnx")
    common.add_argument("--shape", type=_shape, default=(1, 3, 512, 512))
    common.add_argument("--color-order", choices=["RGB", "BGR"], default="RGB")
    common.add_argument("--scale", type=float, default=1.0 / 255.0)
    common.add_argument("--mean", type=_floats(3), default=(0.0, 0.0, 0.0))
    common.add_argument("--std", type=_floats(3), default=(1.0, 1.0, 1.0))
    common.add_argument("--output-kind", choices=["depth", "label_map", "masks"], default=None)
    common.add_argument("--depth-range", type=_floats(2), default=(0.0, 1.0))
    common.add_argument("--near-is-small", action="store_true")
    common.add_argument("--labels", default=None)
    common.add_argument("--notes", default="")

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="校验已放入目录的模型")
    check.add_argument("--dir", required=True)
    check.set_defaults(func=cmd_check)

    manifest = sub.add_parser("manifest", parents=[common], help="只生成 model.json")
    manifest.add_argument("--out", required=True)
    manifest.set_defaults(func=cmd_manifest)

    export = sub.add_parser("export", parents=[common], help="从 PyTorch checkpoint 导出 ONNX")
    export.add_argument("--module", required=True)
    export.add_argument("--checkpoint", required=True)
    export.add_argument("--out", required=True)
    export.add_argument("--opset", type=int, default=17)
    export.set_defaults(func=cmd_export)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
