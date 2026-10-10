#!/usr/bin/env python3
"""
Live2D Master Agent - 可选 AI 模型依赖安装入口

requirements.txt 中标注为「large, install separately with install_models.py」
的重量级依赖由此脚本统一安装。默认安装核心依赖之外的图像生成 / 语义分割 /
语音识别 / CLIP 嵌入等可选能力所需的包。

用法:
    python install_models.py                # 安装全部可选依赖
    python install_models.py --all
    python install_models.py --segment      # 语义分割 (SAM / ISNet)
    python install_models.py --clip         # 角色视觉嵌入 (CLIP)
    python install_models.py --asr          # 语音识别 (Whisper / FunASR)
    python install_models.py --generation   # 本地扩散模型生成 (diffusers)
    python install_models.py --list         # 只列出分组，不安装
    python install_models.py --dry-run      # 打印将执行的命令

注意: 这些包体积较大（合计可能数 GB），且部分需要 GPU 才能发挥性能。
脚本可重复执行，已安装的包会被 pip 自动跳过。
"""

import argparse
import os
import subprocess
import sys
from typing import Dict, List, Optional


# 重型包（torch/CUDA 等）安装可能很慢，给一个宽松的默认上限防止永久挂起；
# 环境变量 LIVE2D_PIP_TIMEOUT 设为 0 表示不限制。
def _pip_timeout() -> Optional[int]:
    raw = os.environ.get("LIVE2D_PIP_TIMEOUT", "").strip()
    if not raw:
        return 1800
    try:
        val = int(raw)
        return val if val > 0 else None
    except ValueError:
        return 1800


GROUPS: Dict[str, List[str]] = {
    "generation": [
        "diffusers>=0.20.0",
        "accelerate>=0.20.0",
    ],
    "segment": [
        "segment-anything>=1.0",
        # core/segment_engine/sam2_gd.py 点名后端硬依赖这三件：
        # Sam2ImageProcessor 缺 torchvision 会直接 ModelUnavailable。
        "torch>=2.0.0",
        "torchvision>=0.15.0",
        "transformers>=4.30.0",
    ],
    "clip": [
        "torch>=2.0.0",
        "torchvision>=0.15.0",
        "transformers>=4.30.0",
        "open-clip-torch>=2.20.0",
    ],
    "asr": [
        "faster-whisper>=0.10.0",
        "funasr>=1.0.0",
    ],
    "hub": [
        "huggingface-hub>=0.17.0",
    ],
}

GROUP_DESC: Dict[str, str] = {
    "generation": "本地扩散模型图像生成",
    "segment": "语义分割 (SAM2 + GroundingDINO / ISNet，提升分层质量；含 torch/torchvision/transformers)",
    "clip": "角色视觉嵌入 (角色一致性)",
    "asr": "语音识别 (本地 Whisper / FunASR)",
    "hub": "HuggingFace 模型下载工具",
}


def select_groups(args: argparse.Namespace) -> List[str]:
    chosen = [g for g in GROUPS if getattr(args, g, False)]
    if args.all or not chosen:
        return list(GROUPS.keys())
    return chosen


def build_requirements(groups: List[str]) -> List[str]:
    reqs: List[str] = []
    for g in groups:
        for pkg in GROUPS[g]:
            if pkg not in reqs:
                reqs.append(pkg)
    return reqs


def install(packages: List[str], dry_run: bool) -> int:
    if not packages:
        print("没有需要安装的包。")
        return 0
    cmd = [sys.executable, "-m", "pip", "install", *packages]
    printable = " ".join(f'"{p}"' if any(c in p for c in "><=;") else p for p in cmd)
    print("执行: " + printable)
    if dry_run:
        return 0
    timeout = _pip_timeout()
    try:
        return subprocess.call(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        limit = f"{timeout}s" if timeout else "不限"
        print(f"\n❌ pip 安装超时（上限 {limit}）。网络过慢时可：")
        print("   1) 增大上限：set LIVE2D_PIP_TIMEOUT=3600（Linux/macOS 用 export）")
        print("   2) 设为 0 不限制：set LIVE2D_PIP_TIMEOUT=0")
        print("   3) 稍后重跑本脚本，已安装的包会自动跳过。")
        return 124  # 与 POSIX timeout(1) 的超时退出码一致


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Live2D Master Agent 可选 AI 模型依赖安装",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--all", action="store_true", help="安装全部分组")
    for group in GROUPS:
        parser.add_argument(f"--{group}", action="store_true", help=GROUP_DESC[group])
    parser.add_argument("--list", action="store_true", help="只列出分组与包，不安装")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的命令")
    args = parser.parse_args()

    if args.list:
        print("可选依赖分组:")
        for group, pkgs in GROUPS.items():
            print(f"  --{group:<12} {GROUP_DESC[group]}")
            for pkg in pkgs:
                print(f"      - {pkg}")
        return 0

    groups = select_groups(args)
    packages = build_requirements(groups)
    print(f"目标分组: {', '.join(groups)}")
    print(f"待安装包 ({len(packages)}): {', '.join(packages)}")
    print("-" * 60)

    rc = install(packages, args.dry_run)
    if rc != 0:
        print(f"\n安装失败 (退出码 {rc})。可尝试逐个分组安装以定位问题。")
        return rc

    if not args.dry_run:
        print("\n安装完成。")
        print("提示: 部分能力仍需预先下载模型权重，例如 SAM/ISNet；")
        print("      启动时若检测不到权重会自动降级到 K-means 分层。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
