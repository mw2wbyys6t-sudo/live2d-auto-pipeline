"""把 checkpoint 导出成管线可识别的 ONNX + model.json。

导出的东西直接放进 ``models/custom/<name>/``，再由 ``core.custom_model`` 加载。
输出必须是 argmax 后的类别索引图（见 ``ArgmaxExport`` 的说明），否则
``runner.postprocess_segmentation`` 会拿 logits 当标签比对。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.custom_model.manifest import SCHEMA_VERSION, validate_manifest
from training.labels import INDEX_TO_LABEL, NUM_CLASSES
from training.model import ArgmaxExport, build_model


def load_checkpoint(path) -> dict:
    try:
        return torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        return torch.load(path, map_location="cpu")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--out", required=True, help="输出目录，建议 models/custom/<name>")
    parser.add_argument("--name", default=None)
    parser.add_argument("--size", type=int, default=None)
    parser.add_argument("--opset", type=int, default=17)
    args = parser.parse_args()

    checkpoint = load_checkpoint(args.checkpoint)
    model = build_model(
        num_classes=int(checkpoint.get("num_classes", NUM_CLASSES)),
        base=int(checkpoint.get("base", 32)),
    )
    model.load_state_dict(checkpoint["model"])

    wrapper = ArgmaxExport(model).eval()
    size = int(args.size or checkpoint.get("size", 512))
    dummy = torch.rand(1, 3, size, size)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = out_dir / "model.onnx"

    export_kwargs = {
        "input_names": ["input"],
        "output_names": ["output"],
        "opset_version": args.opset,
        "do_constant_folding": True,
    }
    try:
        torch.onnx.export(wrapper, dummy, str(onnx_path), dynamo=False, **export_kwargs)
    except TypeError:
        torch.onnx.export(wrapper, dummy, str(onnx_path), **export_kwargs)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "name": args.name or out_dir.name,
        "task": "segmentation",
        "format": "onnx",
        "file": "model.onnx",
        "input": {
            "name": "input",
            "shape": [1, 3, size, size],
            "color_order": "RGB",
            "scale": 1.0 / 255.0,
            "mean": [0.0, 0.0, 0.0],
            "std": [1.0, 1.0, 1.0],
        },
        "output": {
            "name": "output",
            "kind": "label_map",
            "labels": {str(index): name for index, name in INDEX_TO_LABEL.items()},
        },
        "notes": "trained from scratch via training/train.py",
    }

    errors = validate_manifest(manifest)
    if errors:
        print("[FAIL] model.json 不符合契约：" + "; ".join(errors))
        return 1

    (out_dir / "model.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    try:
        import onnxruntime as ort

        session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        output = session.run(None, {"input": dummy.numpy()})[0]
        print(f"[OK] onnxruntime 试推理输出 shape={output.shape} "
              f"取值={sorted(set(output.astype(int).ravel().tolist()))[:5]}")
    except Exception as exc:
        print(f"[WARN] onnxruntime 试推理失败：{exc}")

    print(f"[OK] 导出完成：{onnx_path}")
    print(f"[OK] 清单完成：{out_dir / 'model.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
