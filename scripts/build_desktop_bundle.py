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

try:
    from packaging.requirements import InvalidRequirement, Requirement
except ImportError:  # 装配机通常只保证有 pip，而 pip 自带一份 packaging
    from pip._vendor.packaging.requirements import InvalidRequirement, Requirement

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
# **rembg 官方 release 排第一**：2026-10-21 起 skytnt/anime-seg 两个地址都返回
# 404（上游仓库被删/改名），桌面构建因此在「装配运行时」阶段整条挂掉
# （run 39576584827）。rembg 的 v0.0.0 release 里那份 isnet-anime.onnx 与锁定表
# 的 SHA256 逐字节一致（实测 176,069,933 B → f15622d8…），是同一个文件，
# 所以只换下载源、不动哈希锁定。
#
# 2026-10-28 复测：skytnt/anime-seg 至今只有 `isnetis.onnx`（不是同名文件、哈希也
# 不同），原先留下的两条 HuggingFace「镜像」全部稳定 404，等于兜底链形同虚设；
# GitHub 直连在本机只有 ~25 kB/s（176MB → 两小时）。现改为经 gh-proxy 反代同一
# release 资产，实测 7–19 MB/s，落地文件哈希与锁定表一致。反代只改路由、不改字节，
# 但如果哪天被篡改或缓存了别的东西，`verify_hash` 会直接拦下（不是静默接受）。
ISNET_ANIME_NAME = "isnet-anime.onnx"
ISNET_ANIME_URLS = (
    "https://github.com/danielgatis/rembg/releases/download/v0.0.0/"
    + ISNET_ANIME_NAME,
    "https://gh-proxy.com/https://github.com/danielgatis/rembg/releases/"
    "download/v0.0.0/" + ISNET_ANIME_NAME,
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
SITECUSTOMIZE = '''"""Pygmalion Studio：把同目录树下的 app/ 注入 sys.path。

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
# 注意：torch / torchvision / transformers 刻意**不在**这两个列表里。
# 它们不随包分发（见 requirements.desktop.txt 与 core/capabilities.py 的设计
# 契约），由应用内按需下载，缺失是**预期状态** —— 留在 optional 里只会让每次
# 装配日志刷出一条无意义的 [warn]。
# rembg 同理（v0.10.10 起移出基础包：它钉着 CPU 版 onnxruntime，会静默顶掉
# Windows 的 DirectML；分层主链路走 isnet 权重直连，不经过 rembg）。
SMOKE_OPTIONAL = ["mediapipe", "mediapipe.tasks.python.vision",
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


def verify_hash(path: Path, expected: str, key: str, record: dict,
                *, evict: Path | None = None) -> None:
    """记录实测 SHA256；锁定表已有值时不匹配即失败。

    `evict` 用于「来源缓存」（如解压前的 zip）：被校验文件只是它的产物，
    只删产物下次还会从同一个坏缓存里解出来，所以两者一起清掉。
    """
    actual = sha256_file(path)
    record[key] = actual
    if not expected:
        print(f"    [hash] {key} 未在锁定表中设置 → 实测 {actual}")
        return
    if actual.lower() != expected.lower():
        # 删掉坏缓存，避免以后每次构建都复用同一个错误文件（如 LFS 指针）。
        removed = []
        for target in (path, evict):
            if target is None:
                continue
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                removed.append(str(target))
            except OSError:
                removed.append(f"{target}（删除失败，可手动清理）")
        sys.exit(
            f"❌ SHA256 校验失败：{key}\n"
            f"   期望 {expected}\n   实测 {actual}\n   文件 {path}\n"
            f"   已清理：{', '.join(removed) or '无'}\n"
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


# 安装包只发 Windows x64：wheel 标签、随包解释器、环境标记三者必须同一口径。
PLATFORM_TAG = "win_amd64"

# 环境标记的求值口径。pip 的 --platform/--python-version **只**约束 wheel 标签，
# 标记仍按宿主机求值（pip/_internal/req/req_install.py 用 packaging 的
# default_environment），所以这一步必须自己接管 —— 见 flatten_requirements()。
# 这里只列**需要覆盖**的键：packaging 会与 default_environment() 合并。
TARGET_MARKER_ENV_BY_PLATFORM = {
    "win_amd64": {
        "sys_platform": "win32",
        "platform_system": "Windows",
        "os_name": "nt",
        "platform_machine": "AMD64",
    },
}


def target_marker_env() -> dict:
    env = dict(TARGET_MARKER_ENV_BY_PLATFORM[PLATFORM_TAG])
    env.update({
        "python_version": PYTHON_VERSION[:4],
        "python_full_version": PYTHON_VERSION,
        "implementation_name": "cpython",
        "implementation_version": PYTHON_VERSION,
        "platform_python_implementation": "CPython",
        "extra": "",
    })
    return env


def flatten_requirements(text: str) -> list[str]:
    """按随包解释器求值环境标记，产出真正交给 pip 的依赖行。

    宿主机解释器一旦和嵌入式 3.12 不一致，直接把原始清单喂给 pip 就会串味：
    `python_version < "3.14"` 在 3.14 机器上判成 False，产物里装的是 pygame-ce
    而不是 pygame —— 冒烟还照样绿（两者都提供 `pygame` 模块）。
    """
    lines: list[str] = []
    env = target_marker_env()
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-"):
            lines.append(line)  # --index-url 之类的选项行原样透传
            continue
        try:
            req = Requirement(line)
        except InvalidRequirement as exc:
            raise ValueError(f"第 {lineno} 行无法解析依赖行：{line!r}（{exc}）") from exc
        if req.marker is not None and not req.marker.evaluate(env):
            continue
        req.marker = None
        lines.append(str(req))
    return lines


def resolved_requirements(requirements: Path, wheels_dir: Path) -> Path:
    """把求值后的清单落盘（缓存目录里，便于人工核对装配到底喂了什么）。"""
    out = wheels_dir.parent / "requirements.resolved.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(flatten_requirements(
        requirements.read_text(encoding="utf-8"))) + "\n", encoding="utf-8")
    return out


def pip_common_args() -> list:
    """把 wheel 标签钉在**随包解释器**（嵌入式 CPython 3.12 / win_amd64）上。

    pip 默认按宿主机解释器挑轮子，而产物里的解释器是解压出来的 3.12.10。两者
    一旦不一致（比如在 Python 3.14 的 Windows 机器上装配），cp314 的 numpy/opencv
    会被灌进 3.12 的 site-packages，装完直接 ImportError。CI 恰好用了 3.12 才没暴露。
    环境标记不在这里解决 —— pip 不按 --python-version 求值标记，见 flatten_requirements()。
    """
    return ["--only-binary=:all:",
            "--platform", PLATFORM_TAG,
            "--python-version", PYTHON_VERSION[:4],
            "--implementation", "cp",
            "--abi", PYTHON_ABI]


def _requirements_digest(requirements: Path) -> str:
    """缓存指纹：清单原文 + 按随包解释器求值出的解析结果。

    只看原文不够 —— CI 的 actions/cache 用 restore-keys 前缀匹配，清单没改、
    解析口径（钉死的 3.12/win_amd64）改了时，旧 wheel 集合照样被恢复出来。
    """
    body = requirements.read_bytes()
    resolved = "\n".join(flatten_requirements(body.decode("utf-8"))).encode("utf-8")
    return hashlib.sha256(body + b"\n" + resolved).hexdigest()


def download_wheels(requirements: Path, wheels_dir: Path, offline: bool) -> None:
    """下载 wheel 到缓存目录。

    缓存不能只看「有没有 .whl」：CI 的 actions/cache 用 ``restore-keys`` 前缀
    匹配，清单改了仍会把**旧版本**的 wheel 目录恢复出来，而 ``pip install
    --no-index --find-links`` 在离线目录里找不到新下限（实测：Pillow 下限抬到
    13.0.0 后，缓存里只有 12.3.0，装配直接失败）。因此用清单指纹做标记，
    指纹不一致就丢弃旧 wheel 重新下载。
    """
    marker = wheels_dir / ".requirements.sha256"
    want = _requirements_digest(requirements)
    have = ""
    if marker.is_file():
        try:
            have = marker.read_text(encoding="utf-8").strip()
        except OSError:
            have = ""

    cached = list(wheels_dir.glob("*.whl"))
    if cached:
        if have == want:
            print(f"  [复用] wheel 缓存 {wheels_dir}（{len(cached)} 个）")
            return
        print(f"  [重建] 依赖清单已变更（缓存标记 {have[:12] or '缺失'} ≠ "
              f"{want[:12]}），丢弃 {len(cached)} 个旧 wheel 重新下载")
        for whl in cached:
            try:
                whl.unlink()
            except OSError:
                pass
        try:
            marker.unlink()
        except OSError:
            pass

    if offline:
        sys.exit(f"❌ 离线模式但 wheel 缓存为空：{wheels_dir}")
    wheels_dir.mkdir(parents=True, exist_ok=True)
    resolved = resolved_requirements(requirements, wheels_dir)
    run([sys.executable, "-m", "pip", "download", *pip_common_args(),
         "--no-cache-dir", "-d", wheels_dir, "-r", str(resolved)])
    marker.write_text(want + "\n", encoding="utf-8")


def install_wheels(requirements: Path, wheels_dir: Path, site_packages: Path) -> None:
    site_packages.mkdir(parents=True, exist_ok=True)
    resolved = resolved_requirements(requirements, wheels_dir)
    run([sys.executable, "-m", "pip", "install", "--no-index",
         "--find-links", wheels_dir, "--target", site_packages,
         "--no-cache-dir", "--upgrade", *pip_common_args(), "-r", str(resolved)])


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

    ADR-001 Rev.5：对外分发的安装包必须内含与仓库根逐字节一致的 AGPL-3.0
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


def smoke_test(bundle: Path) -> dict:
    """用随包解释器校验导入，并返回实测到的 onnxruntime provider 列表。"""
    py = bundle / "python" / "python.exe"
    if not py.exists():
        print("  [跳过] 未找到 python.exe，无法冒烟")
        return {}
    if platform.system() != "Windows":
        print("  [跳过] 非 Windows 宿主，无法执行 win_amd64 解释器")
        return {}
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
        "try:\n"
        "    import onnxruntime as _ort\n"
        "    res['__ORT_PROVIDERS__'] = _ort.get_available_providers()\n"
        "except Exception as e:\n"
        "    res['__ORT_PROVIDERS__'] = 'FAIL: %s' % e\n"
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
    providers = res.pop("__ORT_PROVIDERS__", None)
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

    # GPU 运行时闸门：Windows 基础包必须带 DmlExecutionProvider。
    # onnxruntime 与 onnxruntime-directml 共用 onnxruntime/ 目录，任何把 CPU 版
    # 拉回来的依赖（rembg[cpu] 就是那个）都会**静默**顶掉 DirectML —— pip 不报错、
    # 导入也成功，只有分层的真实耗时回到 CPU。所以这里必须实测并硬失败。
    if isinstance(providers, str) and providers.startswith("FAIL"):
        sys.exit(f"❌ 随包 onnxruntime 无法枚举 provider：{providers}")
    if not isinstance(providers, list):
        sys.exit("❌ 冒烟未取到 onnxruntime provider 列表（不能假定 GPU 运行时到位）")
    print(f"    [ort ] providers = {providers}")
    if "DmlExecutionProvider" not in providers:
        sys.exit(
            "❌ 随包 onnxruntime 缺少 DmlExecutionProvider —— 分层的 GPU 加速不会生效。\n"
            "   最常见原因：某个依赖（rembg[cpu] 等）把 CPU 版 onnxruntime 装了回来，\n"
            "   两个发行名共用 onnxruntime/ 目录，后装的覆盖前装的。\n"
            f"   实测 provider：{providers}"
        )
    return {"ort_providers": providers}


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
            "⚠  当前宿主非 Windows。轮子标签已钉在随包解释器上（--platform/\n"
            "   --python-version/--abi），环境标记由 flatten_requirements() 按同一口径\n"
            "   自行求值（pip 自己不做这件事），所以**解析出来的依赖集合**与 Windows\n"
            "   宿主机一致；但第 5 步冒烟要在随包的 win_amd64 解释器里真跑，非 Windows\n"
            "   宿主只能跳过 —— 而 DmlExecutionProvider 这道 GPU 闸门正是冒烟的一部分。\n"
            "   正式出包仍须在 Windows 上装配并跑完整冒烟。")

    hashes = load_hashes()
    record: dict = {}
    dest.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    print("=" * 68)
    print(" Pygmalion Studio — 桌面运行时装配")
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
        dll = next(extract_dir.rglob("onnxruntime.dll"), None) \
            if extract_dir.is_dir() else None
        if dll is None:
            # 校验失败会删掉 dll 再退出；此时解压目录还在，必须重解压而不是报缺失。
            if extract_dir.is_dir():
                shutil.rmtree(extract_dir)
            extract_zip(ort_zip, extract_dir)
            dll = next(extract_dir.rglob("onnxruntime.dll"), None)
        if dll is None:
            sys.exit(f"❌ 在 {extract_dir} 中未找到 onnxruntime.dll")
        verify_hash(dll, hashes.get("onnxruntime-dll", ""), "onnxruntime-dll",
                    record, evict=ort_zip)
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
    smoke_info = {} if args.no_smoke_test else smoke_test(dest)

    sizes = {
        "python": dir_size(dest / "python"),
        "app": dir_size(dest / "app"),
        "models": dir_size(dest / "models"),
        "total": dir_size(dest),
    }
    info = {
        "product": "Pygmalion Studio",
        "python_embed": PYTHON_VERSION,
        "onnxruntime": ONNXRUNTIME_VERSION,
        # 随包 Python 的分层运行时 provider（装配期实测，不是声明值）。
        "ort_providers": smoke_info.get("ort_providers", []),
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
