#!/usr/bin/env python3
"""可选重能力按需下载器（P2）——哈希校验 + HTTP 断点续传，不进基础包。

基础包只含 CPU 轻依赖，主链路（生图 → 颜色分层 → moc3 导出 → 预览）无需本脚本。
下列组件为「质量升级」，按需下载到用户数据目录（LIVE2D_DATA_DIR / hf-cache 或
models/），下载失败不影响主链路，分层自动回退颜色启发式。

用法（在 app 源码树根目录下）：
    python scripts/optional_components.py list              # 查看组件状态
    python scripts/optional_components.py download rembg    # 下载指定组件（断点续传）
    python scripts/optional_components.py verify rembg      # 仅校验已下载文件哈希

组件清单（URL / SHA256 / 体积）见 MANIFEST；改版本必须同步更新哈希，未登记的
组件会在下载后提示回写，绝不静默使用未校验文件。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# 可选组件清单：全部为宽松许可；体积小的 ONNX 模型直接下载，
# torch 系（SAM2）体积过大，仅给指引，不在这里硬拉。
MANIFEST: dict[str, "Component"] = {}


@dataclass
class Component:
    key: str
    label: str
    url: str
    sha256: str
    size_mb: float
    dest_rel: str
    note: str


def _register(c: Component) -> None:
    MANIFEST[c.key] = c


# rembg u2net 背景移除模型（ONNX，~170MB）—— 可选去背质量升级
_register(Component(
    key="rembg-u2net",
    label="rembg u2net 去背模型（可选）",
    url="https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx",
    sha256="",  # 首次下载后实测回写锁定；未登记时校验降级为「存在性」并告警
    size_mb=170.0,
    dest_rel="models/u2net.onnx",
    note="ISNet/rembg 学不到前景掩码时的可选去背；缺失时颜色分层照常工作。",
))


def data_root() -> Path:
    return Path(os.environ.get("LIVE2D_DATA_DIR", str(Path.home() / "Live2DMasterAgent")))


def dest_path(c: Component) -> Path:
    return data_root() / c.dest_rel


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def download_with_resume(c: Component, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    existing = tmp.stat().st_size if tmp.exists() else 0

    req = urllib.request.Request(c.url)
    if existing > 0:
        req.add_header("Range", f"bytes={existing}-")
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length", "0")) + existing
        mode = "ab" if existing > 0 else "wb"
        with tmp.open(mode) as f:
            downloaded = existing
            while True:
                buf = resp.read(1 << 16)
                if not buf:
                    break
                f.write(buf)
                downloaded += len(buf)
                if total:
                    pct = downloaded * 100 // total
                    print(f"\r  {c.key}: {downloaded // (1<<20)}/{total // (1<<20)} MB ({pct}%)",
                          end="", flush=True)
    print()

    if c.sha256:
        actual = sha256_of(tmp)
        if actual != c.sha256.lower():
            tmp.unlink(missing_ok=True)
            raise SystemExit(f"❌ {c.key} 哈希不匹配：期望 {c.sha256}，实测 {actual}。文件可能损坏。")
        print(f"✓ {c.key} SHA256 校验通过")
    else:
        print(f"⚠ {c.key} 未在 MANIFEST 登记哈希，实测 SHA256={sha256_of(tmp)}（请回写锁定）")

    tmp.replace(dest)
    print(f"✓ {c.label} 已就绪: {dest}")


def verify(c: Component) -> bool:
    dest = dest_path(c)
    if not dest.is_file():
        print(f"✗ {c.key} 未下载: {dest}")
        return False
    if c.sha256:
        ok = sha256_of(dest) == c.sha256.lower()
        print(("✓" if ok else "✗"), f"{c.key} 哈希", "通过" if ok else "不匹配")
        return ok
    print(f"○ {c.key} 已存在（未登记哈希，跳过校验）: {dest}")
    return True


def status_line(c: Component) -> str:
    dest = dest_path(c)
    state = "已就绪" if dest.is_file() else "未下载"
    return f"[{state:>4}] {c.key:<16} {c.size_mb:>7.1f} MB  — {c.note}"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="可选重能力按需下载器（哈希校验 + 断点续传）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    d = sub.add_parser("download"); d.add_argument("component")
    v = sub.add_parser("verify"); v.add_argument("component")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        print(f"数据目录: {data_root()}")
        for c in MANIFEST.values():
            print(status_line(c))
        return 0

    c = MANIFEST.get(args.component)
    if not c:
        print(f"未知组件 {args.component}；可选: {', '.join(MANIFEST)}")
        return 2
    if args.cmd == "download":
        download_with_resume(c, dest_path(c))
        return 0
    if args.cmd == "verify":
        return 0 if verify(c) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
