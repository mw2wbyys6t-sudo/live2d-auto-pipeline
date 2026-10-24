"""端到端冒烟测试：合成数据 → 短训练 → 导出 ONNX → 用管线契约回读。

用来在准备真实数据之前，确认「训练 → 导出 → core.custom_model 能加载」
这条链是通的。全程内存友好（默认 64×64、base=8）。

    python -m training.smoke_test
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import torch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.custom_model.manifest import load_manifest
from core.custom_model.runner import run
from training.labels import INDEX_TO_LABEL, LABEL_TO_INDEX, NUM_CLASSES, CLASS_COLORS


def make_synthetic(root: Path, count: int, size: int) -> None:
    images_dir = root / "images"
    masks_dir = root / "masks"
    images_dir.mkdir(parents=True, exist_ok=True)
    masks_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(7)
    for index in range(count):
        label_map = np.zeros((size, size), dtype=np.uint8)

        top = int(size * (0.10 + 0.05 * (index % 3)))
        face_top = int(size * 0.30)
        face_bottom = int(size * 0.62)
        middle = size // 2

        label_map[:top, :] = LABEL_TO_INDEX["hair_back"]
        label_map[top:face_top, :] = LABEL_TO_INDEX["hair_front"]
        label_map[face_top:face_bottom, :] = LABEL_TO_INDEX["face"]
        label_map[face_bottom:int(size * 0.72), :] = LABEL_TO_INDEX["neck"]
        label_map[int(size * 0.72):, :] = LABEL_TO_INDEX["clothes_top"]

        label_map[face_top + 4:face_top + 12, middle - 14:middle - 6] = LABEL_TO_INDEX["eyes_left"]
        label_map[face_top + 4:face_top + 12, middle + 6:middle + 14] = LABEL_TO_INDEX["eyes_right"]
        label_map[face_top + 20:face_top + 26, middle - 6:middle + 6] = LABEL_TO_INDEX["mouth"]

        color = np.zeros((size, size, 3), dtype=np.uint8)
        for name, value in CLASS_COLORS.items():
            color[label_map == LABEL_TO_INDEX[name]] = value
        color = np.clip(color.astype(np.int16) + rng.integers(-18, 18, color.shape), 0, 255).astype(np.uint8)

        Image.fromarray(color).save(images_dir / f"synth_{index:03d}.png")
        Image.fromarray(label_map, mode="L").save(masks_dir / f"synth_{index:03d}.png")


def main() -> int:
    size = 64
    with tempfile.TemporaryDirectory() as work:
        work_path = Path(work)
        dataset_dir = work_path / "dataset"
        out_dir = work_path / "model"

        print("[1/4] 生成合成数据")
        make_synthetic(dataset_dir, count=6, size=size)
        print(f"      {dataset_dir}")

        print("[2/4] 短训练")
        from training.train import main as train_main

        sys.argv = [
            "train",
            "--data", str(dataset_dir),
            "--out", str(out_dir),
            "--size", str(size),
            "--base", "8",
            "--epochs", "2",
            "--batch", "2",
            "--val-split", "0.34",
            "--device", "cpu",
        ]
        if train_main() != 0:
            print("[FAIL] 训练失败")
            return 1

        print("[3/4] 导出 ONNX")
        from training.export import main as export_main

        sys.argv = [
            "export",
            "--checkpoint", str(out_dir / "best.pt"),
            "--out", str(out_dir),
            "--name", "smoke",
        ]
        if export_main() != 0:
            print("[FAIL] 导出失败")
            return 1

        print("[4/4] 用 core.custom_model 回读")
        manifest = load_manifest(str(out_dir))
        print(f"      清单: {manifest.name} / {manifest.task} / {manifest.output.kind}")

        blank = Image.new("RGB", (size, size), (128, 128, 128))
        masks = run(manifest, blank)
        print(f"      推理产出部件: {sorted(masks)}")

        if not masks:
            print("[FAIL] 模型没有产出任何部件掩码")
            return 1

        unknown = set(masks) - set(INDEX_TO_LABEL.values())
        if unknown:
            print(f"[FAIL] 出现未在标签表里的部件: {unknown}")
            return 1

        print("[OK] 端到端链路打通：训练 → 导出 → 清单加载 → 推理掩码")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
