"""数据集读取：``images/<name>.png`` 配 ``masks/<name>.png``。

标注约定：mask 是单通道 PNG（L 模式），像素值就是 ``training.labels.LABELS``
里的类别索引。用 ``label_map_to_rgb`` 可以把它转成可视化彩色图，方便人工修。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset

from training.labels import NUM_CLASSES

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def paired_files(root: Path) -> List[Tuple[Path, Path]]:
    root = Path(root)
    images_dir = root / "images"
    masks_dir = root / "masks"
    if not images_dir.is_dir():
        return []

    pairs: List[Tuple[Path, Path]] = []
    for image_path in sorted(images_dir.iterdir()):
        if image_path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        mask_path = masks_dir / f"{image_path.stem}.png"
        if mask_path.is_file():
            pairs.append((image_path, mask_path))
    return pairs


class LayeringDataset(Dataset):
    def __init__(self, root, size: int = 512, augment: bool = False) -> None:
        self.root = Path(root)
        self.size = int(size)
        self.augment = augment
        self.pairs = paired_files(self.root)
        if not self.pairs:
            raise FileNotFoundError(
                f"{self.root} 下没有找到 images/ 与 masks/ 的同名配对样本"
            )

    def __len__(self) -> int:
        return len(self.pairs)

    def __getitem__(self, index: int) -> Tuple[torch.Tensor, torch.Tensor]:
        image_path, mask_path = self.pairs[index]

        image = Image.open(image_path).convert("RGB").resize(
            (self.size, self.size), Image.BILINEAR
        )
        mask = Image.open(mask_path).convert("L").resize(
            (self.size, self.size), Image.NEAREST
        )

        image_array = np.asarray(image, dtype=np.float32) / 255.0
        mask_array = np.asarray(mask, dtype=np.int64)
        np.clip(mask_array, 0, NUM_CLASSES - 1, out=mask_array)

        if self.augment and np.random.rand() < 0.5:
            image_array = image_array[:, ::-1].copy()
            mask_array = mask_array[:, ::-1].copy()

        tensor = torch.from_numpy(np.ascontiguousarray(image_array.transpose(2, 0, 1)))
        target = torch.from_numpy(np.ascontiguousarray(mask_array))
        return tensor, target
