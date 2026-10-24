"""评估训练结果。

两套指标：
- per-class IoU：纯模型质量，用于挑 checkpoint。
- 项目的分层质量门禁：直接调 ``core.segment_engine.layer_quality.score_masks``，
  回答「这个模型产出的分层，管线认不认」。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.segment_engine.layer_quality import REQUIRED_PARTS, score_masks
from training.dataset import LayeringDataset
from training.labels import INDEX_TO_LABEL, NUM_CLASSES, label_map_to_rgb
from training.model import build_model


def load_checkpoint(path) -> dict:
    try:
        return torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        return torch.load(path, map_location="cpu")


def masks_from_label_map(label_map: np.ndarray) -> dict:
    masks = {}
    for index in range(1, NUM_CLASSES):
        mask = label_map == index
        if mask.any():
            masks[INDEX_TO_LABEL[index]] = mask
    return masks


@torch.no_grad()
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--size", type=int, default=None)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--limit", type=int, default=0, help="只评估前 N 张，0 表示全部")
    parser.add_argument("--dump", default=None, help="把每张图的预测彩色图存到这个目录")
    parser.add_argument("--json", default=None)
    args = parser.parse_args()

    device = args.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"

    checkpoint = load_checkpoint(args.checkpoint)
    model = build_model(
        num_classes=int(checkpoint.get("num_classes", NUM_CLASSES)),
        base=int(checkpoint.get("base", 32)),
    )
    model.load_state_dict(checkpoint["model"])
    model.eval().to(device)

    size = int(args.size or checkpoint.get("size", 512))
    dataset = LayeringDataset(args.data, size=size, augment=False)
    limit = len(dataset) if args.limit <= 0 else min(args.limit, len(dataset))

    intersection = np.zeros(NUM_CLASSES, dtype=np.float64)
    union = np.zeros(NUM_CLASSES, dtype=np.float64)
    passed = 0
    defect_log = []
    dump_dir = Path(args.dump) if args.dump else None
    if dump_dir:
        dump_dir.mkdir(parents=True, exist_ok=True)

    for index in range(limit):
        image, target = dataset[index]
        prediction = model(image.unsqueeze(0).to(device)).argmax(dim=1)[0].cpu().numpy()
        truth = target.numpy()

        for class_index in range(NUM_CLASSES):
            predicted = prediction == class_index
            expected = truth == class_index
            intersection[class_index] += np.logical_and(predicted, expected).sum()
            union[class_index] += np.logical_or(predicted, expected).sum()

        result = score_masks(masks_from_label_map(prediction), REQUIRED_PARTS)
        defects = result.get("defects") or {}
        if not defects:
            passed += 1
        else:
            defect_log.append({"sample": dataset.pairs[index][0].name, "defects": defects})

        if dump_dir:
            from PIL import Image

            name = dataset.pairs[index][0].stem
            Image.fromarray(label_map_to_rgb(prediction)).save(dump_dir / f"{name}.png")

    present = union > 0
    per_class = {
        INDEX_TO_LABEL[i]: round(float(intersection[i] / union[i]), 4)
        for i in range(NUM_CLASSES)
        if present[i]
    }
    overall = float((intersection[present] / union[present]).mean()) if present.any() else 0.0

    report = {
        "samples": limit,
        "miou": round(overall, 4),
        "per_class_iou": per_class,
        "gate_passed": passed,
        "gate_pass_rate": round(passed / limit, 4) if limit else 0.0,
        "defects": defect_log[:50],
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
