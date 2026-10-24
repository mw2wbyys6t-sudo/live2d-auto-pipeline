"""训练主循环。

两个入口：
- ``--self-test``：不读数据，随机张量跑一遍前向 + 反向，用来确认环境装好、
  网络形状对得上。在还没准备数据集时先跑这个。
- ``--data <dir>``：正式训练，按验证集 mIoU 保存最佳权重。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, random_split

from training.dataset import LayeringDataset
from training.labels import NUM_CLASSES
from training.model import build_model


@torch.no_grad()
def evaluate_iou(model, loader, device, num_classes: int) -> float:
    model.eval()
    intersection = np.zeros(num_classes, dtype=np.float64)
    union = np.zeros(num_classes, dtype=np.float64)

    for images, masks in loader:
        predictions = model(images.to(device)).argmax(dim=1).cpu().numpy()
        truth = masks.numpy()
        for class_index in range(num_classes):
            predicted = predictions == class_index
            expected = truth == class_index
            intersection[class_index] += np.logical_and(predicted, expected).sum()
            union[class_index] += np.logical_or(predicted, expected).sum()

    present = union > 0
    if not present.any():
        return 0.0
    return float((intersection[present] / union[present]).mean())


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    return "cuda" if torch.cuda.is_available() else "cpu"


def run_self_test(model, device, size: int) -> int:
    images = torch.rand(2, 3, size, size, device=device)
    masks = torch.randint(0, NUM_CLASSES, (2, size, size), device=device, dtype=torch.long)

    logits = model(images)
    loss = F.cross_entropy(logits, masks)
    loss.backward()

    parameters = sum(p.numel() for p in model.parameters())
    print(f"[self-test] device={device} logits={tuple(logits.shape)} "
          f"loss={loss.item():.4f} params={parameters:,}")
    print("[self-test] 前向/反向通过，环境可用")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=None, help="数据集根目录，含 images/ 和 masks/")
    parser.add_argument("--out", default="models/custom/layering", help="权重输出目录")
    parser.add_argument("--size", type=int, default=512)
    parser.add_argument("--base", type=int, default=32, help="U-Net 首层通道数")
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--val-split", type=float, default=0.15)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    device = resolve_device(args.device)
    model = build_model(num_classes=NUM_CLASSES, base=args.base).to(device)

    if args.self_test:
        return run_self_test(model, device, args.size)

    if not args.data:
        parser.error("需要 --data，或先用 --self-test 验证环境")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    dataset = LayeringDataset(args.data, size=args.size, augment=True)
    val_size = max(1, int(len(dataset) * args.val_split))
    train_size = len(dataset) - val_size
    if train_size < 1:
        parser.error(f"样本太少（{len(dataset)} 张），无法切验证集")

    train_set, val_set = random_split(dataset, [train_size, val_size])
    train_loader = DataLoader(train_set, batch_size=args.batch, shuffle=True, drop_last=True)
    val_loader = DataLoader(val_set, batch_size=1)

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)

    best_iou = 0.0
    best_path = out_dir / "best.pt"

    for epoch in range(1, args.epochs + 1):
        model.train()
        running = 0.0
        for images, masks in train_loader:
            images = images.to(device)
            masks = masks.to(device)
            optimizer.zero_grad()
            loss = F.cross_entropy(model(images), masks)
            loss.backward()
            optimizer.step()
            running += loss.item() * images.size(0)
        scheduler.step()

        train_loss = running / max(1, len(train_set))
        iou = evaluate_iou(model, val_loader, device, NUM_CLASSES)

        marker = ""
        if iou > best_iou:
            best_iou = iou
            torch.save(
                {
                    "model": model.state_dict(),
                    "base": args.base,
                    "size": args.size,
                    "num_classes": NUM_CLASSES,
                    "best_iou": best_iou,
                },
                best_path,
            )
            marker = "  *best"

        print(f"[{epoch:03d}/{args.epochs}] loss={train_loss:.4f} val_mIoU={iou:.4f}{marker}")

    summary = {
        "best_miou": best_iou,
        "epochs": args.epochs,
        "size": args.size,
        "base": args.base,
        "samples": len(dataset),
    }
    (out_dir / "train_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"最佳 mIoU={best_iou:.4f}，权重已保存到 {best_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
