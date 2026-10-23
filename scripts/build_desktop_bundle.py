#!/usr/bin/env python3
"""装配 Windows 桌面安装包的运行时资源目录（Tauri `bundle.resources` 的源）。

产物（默认 `desktop/bundle/`，安装后即 Tauri 的 ``$RESOURCE`` 根）：

    bundle/
      live2d-api.exe          Go 后端（内嵌 Web 工作台 UI）
      face_tracker.exe        Rust 原生面捕
      onnxruntime.dll         面捕 load-dynamic 依赖（须与 exe 同目录）
      models/face_detection_yunet_2023mar.onnx
      models/rembg/isnet-anime.onnx   前景（抠图）权重，分层质量的地基
      python/                 嵌入式 CPython 3.12（含离线 site-packages）
      app/                    Python 工程源码树（core/ drivers/ ...）
      BUILD-INFO.json         版本 + SHA256 + 体积清单

``desktop/tauri.conf.json`` 用 resources 的 **map 形式** ``{"bundle/": ""}``
把这些文件**扁平**落到 ``$RESOURCE``，与 ``api/config/desktop.go`` 的
``resolveDesktopRoot`` 约定一致 —— ``<res>/app/core/workflow.py`` 就是布局标记，
命中后 Go 自动把解释器/源码/用户数据目录全部解析到正确位置。

为什么用 Python 而不是 PS1：a) 仓库已有 ``scripts/build_portable.py`` 先例；
b) 下载 / SHA256 / 离线 wheel 装配 / smoke 校验在 Linux 与 Windows 上同一套
逻辑可跑通，便于本地复现与 CI 一致。hashes.json 里提到的 Bundle-Desktop.ps1
即本脚本的前身命名。

用法（Windows 构建机或 GitHub Actions windows-latest 上）：

    python scripts/build_desktop_bundle.py \\
        --go-exe api/live2d-api.exe \\
        --face-tracker-exe native/face_tracker/target/release/face_tracker.exe

常用开关见 ``--help``。全量依赖清单见 ``requirements.desktop.txt``。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# Windows 控制台/CI 的默认编码常是 cp1252（非 UTF-8），直接 print 中文会抛
# UnicodeEncodeError 把整个装配打断。统一切到 UTF-8（reconfigure 需 3.7+，
# 失败则忽略，不影响功能）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# 钉死的下载物（版本升级时必须同步更新 scripts/windows/hashes.json）
# ---------------------------------------------------------------------------
PYTHON_VERSION = "3.12.10"
_pv = PYTHON_VERSION.split(".")
PYTHON_TAG = f"python{_pv[0]}{_pv[1]}"           # python312
PYTHON_ABI = f"cp{_pv[0]}{_pv[1]}"               # cp312
PYTHON_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
    f"python-{PYTHON_VERSION}-embed-amd64.zip"
)

YUNET_NAME = "face_detection_yunet_2023mar.onnx"
# 注意：opencv_zoo 用 Git LFS 托管该模型。`raw.githubusercontent.com` 只会返回
# 131 字节的 LFS 指针文本，必须走 media.githubusercontent.com（或 github.com/.../raw）
# 才会拿到真正的二进制。
YUNET_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
    "models/face_detection_yunet/" + YUNET_NAME
)

# ISNet 动漫前景（抠图）模型权重。
#
# 为什么必须内置：rembg 找不到本地权重时会**静默联网下载** 176MB 权重，
# 离线/内网环境必然失败，分层随之退化成颜色启发式（实测：头发占 46%、
# 脸仅 0.7%、眼睛完全缺失）。安装包内置权重才能保证分层质量稳定。
#
# 官方来源是 HuggingFace 的 skytnt/anime-seg（rembg 2.x 也是从这里取），
# 国内网络直连 huggingface.co 常不稳定，故追加 hf-mirror.com 镜像兜底。
#
# **rembg 官方 release 排第一**：2026-10-21 起 skytnt/anime-seg 两个地址都返回
# 404（上游仓库被删/改名），桌面构建因此在「装配运行时」阶段整条挂掉
# （run 39576584827）。rembg 的 v0.0.0 release 里那份 isnet-anime.onnx 与锁定表
# 的 SHA256 逐字节一致（实测 176,069,933 B → f15622d8…），是同一个文件，
# 所以只换下载源、不动哈希锁定。
ISNET_ANIME_NAME = "isnet-anime.onnx"
ISNET_ANIME_URLS = (
    "https://github.com/danielgatis/rembg/releases/download/v0.0.0/"
    + ISNET_ANIME_NAME,
    "https://huggingface.co/skytnt/anime-seg/resolve/main/" + ISNET_ANIME_NAME,
    "https://hf-mirror.com/skytnt/anime-seg/resolve/main/" + ISNET_ANIME_NAME,
)

ONNXRUNTIME_VERSION = "1.31.0"
ONNXRUNTIME_URL = (
    "https://github.com/microsoft/onnxruntime/releases/download/"
    f"v{ONNXRUNTIME_VERSION}/onnxruntime-win-x64-{ONNXRUNTIME_VERSION}.zip"
)

# MediaPipe Tasks 人脸关键点模型：mediapipe>=0.10.30 删除了经典 solutions API，
# drivers/face_tracker 会在缺少 solutions 时改用 Tasks FaceLandmarker，需要此模型。
FACE_LANDMARKER_NAME = "face_landmarker.task"
FACE_LANDMARKER_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)

# Python 工程源码树：只收运行时真正用到的部分（不含 tests/ tools/ docs/ api/）。
APP_DIRS = ["core", "drivers", "live2d_builder", "llm_bridge", "prompts", "templates"]
APP_FILES = ["local_image_generator.py", "LICENSE", "requirements.desktop.txt"]

HASHES_FILE = ROOT / "scripts" / "windows" / "hashes.json"
DEFAULT_REQUIREMENTS = ROOT / "requirements.desktop.txt"

# 随包注入的 sitecustomize：把同目录树下的 app/ 加进 sys.path。
# 嵌入式 Python 存在 pythonXY._pth 时会进入 isolated 模式（忽略 PYTHONPATH），
# 而 Go 桥以 `python <res>/app/core/workflow.py` 调用，脚本目录是 core/ 而非项目
# 根，故必须在此显式补上 <res>/app。按 __file__ 绝对定位，与盘符/用户名无关。
SITECUSTOMIZE = '''"""Live2D Master Agent：把同目录树下的 app/ 注入 sys.path。

嵌入式 Python + pythonXY._pth = isolated 模式，PYTHONPATH 被忽略；Go 桥以脚本
绝对路径调用 core/workflow.py，sys.path[0] 是 core/ 而非项目根。这里按本文件
位置（<res>/python/Lib/site-packages/sitecustomize.py）上溯三级定位 <res>/app。
"""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_app = os.path.normpath(os.path.join(_here, "..", "..", "..", "app"))
if os.path.isdir(_app) and _app not in sys.path:
    sys.path.insert(0, _app)
'''

# 冒烟校验：required 缺失即失败；optional 缺失只告警（按需下载/机型相关）。
SMOKE_REQUIRED = ["PIL", "numpy", "scipy", "sklearn", "cv2", "psd_tools",
                  "onnxruntime", "core.version", "live2d_builder.pipeline"]
SMOKE_OPTIONAL = ["torch", "mediapipe", "mediapipe.tasks.python.vision", "rembg",
                  "edge_tts", "sounddevice", "soundfile", "pygame", "live2d"]


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def run(cmd: list) -> None:
    printable = " ".join(str(c) for c in cmd)
    print(f"  $ {printable}")
    rc = subprocess.call([str(c) for c in cmd])
    if rc != 0:
        sys.exit(f"❌ 命令失败（退出码 {rc}）：{printable}")


def load_hashes() -> dict:
    if HASHES_FILE.exists():
        try:
            return json.loads(HASHES_FILE.read_text(encoding="utf-8"))
        except Exception as e:  # 损坏的锁定表不应静默放行
            sys.exit(f"❌ 无法解析 {HASHES_FILE}: {e}")
    return {}


def download(url: str, dest: Path, *, offline: bool, retries: int = 3) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"    [cache] {dest.name}  {human(dest.stat().st_size)}")
        return dest
    if offline:
        sys.exit(f"❌ 离线模式（--skip-download）但缓存缺失：{dest}\n"
                 f"   请先联网运行一次以填充缓存。")
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        tmp = dest.with_name(dest.name + ".part")
        try:
            print(f"    [get ] {url}  （尝试 {attempt}/{retries}）")
            req = urllib.request.Request(
                url, headers={"User-Agent": "live2d-desktop-bundler/1.0"})
            with urllib.request.urlopen(req, timeout=180) as resp, open(tmp, "wb") as out:
                shutil.copyfileobj(resp, out, length=1 << 20)
            tmp.replace(dest)
            size = dest.stat().st_size
            print(f"    [ok  ] {dest.name}  {human(size)}")
            if size < 1024:
                print("    [warn] 文件异常小，可能是 Git LFS 指针或错误页；"
                      "随后的 SHA256 校验会拦截")
            return dest
        except Exception as e:  # 网络抖动：退避重试
            last_err = e
            print(f"    [!!  ] 下载失败：{e}")
            time.sleep(2 * attempt)
    sys.exit(f"❌ 下载失败（{retries} 次）：{url}\n   最后错误：{last_err}")


def download_any(urls: tuple[str, ...], dest: Path, *, offline: bool) -> Path:
    """依次尝试多个镜像下载同一文件；任一成功即返回。

    权重 176MB、来源在境外，单一站点抖动就会让整个构建失败，
    因此按顺序重试所有镜像。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"    [cache] {dest.name}  {human(dest.stat().st_size)}")
        return dest
    if offline:
        sys.exit(f"❌ 离线模式（--skip-download）但缓存缺失：{dest}\n"
                 f"   请先联网运行一次以填充缓存。")
    errors = []
    for url in urls:
        try:
            print(f"    [get ] {url}")
            tmp = dest.with_name(dest.name + ".part")
            req = urllib.request.Request(
                url, headers={"User-Agent": "live2d-desktop-bundler/1.0"})
            with urllib.request.urlopen(req, timeout=600) as resp, open(tmp, "wb") as out:
                shutil.copyfileobj(resp, out, length=1 << 20)
            tmp.replace(dest)
            print(f"    [ok  ] {dest.name}  {human(dest.stat().st_size)}")
            return dest
        except Exception as e:
            errors.append(f"{url} → {e}")
            print(f"    [!!  ] 下载失败：{e}")
            try:
                dest.with_name(dest.name + ".part").unlink()
            except OSError:
                pass
    sys.exit("❌ 全部镜像均下载失败：\n   " + "\n   ".join(errors))


def verify_hash(path: Path, expected: str, key: str, record: dict) -> None:
    actual = sha256_file(path)
    record[key] = actual
    if not expected:
        print(f"    [hash] {key} 未在锁定表中设置 → 实测 {actual}")
        return
    if actual.lower() != expected.lower():
        # 删掉坏缓存，避免以后每次构建都复用同一个错误文件（如 LFS 指针）。
        try:
            path.unlink()
            removed = "已删除该缓存文件，重跑会重新下载。"
        except OSError:
            removed = "缓存文件删除失败，可手动清理。"
        sys.exit(
            f"❌ SHA256 校验失败：{key}\n"
            f"   期望 {expected}\n   实测 {actual}\n   文件 {path}\n"
            f"   {removed}\n"
            f"   若确属版本升级，请更新 {HASHES_FILE.relative_to(ROOT)} 后重试。")
    print(f"    [hash] {key} OK")


def extract_zip(zip_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)


def patch_pth(python_dir: Path) -> None:
    """放开 import site + 增加 Lib\\site-packages，并保留标准库 zip 与自身目录。"""
    pth = python_dir / f"{PYTHON_TAG}._pth"
    if not pth.exists():
        found = sorted(python_dir.glob("python*._pth"))
        if not found:
            sys.exit(f"❌ 未找到 ._pth（嵌入式 Python 布局异常）：{python_dir}")
        pth = found[0]
    lines = [f"{PYTHON_TAG}.zip", ".", r"Lib\site-packages", "import site"]
    pth.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"    [pth ] {pth.name} → {lines}")


def write_sitecustomize(site_packages: Path) -> None:
    site_packages.mkdir(parents=True, exist_ok=True)
    (site_packages / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    print("    [path] 写入 sitecustomize.py（注入 <res>/app 到 sys.path）")


def pip_common_args() -> list:
    """始终只取二进制轮子；非 Windows 宿主机时显式声明目标环境（跨平台装配）。"""
    args = ["--only-binary=:all:"]
    if platform.system() != "Windows":
        args += ["--platform", "win_amd64", "--python-version", PYTHON_VERSION[:4],
                 "--implementation", "cp", "--abi", PYTHON_ABI]
    return args


def download_wheels(requirements: Path, wheels_dir: Path, offline: bool) -> None:
    if any(wheels_dir.glob("*.whl")):
        print(f"  [复用] wheel 缓存 {wheels_dir}（{len(list(wheels_dir.glob('*.whl')))} 个）")
        return
    if offline:
        sys.exit(f"❌ 离线模式但 wheel 缓存为空：{wheels_dir}")
    wheels_dir.mkdir(parents=True, exist_ok=True)
    run([sys.executable, "-m", "pip", "download", *pip_common_args(),
         "--no-cache-dir", "-d", wheels_dir, "-r", requirements])


def install_wheels(requirements: Path, wheels_dir: Path, site_packages: Path) -> None:
    site_packages.mkdir(parents=True, exist_ok=True)
    run([sys.executable, "-m", "pip", "install", "--no-index",
         "--find-links", wheels_dir, "--target", site_packages,
         "--no-cache-dir", "--upgrade", *pip_common_args(), "-r", requirements])


def copy_app_tree(app_dir: Path) -> None:
    app_dir.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache")
    for name in APP_DIRS:
        src = ROOT / name
        if not src.is_dir():
            sys.exit(f"❌ 源码目录缺失：{src}")
        shutil.copytree(src, app_dir / name, dirs_exist_ok=True, ignore=ignore)
    for name in APP_FILES:
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, app_dir / name)
    print(f"    [app ] {', '.join(APP_DIRS)} + {', '.join(APP_FILES)}")


def copy_license_bundle(dest: Path) -> None:
    """把许可证文件复制到安装包资源根（FF-15）。

    ADR-001 Rev.4：对外分发的安装包必须内含与仓库根逐字节一致的 Apache-2.0
    许可全文；根目录若存在 NOTICE 则一并保留。放在 bundle/ 根 = 安装后的
    $RESOURCE 根，用户能在安装目录里直接看到许可文本。
    """
    sys.path.insert(0, str(ROOT))
    try:
        from core.license_bundle import install_license_bundle

        written = install_license_bundle(dest)
    except Exception as exc:
        sys.exit(f"❌ 许可文件写入失败（FF-15 要求产出物内含许可全文）：{exc}")
    if not written:
        sys.exit("❌ 仓库根找不到 LICENSE —— 无法产出合规的安装包（FF-15）")
    print(f"    [许可] {', '.join(written)} → {dest}")


def copy_binary(src: Path, dest: Path, label: str) -> None:
    if not src.exists():
        sys.exit(f"❌ 缺少 {label}：{src}\n   请先构建（见脚本 docstring 的 --go-exe / "
                 f"--face-tracker-exe）。")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    print(f"    [bin ] {label} → {dest.name}  {human(dest.stat().st_size)}")


def smoke_test(bundle: Path) -> None:
    py = bundle / "python" / "python.exe"
    if not py.exists():
        print("  [跳过] 未找到 python.exe，无法冒烟")
        return
    if platform.system() != "Windows":
        print("  [跳过] 非 Windows 宿主，无法执行 win_amd64 解释器")
        return
    probes = SMOKE_REQUIRED + SMOKE_OPTIONAL
    code = (
        "import importlib, json\n"
        f"mods = {json.dumps(probes)}\n"
        "res = {}\n"
        "for m in mods:\n"
        "    try:\n"
        "        importlib.import_module(m)\n"
        "        res[m] = 'ok'\n"
        "    except Exception as e:\n"
        "        res[m] = 'FAIL: %s' % e\n"
        "print('__SMOKE__' + json.dumps(res))\n"
    )
    print("  [冒烟] 用随包解释器校验导入 ...")
    proc = subprocess.run([str(py), "-c", code], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=900)
    line = ""
    for out in (proc.stdout, proc.stderr):
        for l in (out or "").splitlines():
            if l.startswith("__SMOKE__"):
                line = l[len("__SMOKE__"):]
    if not line:
        sys.exit(f"❌ 冒烟未产出结果（随包解释器无法运行）。\nstdout:\n{proc.stdout}\n"
                 f"stderr:\n{proc.stderr}")
    res = json.loads(line)
    failed_required = []
    for m in SMOKE_REQUIRED:
        if res.get(m) != "ok":
            failed_required.append(f"{m}: {res.get(m)}")
    for m in SMOKE_OPTIONAL:
        if res.get(m) != "ok":
            print(f"    [warn] 可选模块不可用 {m}: {res.get(m)}")
    if failed_required:
        sys.exit("❌ 必装模块导入失败：\n  " + "\n  ".join(failed_required))
    print(f"    [ok  ] 必装 {len(SMOKE_REQUIRED)} 项全部可导入")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="装配 Windows 桌面安装包运行时资源目录（Tauri resources 源）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", default=str(ROOT / "desktop" / "bundle"),
                    help="产物根目录（默认 desktop/bundle）")
    ap.add_argument("--cache-dir", default=str(ROOT / ".cache" / "desktop-bundle"),
                    help="下载缓存目录")
    ap.add_argument("--requirements", default=str(DEFAULT_REQUIREMENTS),
                    help="桌面依赖清单（默认 requirements.desktop.txt）")
    ap.add_argument("--go-exe", default=str(ROOT / "api" / "live2d-api.exe"),
                    help="Go 后端可执行文件")
    ap.add_argument("--face-tracker-exe",
                    default=str(ROOT / "native" / "face_tracker" / "target" / "release"
                                / "face_tracker.exe"),
                    help="Rust 原生面捕可执行文件")
    ap.add_argument("--skip-python", action="store_true", help="复用已解压的 python/")
    ap.add_argument("--skip-wheels", action="store_true", help="跳过 wheel 下载/安装")
    ap.add_argument("--skip-app", action="store_true", help="跳过源码树复制")
    ap.add_argument("--skip-native", action="store_true", help="跳过 exe/dll/模型复制")
    ap.add_argument("--isnet-anime-onnx", default="",
                    help="本地 isnet-anime.onnx 权重路径（默认优先用 models/rembg/"
                         "isnet-anime.onnx，其次联网下载）")
    ap.add_argument("--skip-download", action="store_true",
                    help="完全离线：只用缓存中已有的下载物")
    ap.add_argument("--no-smoke-test", action="store_true", help="不跑随包解释器冒烟")
    args = ap.parse_args()

    dest = Path(args.dest)
    cache = Path(args.cache_dir)
    requirements = Path(args.requirements)
    offline = args.skip_download

    if not requirements.exists():
        sys.exit(f"❌ 依赖清单不存在：{requirements}")
    if platform.system() != "Windows":
        print(
            "⚠  当前宿主非 Windows。pip 的 --platform 只约束 wheel 标签，**不改变环境\n"
            "   标记的评估**（platform_system / python_version 仍按宿主机），因此：\n"
            "     - torch 等含 platform_system 标记的依赖可能解析失败（Linux 上会误拉\n"
            "       nvidia-* 依赖）；\n"
            "     - pygame / pygame-ce 这类 python_version 分支可能选错。\n"
            "   正式安装包务必在 Windows（GitHub Actions windows-latest，python 3.12）\n"
            "   上装配；本机运行仅用于调试装配逻辑，冒烟校验会被自动跳过。")

    hashes = load_hashes()
    record: dict = {}
    dest.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    print("=" * 68)
    print(" Live2D Master Agent — 桌面运行时装配")
    print(f" 产物: {dest}")
    print(f" 缓存: {cache}")
    print("=" * 68)

    # ---- 1) 嵌入式 Python ----
    print("[1/5] 嵌入式 CPython")
    python_dir = dest / "python"
    site_packages = python_dir / "Lib" / "site-packages"
    if args.skip_python and (python_dir / f"{PYTHON_TAG}.zip").exists():
        print("  [跳过] 复用已有 python/")
    else:
        if python_dir.exists():
            shutil.rmtree(python_dir)
        zip_path = download(PYTHON_EMBED_URL, cache / f"python-{PYTHON_VERSION}-embed-amd64.zip",
                            offline=offline)
        verify_hash(zip_path, hashes.get("python-embed", ""), "python-embed", record)
        extract_zip(zip_path, python_dir)
        print(f"    [zip ] 解压 → {python_dir}")
    patch_pth(python_dir)
    write_sitecustomize(site_packages)

    # ---- 2) 依赖轮子（离线装配进 site-packages）----
    print("[2/5] Python 依赖（离线 wheel → site-packages）")
    if args.skip_wheels:
        print("  [跳过] --skip-wheels")
    else:
        wheels_dir = cache / "wheels"
        download_wheels(requirements, wheels_dir, offline)
        install_wheels(requirements, wheels_dir, site_packages)
        print(f"    [site] site-packages = {human(dir_size(site_packages))}")

    # ---- 3) Python 工程源码树 ----
    print("[3/5] Python 源码树 → app/")
    if args.skip_app:
        print("  [跳过] --skip-app")
    else:
        copy_app_tree(dest / "app")

    # ---- 3b) 许可证全文（FF-15：安装包必须内含许可声明）----
    print("[3b/5] 许可证全文 → bundle 根")
    copy_license_bundle(dest)

    # ---- 4) 原生二进制 / DLL / 模型 ----
    print("[4/5] 原生二进制、ONNX Runtime、YuNet 模型")
    if args.skip_native:
        print("  [跳过] --skip-native")
    else:
        copy_binary(Path(args.go_exe), dest / "live2d-api.exe", "Go 后端")
        copy_binary(Path(args.face_tracker_exe), dest / "face_tracker.exe", "原生面捕")

        ort_zip = download(ONNXRUNTIME_URL,
                           cache / f"onnxruntime-win-x64-{ONNXRUNTIME_VERSION}.zip",
                           offline=offline)
        extract_dir = cache / f"ort-{ONNXRUNTIME_VERSION}"
        if not extract_dir.exists():
            extract_zip(ort_zip, extract_dir)
        dll = next(extract_dir.rglob("onnxruntime.dll"), None)
        if dll is None:
            sys.exit(f"❌ 在 {extract_dir} 中未找到 onnxruntime.dll")
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dll, dest / "onnxruntime.dll")
        print(f"    [dll ] onnxruntime.dll {human((dest / 'onnxruntime.dll').stat().st_size)}")

        models_dir = dest / "models"
        models_dir.mkdir(parents=True, exist_ok=True)
        yunet = download(YUNET_URL, cache / YUNET_NAME, offline=offline)
        verify_hash(yunet, hashes.get("yunet-onnx", ""), "yunet-onnx", record)
        shutil.copy2(yunet, models_dir / YUNET_NAME)
        print(f"    [onnx] models/{YUNET_NAME}")

        landmarker = download(FACE_LANDMARKER_URL, cache / FACE_LANDMARKER_NAME,
                              offline=offline)
        verify_hash(landmarker, hashes.get("face-landmarker-task", ""),
                    "face-landmarker-task", record)
        shutil.copy2(landmarker, models_dir / FACE_LANDMARKER_NAME)
        print(f"    [task] models/{FACE_LANDMARKER_NAME}")

        # ISNet 动漫前景权重：分层质量的地基。缺失时 rembg 会尝试联网下载，
        # 离线/内网下必然失败 → 分层静默退化成颜色启发式（脸/眼睛全丢）。
        rembg_dir = models_dir / "rembg"
        rembg_dir.mkdir(parents=True, exist_ok=True)
        local_isnet = Path(args.isnet_anime_onnx) if args.isnet_anime_onnx else \
            ROOT / "models" / "rembg" / ISNET_ANIME_NAME
        if local_isnet.is_file() and local_isnet.stat().st_size > 0:
            isnet = local_isnet
            print(f"    [本地] 使用已有权重 {isnet}")
        else:
            isnet = download_any(ISNET_ANIME_URLS, cache / ISNET_ANIME_NAME,
                                 offline=offline)
        verify_hash(isnet, hashes.get("isnet-anime-onnx", ""),
                    "isnet-anime-onnx", record)
        shutil.copy2(isnet, rembg_dir / ISNET_ANIME_NAME)
        print(f"    [onnx] models/rembg/{ISNET_ANIME_NAME}  "
              f"{human((rembg_dir / ISNET_ANIME_NAME).stat().st_size)}")

    # ---- 5) 冒烟 + BUILD-INFO ----
    print("[5/5] 冒烟校验与 BUILD-INFO")
    if not args.no_smoke_test:
        smoke_test(dest)

    sizes = {
        "python": dir_size(dest / "python"),
        "app": dir_size(dest / "app"),
        "models": dir_size(dest / "models"),
        "total": dir_size(dest),
    }
    info = {
        "product": "Live2D Master Agent",
        "python_embed": PYTHON_VERSION,
        "onnxruntime": ONNXRUNTIME_VERSION,
        "yunet": YUNET_NAME,
        "sha256": record,
        "sizes_bytes": sizes,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (dest / "BUILD-INFO.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    print("-" * 68)
    print(" 体积：")
    for k in ("python", "app", "models", "total"):
        print(f"   {k:8} {human(sizes[k])}")
    print(f" BUILD-INFO.json 已写入 {dest / 'BUILD-INFO.json'}")
    missing = [k for k in ("python-embed", "yunet-onnx", "onnxruntime-dll",
                           "face-landmarker-task", "isnet-anime-onnx")
               if not hashes.get(k)]
    if missing:
        print("\n 提示：以下下载物尚未在锁定表中固定 SHA256，已实测并记录到 "
              "BUILD-INFO.json。")
        print(f"        复核无误后请回填 {HASHES_FILE.relative_to(ROOT)} 转为强制校验：")
        for k in missing:
            print(f'         "{k}": "{record.get(k, "")}"')
    print("=" * 68)
    print(" ✅ 装配完成。下一步：cargo tauri build --bundles nsis（在 desktop/ 下）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
