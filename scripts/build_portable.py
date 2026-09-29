#!/usr/bin/env python3
"""
Build the self-contained Windows runtime folder used by the installer.

Produces ``runtime/`` next to the project root:

    runtime/python/          venv with the CUDA torch trio + requirements.txt
    runtime/hf-cache/        ONLY the .safetensors weights (the redundant
                             .bin / .pt copies are deliberately not shipped --
                             measured 876 MB of the 1.7 GB cache is duplicate
                             formats of the same weights)
    config.portable.json     python_path / scripts_dir pointing at runtime/

Why a folder and not a frozen exe: ``api/config/config.go`` already takes
``python_path`` and ``scripts_dir``, so the Go desktop binary needs no change
to run against a bundled interpreter.

Usage:
    python scripts/build_portable.py [--dest runtime] [--dry-run] [--skip-weights]

Nothing here installs system tools. Compiling the installer itself needs Inno
Setup (``iscc.exe``), which this script deliberately does not fetch.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Pinned because it is the CUDA build matching the CPU wheel already verified
#: on this machine (torch 2.14.0 cp314). cu126 over cu130: the RTX 2060 this
#: pipeline was measured on is Turing/sm_75, and newer CUDA builds start
#: dropping older architectures.
TORCH_INDEX = "https://download.pytorch.org/whl/cu126"
TORCH_PACKAGES = ["torch==2.14.0+cu126", "torchvision==0.29.0+cu126"]

#: HuggingFace repos the sam2_gd backend loads, and the files worth shipping.
WEIGHT_SOURCES = (
    ("IDEA-Research", "grounding-dino-tiny", ("model.safetensors", "config.json",
     "preprocessor_config.json", "tokenizer.json", "tokenizer_config.json",
     "special_tokens_map.json", "vocab.txt", "added_tokens.json")),
    ("facebook", "sam2-hiera-small", ("model.safetensors", "config.json",
     "preprocessor_config.json", "processor_config.json",
     "video_preprocessor_config.json", "sam2_hiera_s.yaml")),
)


def run(cmd: list[str], dry: bool) -> int:
    print(f"  $ {' '.join(cmd)}")
    if dry:
        return 0
    return subprocess.call(cmd, env=dict(os.environ))


def human(nbytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024 or unit == "GB":
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} GB"


def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def build_venv(dest: Path, pip_args: list[list[str]], dry: bool) -> Path:
    python = dest / "python"
    if python.exists():
        print(f"[1/4] 复用已存在的 venv: {python}")
    else:
        print(f"[1/4] 创建 venv: {python}")
        if run([sys.executable, "-m", "venv", str(python)], dry) != 0:
            sys.exit("venv 创建失败")
    exe = python / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    exe = python / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    print(f"[2/4] 安装 CUDA 版 torch（索引 {TORCH_INDEX}）")
    if run([str(exe), "-m", "pip", "install", "--no-cache-dir", *pip_args[0],
            "--index-url", TORCH_INDEX], dry) != 0:
        sys.exit("torch 安装失败：runtime 不完整，不能继续")
    print("[3/4] 安装 requirements.txt")
    req = ROOT / "requirements.txt"
    if run([str(exe), "-m", "pip", "install", "--no-cache-dir", "-r", str(req)],
           dry) != 0:
        sys.exit(f"requirements.txt 安装失败（很可能是某个包没有 cp{sys.version_info[1]}"
                 f" 轮子）：runtime 不完整，不能继续")
    return exe if not dry else python / "Scripts/python.exe"


def copy_weights(dest: Path, dry: bool) -> int:
    """Copy only the listed files out of the shared HF cache, offline-safe."""
    src_root = Path.home() / ".cache" / "huggingface" / "hub"
    dst_root = dest / "hf-cache"
    total = 0
    print(f"[4/4] 复制权重（只收 safetensors 与其配置，跳过 .bin/.pt 重复份）")
    for org, repo, files in WEIGHT_SOURCES:
        src = src_root / f"models--{org}--{repo}"
        if not src.is_dir():
            print(f"  !! 本机没有 {org}/{repo}，安装包将不含该权重: {src}")
            continue
        snaps = sorted((src / "snapshots").glob("*"))
        if not snaps:
            print(f"  !! {org}/{repo} 没有快照目录")
            continue
        snap = snaps[-1]
        target = dst_root / f"models--{org}--{repo}" / "snapshots" / snap.name
        target.mkdir(parents=True, exist_ok=True)
        for name in files:
            f = snap / name
            if not f.is_file():
                print(f"  !! 缺文件 {org}/{repo}/{name}")
                continue
            dst = target / name
            if not dry and not dst.exists():
                shutil.copy2(f, dst)
            size = f.stat().st_size if f.exists() else 0
            total += size
            print(f"     {org}/{repo}/{name}  {human(size)}")
    return total


def write_config(dest: Path, dry: bool) -> None:
    py = dest / "python" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    config = {
        "python_path": str(py),
        "scripts_dir": str(ROOT),
        "hf_cache": str(dest / "hf-cache"),
    }
    out = ROOT / "config.portable.json"
    print(f"[cfg] 写入 {out}")
    if not dry:
        out.write_text(json.dumps(config, ensure_ascii=False, indent=2),
                       encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=str(ROOT / "runtime"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-weights", action="store_true")
    args = ap.parse_args()

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    exe = build_venv(dest, [TORCH_PACKAGES], args.dry_run)

    if not args.skip_weights:
        copy_weights(dest, args.dry_run)
    write_config(dest, args.dry_run)

    if args.dry_run:
        print("\n[dry-run] 未真正执行。")
        return 0
    venv_bytes = dir_size(dest / "python")
    weight_bytes = dir_size(dest / "hf-cache") if (dest / "hf-cache").is_dir() else 0
    print("\n=== 实测体积 ===")
    print(f"  runtime/python    {human(venv_bytes)}")
    print(f"  runtime/hf-cache  {human(weight_bytes)}")
    print(f"  合计              {human(venv_bytes + weight_bytes)}")
    print("\n下一步：装 Inno Setup 后编译 deploy/installer/desktop.iss；"
          "运行时用 Live2DMasterAgent.exe -config config.portable.json 指向该 runtime。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
