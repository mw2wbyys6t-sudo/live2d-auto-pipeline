"""从零训练用的紧凑 U-Net。

约 2M 参数（base=32），单卡消费级 GPU 可训。输入 512×512、5 级下采样，
输出与输入同分辨率的 num_classes 通道 logits。
"""

from __future__ import annotations

import torch
import torch.nn as nn

from training.labels import NUM_CLASSES


class DoubleConv(nn.Module):
    def __init__(self, in_channels: int, out_channels: int) -> None:
        super().__init__()
        self.block = nn.Sequential(
            nn.Conv2d(in_channels, out_channels, 3, padding=1, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_channels, out_channels, 3, padding=1, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.block(x)


class UNet(nn.Module):
    def __init__(self, in_channels: int = 3, num_classes: int = NUM_CLASSES, base: int = 32) -> None:
        super().__init__()
        widths = [base * (2 ** level) for level in range(5)]

        self.inc = DoubleConv(in_channels, widths[0])
        self.downs = nn.ModuleList(
            [
                nn.Sequential(nn.MaxPool2d(2), DoubleConv(widths[level], widths[level + 1]))
                for level in range(4)
            ]
        )

        self.ups = nn.ModuleList()
        self.decoders = nn.ModuleList()
        for level in range(4, 0, -1):
            self.ups.append(nn.ConvTranspose2d(widths[level], widths[level - 1], 2, stride=2))
            self.decoders.append(DoubleConv(widths[level - 1] * 2, widths[level - 1]))

        self.head = nn.Conv2d(widths[0], num_classes, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        skips = [self.inc(x)]
        for down in self.downs:
            skips.append(down(skips[-1]))

        x = skips[-1]
        for up, decoder, skip in zip(self.ups, self.decoders, reversed(skips[:-1])):
            x = up(x)
            x = decoder(torch.cat([x, skip], dim=1))

        return self.head(x)


class ArgmaxExport(nn.Module):
    """导出用包装：把 logits 压成 [N,1,H,W] 的类别索引图。

    管线的 ``core/custom_model/runner.py`` 在 ``label_map`` 模式下直接拿输出去
    和整数标签比对，所以 ONNX 里必须已经 argmax，不能吐 logits。
    """

    def __init__(self, model: nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        logits = self.model(x)
        return logits.argmax(dim=1, keepdim=True).to(logits.dtype)


def build_model(num_classes: int = NUM_CLASSES, base: int = 32, in_channels: int = 3) -> UNet:
    return UNet(in_channels=in_channels, num_classes=num_classes, base=base)
