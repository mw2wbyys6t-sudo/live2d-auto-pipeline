"""
Live2D Master Agent - Python API Server (FastAPI)
兼容 Go API 接口格式，直接调用 core 模块。

⚠️ 降级模式：本服务是 Go API（./api/live2d-api）的降级替代实现，
   仅提供只读端点与已落地的写操作，用于无 Go 编译能力的环境。
   完整功能请运行 ./api/live2d-api（Go 服务）。
   详细缺口见 docs/LIMITATIONS.md §6「API 层」。

路由状态约定：
- 完整实现 / 降级可用 → 路由保留，返回真实结构化数据；
- 仅在 Go API 才有真实实现的桩能力 → 一律回 501 Not Implemented，
  并在响应里给出 go_endpoint 指引，避免给前端造成「功能对等」的错觉。

SUPPORTED_ENDPOINTS（降级模式下可用）：
    # 基础信息（只读）
    GET  /                       根信息
    GET  /api/health             健康检查（含 python_ok）
    GET  /api/status             系统状态（服务可用性为实测结果）
    GET  /api/info               API 元信息
    GET  /api/models             可用模型列表（provider 实测可用性）
    GET  /api/expressions        表情列表（静态模板）
    GET  /api/scripts            scripts/ 目录脚本列表
    GET  /api/diagnostics        环境自检（8 项 checks，与 Go 同构）
    GET  /api/providers          图像生成上游 provider 状态
    GET  /api/models/optional    可选重模型清单 + 就绪状态
    GET  /api/generations/models output 下已导出的 Live2D 模型列表

    # 角色管理（完整 CRUD，落盘到 assets/characters/*.json）
    GET    /api/characters
    POST   /api/characters
    GET    /api/characters/{char_id}
    PUT    /api/characters/{char_id}
    DELETE /api/characters/{char_id}
    POST   /api/characters/{char_id}/references   登记参考图 + 提取 embedding
    POST   /api/characters/{char_id}/embedding    重算视觉 embedding

    # 生成 / 分层 / 导出 / 桌宠（真实调用 core、live2d_builder、drivers）
    POST /api/generate
    POST /api/generate/character
    GET  /api/generations/latest
    POST /api/segment
    POST /api/psd-plan                       # 返回静态模板，已诚实标注 applied=False
    POST /api/export/live2d                  # 支持下载 ZIP（download=true）
    POST /api/deploy/desktop
    POST /api/upload                         # 图片上传（multipart）
    POST /api/import/psd                     # 外部 PSD 逐层导入（multipart）

    # LLM 对话（含降级路径，不会静默失败）
    POST /api/chat
    POST /api/chat/stream                    # SSE

    # 静态资源与面捕（服务端面捕管线，浏览器面捕走 /preview 的 WASM）
    GET  /output/{filepath:path}
    GET  /api/preview/{filepath:path}
    POST /api/tracking/start
    POST /api/tracking/stop
    GET  /api/tracking/status
    WS   /ws/tracking
    WS   /api/ws
    GET  /ws/progress                        # 仅返回指引，引导到 /api/ws

NOT_IMPLEMENTED（仅 Go API 实现，本服务回 501）：
    POST /api/see-through
    GET  /api/export/package
    POST /api/export/psd
    GET  /api/cache/stats
    POST /api/cache/clear
    POST /api/models/optional/download
    POST /api/models/optional/cancel

启动方式:
    python api_server.py            # 默认只监听 127.0.0.1:8080
    # 确需局域网访问时显式覆盖：
    LIVE2D_API_HOST=0.0.0.0 python api_server.py
"""

import os
import sys
import json
import time
import uuid
import shutil
import asyncio
import importlib
import ipaddress
import logging
import platform
import tempfile
import zipfile
import re
import threading
from urllib.parse import urlparse
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Body, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from pydantic import BaseModel, Field

# ---------- 项目路径设置 ----------
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

# 就绪判定住在领域层（决策点在 live2d_builder）；此处只 re-export。
from live2d_builder.readiness import runtime_readiness as _runtime_readiness

OUTPUT_DIR = PROJECT_ROOT / "output"
ASSETS_DIR = PROJECT_ROOT / "assets"
CHARACTERS_DIR = ASSETS_DIR / "characters"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)

VERSION = "v0.10.8.1-py"
SERVER_PORT = 8080  # v0.10.3: 与 Go 后端及前端代理目标一致
# 安全默认：只监听本机回环（与 Go 服务同一策略），避免同 WiFi/局域网设备
# 无鉴权调用（耗 API 额度、读 output 产物、驱动摄像头）。确需局域网访问时
# 显式设置 LIVE2D_API_HOST=0.0.0.0（容器镜像已显式设置）。
SERVER_HOST = (
    os.environ.get("LIVE2D_API_HOST", "").strip()
    or os.environ.get("GO_API_HOST", "").strip()
    or "127.0.0.1"
)
START_TIME = time.time()

# 生图尺寸安全区间（与 Go 服务 ClampImageDimensions 保持一致）：
# 防止单请求申请超大画布导致进程 OOM。
MIN_IMAGE_DIMENSION = 64
MAX_IMAGE_DIMENSION = 4096


def _clamp_dimensions(req: Any) -> None:
    """就地钳制请求宽高；<=0 保留给后续默认值逻辑。"""
    w = getattr(req, "width", 0)
    h = getattr(req, "height", 0)
    if 0 < w < MIN_IMAGE_DIMENSION:
        req.width = MIN_IMAGE_DIMENSION
    elif w > MAX_IMAGE_DIMENSION:
        req.width = MAX_IMAGE_DIMENSION
    if 0 < h < MIN_IMAGE_DIMENSION:
        req.height = MIN_IMAGE_DIMENSION
    elif h > MAX_IMAGE_DIMENSION:
        req.height = MAX_IMAGE_DIMENSION


# ---------- 线程池任务超时 ----------
# 同步重活（图像 API 调用、CPU 语义分层、网格/图集烘焙）都经 asyncio.to_thread
# 在线程池执行。若不施加超时，对端永久挂起时 HTTP 请求与工作线程会一起泄漏。
# 默认预算与 Go 后端对齐（生图 600s / 分层 900s），完整工作流因串行包含
# 生图+分层+PSD+绑定+可选导出，给到 1800s。全部支持环境变量覆盖，
# 约定 0 = 不限制（保留给离线批处理等长任务场景）。
def _env_timeout(name: str, default: float) -> float:
    """读取超时秒数；空值用默认，非法值（非数字/负数）回退默认并告警。"""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
        if value < 0:
            raise ValueError
    except ValueError:
        print(f"⚠️  警告: {name}={raw!r} 不是合法超时秒数，回退默认值 {default:g}s")
        return default
    return value


GEN_TIMEOUT = _env_timeout("LIVE2D_GEN_TIMEOUT", 600)                  # 对齐 Go: PythonTimeout(120s)×5
WORKFLOW_TIMEOUT = _env_timeout("LIVE2D_WORKFLOW_TIMEOUT", 1800)       # 生图+语义分层+PSD+绑定+可选导出
SEGMENT_TIMEOUT = _env_timeout("LIVE2D_SEGMENT_TIMEOUT", 900)          # 对齐 Go bridge: 15 分钟
EXPORT_LOAD_TIMEOUT = _env_timeout("LIVE2D_EXPORT_LOAD_TIMEOUT", 120)  # 仅读盘加载 PNG
EXPORT_BUILD_TIMEOUT = _env_timeout("LIVE2D_EXPORT_BUILD_TIMEOUT", 300)  # 网格+图集烘焙+moc3
DEPLOY_TIMEOUT = _env_timeout("LIVE2D_DEPLOY_TIMEOUT", 300)            # 桌宠打包
TRACKING_START_TIMEOUT = _env_timeout("LIVE2D_TRACKING_START_TIMEOUT", 30)  # 摄像头打开（设备被占用时可能阻塞）
TRACKING_STOP_TIMEOUT = _env_timeout("LIVE2D_TRACKING_STOP_TIMEOUT", 10)    # 摄像头释放


async def _to_thread_bounded(func: Any, timeout: float, *args: Any) -> Any:
    """把同步函数丢进默认线程池执行，并施加等待超时。

    注意：Python 无法安全强杀运行中的线程，超时只放弃等待并向客户端返回，
    线程跑完后资源自然回收；关键是保证 HTTP 请求不再永久挂起。
    timeout <= 0（含约定的 0）表示不限制。
    """
    if timeout and timeout > 0:
        return await asyncio.wait_for(asyncio.to_thread(func, *args), timeout=timeout)
    return await asyncio.to_thread(func, *args)


def _timeout_message(label: str, timeout: float, env_name: str) -> str:
    return (
        f"{label}超时（限制 {timeout:g} 秒）。若任务确需更长时间，"
        f"可设置环境变量 {env_name} 调大上限（0 表示不限制）后重试。"
    )


# ---------- 响应模型 ----------
class APIResponse(BaseModel):
    success: bool = True
    message: str = ""
    data: Optional[Any] = None
    error: Optional[str] = None


class CharacterCreateRequest(BaseModel):
    name: str
    persona: Optional[Dict[str, Any]] = None
    palette: Optional[Dict[str, Any]] = None


class GenerateRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    width: int = 1024
    height: int = 1024
    seed: int = 0
    provider: str = "pollinations"


class GenerateCharacterRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    width: int = 1024
    height: int = 1024
    seed: int = 0
    character_id: str = ""
    use_semantic: bool = True
    export_live2d: bool = True
    deploy_desktop: bool = False


class ChatRequest(BaseModel):
    message: str
    character_id: str = ""
    history: List[Dict[str, str]] = []
    stream: bool = False


class ExportLive2DRequest(BaseModel):
    character_id: str = ""
    # 预留字段：当前只有 Live2D 包一种真实格式，本字段不被读取。
    format: str = "live2d-package"
    layers_dir: str = ""
    # 兼容前端两种调用方式：
    # - 工作台导出页：期望 JSON（success/model3_json/...）
    # - Live2D 预览页：download=True 时期望直接下载 ZIP 包
    model_dir: str = ""
    download: bool = False


class SegmentRequest(BaseModel):
    image_path: str = ""
    method: str = "semantic"  # semantic | kmeans


# ---------- 生命周期 ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时
    print(f"\n{'='*60}")
    print(f"  🎨 Live2D Master Agent API {VERSION} (Python Edition)")
    print(f"  高性能 FastAPI 版本 - 直接调用 core 模块")
    print(f"{'='*60}")
    print(f"  服务地址: http://{SERVER_HOST}:{SERVER_PORT}")
    if SERVER_HOST not in ("127.0.0.1", "localhost", "::1"):
        print(f"  ⚠️  监听在 {SERVER_HOST}：同一网络内的设备可不经验证访问本服务，"
              f"请确认防火墙与 LIVE2D_ALLOWED_HOSTS 配置")
    print(f"  输出目录: {OUTPUT_DIR}")
    print(f"  项目根目录: {PROJECT_ROOT}")
    print(f"{'='*60}\n")
    yield
    # 关闭时
    print("API 服务已停止")


app = FastAPI(title="Live2D Master Agent API", version=VERSION, lifespan=lifespan)

# CORS 中间件
# 严格白名单：默认只放行本地工作台（与 Go API 的 AllowedOrigins 默认值一致）。
# 绝不可默认 "*"：它与 allow_credentials=True 组合会形成任意站点携带凭据的漏洞。
# 需要放行其他来源时，用环境变量覆盖（逗号分隔）：
#   LIVE2D_CORS_ORIGINS="http://localhost:3000,https://app.example.com"
# 显式设为 "*" 时强制关闭凭据（CORS 规范要求），并记录警告。
_default_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
_env_origins = os.environ.get("LIVE2D_CORS_ORIGINS", "").strip()
if _env_origins:
    CORS_ORIGINS = [o.strip() for o in _env_origins.split(",") if o.strip()]
else:
    CORS_ORIGINS = _default_origins

# 仅在明确配置了额外可信来源时才允许携带凭据；通配符场景必须关闭凭据
if CORS_ORIGINS == ["*"]:
    print("⚠️  警告: LIVE2D_CORS_ORIGINS='*' 时已自动关闭 allow_credentials")
    _cors_allow_credentials = False
else:
    _cors_allow_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- WebSocket 安全 ----------
# 浏览器的 WebSocket 握手不受 CORS 中间件保护：恶意网页可直接
# new WebSocket("ws://127.0.0.1:8080/ws/tracking") 连本地服务，
# 窃取摄像头面捕数据或旁听任务。规则与 Go 服务一致：
# 无 Origin（curl / 桌面原生进程等非浏览器客户端）放行；有 Origin 必命中白名单。
def _ws_allowed_origin_set() -> set:
    origins = set(CORS_ORIGINS)
    # 服务自身同源（内嵌页面/独立部署由该端口提供页面）
    for host in ("localhost", "127.0.0.1"):
        origins.add(f"http://{host}:{SERVER_PORT}")
    return origins


_WS_ALLOWED_ORIGINS = _ws_allowed_origin_set()


def _ws_origin_allowed(websocket: WebSocket) -> bool:
    # 操作员显式配置 "*" 时与 CORS 行为保持一致（自行承担跨站风险）
    if CORS_ORIGINS == ["*"]:
        return True
    origin = websocket.headers.get("origin", "")
    return origin == "" or origin in _WS_ALLOWED_ORIGINS


# ---------- HTTP 安全护栏（与 Go 服务 main.go 中间件链对齐）----------
# Host 白名单：DNS rebinding 防御。恶意网页可把自己的域名解析到 127.0.0.1，
# 借浏览器对「同源」的信任直连本地服务（CORS/Origin 拦不住这种 Host 伪装）。
# 允许集合 = 回环主机名 + CORS 白名单各 Origin 的主机名 + 环境变量显式扩展。
def _build_allowed_hosts() -> set:
    hosts = {"localhost", "127.0.0.1", "::1"}
    for origin in CORS_ORIGINS:
        if origin == "*":
            continue
        hostname = urlparse(origin).hostname
        if hostname:
            hosts.add(hostname.lower().rstrip("."))
    for h in os.environ.get("LIVE2D_ALLOWED_HOSTS", "").split(","):
        h = h.strip().lower().rstrip(".")
        if h and h != "*":
            hosts.add(h)
    return hosts


_ALLOWED_HOSTS = _build_allowed_hosts()

# 远程（非回环）客户端限流：与 Go rateLimitMiddleware 同一语义——
# 本机回环豁免（桌面 UI/健康轮询/同机脚本不受限），其他来源 60 次/分，
# 0 = 不限制（离线批处理/测试）。直接取 TCP 对端 IP，不信任任何代理头。
def _env_rate_limit() -> int:
    raw = os.environ.get("LIVE2D_RATE_LIMIT_PER_MIN", "").strip()
    if raw == "":
        return 60
    try:
        val = int(raw)
        if val < 0:
            raise ValueError
        return val
    except ValueError:
        print(f"⚠️  LIVE2D_RATE_LIMIT_PER_MIN={raw!r} 非法，回退默认 60/分")
        return 60


_RATE_LIMIT_PER_MIN = _env_rate_limit()
_RATE_WINDOW_SEC = 60


class _RateLimiter:
    """按客户端 IP 的固定分钟窗口计数（线程安全；asyncio 单线程但留锁防未来迁移）。"""

    def __init__(self, limit: int, window: int = _RATE_WINDOW_SEC):
        self.limit = limit
        self.window = window
        self._hits: Dict[str, int] = {}
        self._reset: Dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self, client: Optional[str], now: Optional[float] = None) -> int:
        """返回 0 表示放行；否则返回建议的 Retry-After 秒数。"""
        if self.limit <= 0 or client is None or _is_loopback(client):
            return 0
        now = now if now is not None else time.time()
        with self._lock:
            reset_at = self._reset.get(client, 0.0)
            if now >= reset_at:
                self._hits[client] = 1
                self._reset[client] = now + self.window
                return 0
            if self._hits.get(client, 0) >= self.limit:
                return max(1, int(reset_at - now))
            self._hits[client] = self._hits.get(client, 0) + 1
            # 顺手做懒清理，避免异常来源 IP 无限堆积
            if len(self._hits) > 4096:
                expired = [k for k, t in self._reset.items() if t <= now]
                for k in expired:
                    self._hits.pop(k, None)
                    self._reset.pop(k, None)
            return 0


def _is_loopback(host_or_ip: str) -> bool:
    h = host_or_ip.strip().lower()
    if h in ("127.0.0.1", "::1", "localhost"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


_rate_limiter = _RateLimiter(_RATE_LIMIT_PER_MIN)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": "default-src 'self'",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


@app.middleware("http")
async def _security_guard(request, call_next):
    # 1) Host 白名单：畸形/空 Host 与非白名单主机名一律拒绝，不进入任何业务逻辑。
    host = (request.url.hostname or "").lower().rstrip(".")
    if not host or host not in _ALLOWED_HOSTS:
        return JSONResponse(
            {"success": False, "error": "非法的 Host 来源"}, status_code=400
        )
    # 2) 远程限流（回环豁免）。
    client = request.client.host if request.client else None
    retry_after = _rate_limiter.check(client)
    if retry_after:
        return JSONResponse(
            {"success": False, "error": "请求过于频繁，请稍后再试"},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
    # 3) 统一安全响应头（含 FileResponse/错误响应）。
    response = await call_next(request)
    for k, v in _SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    return response


# ---------- 工具函数 ----------
def ok(data: Any = None, message: str = "") -> Dict[str, Any]:
    return {"success": True, "message": message, "data": data}


def err(error: str, code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=code,
        content={"success": False, "error": error, "data": None},
    )


@app.exception_handler(RequestValidationError)
async def _on_validation_error(request, exc: RequestValidationError):
    """参数校验失败也走统一响应信封。

    FastAPI 默认回 ``{"detail": [...]}``，与本项目（以及 Go 服务的
    ``models.Response``）的 ``{success, error, data}`` 信封不一致 ——
    前端解析不到 ``error`` 字段，只能显示一句
    "Request failed: 422"，看不到具体哪个字段不对。
    """
    problems = []
    for item in jsonable_encoder(exc.errors()):
        loc = ".".join(str(p) for p in item.get("loc", ()) if p != "body")
        problems.append(f"{loc}: {item.get('msg', '')}")
    return err("参数错误: " + "；".join(problems), 422)


def _server_error(where: str, e: Exception) -> JSONResponse:
    """未预期异常统一出口（P2-4）：完整堆栈只写服务端日志，响应体给固定
    中文文案，不把异常原文（可能含绝对路径、上游 URL/Key 片段）回传客户端。
    业务校验类失败请继续使用 err() 返回具体可读提示。"""
    logging.getLogger("api_server").exception("%s 失败: %s", where, e)
    return err(f"{where}服务暂不可用，请稍后重试", 500)


def not_implemented(feature: str, go_endpoint: str = "") -> JSONResponse:
    """统一的「尚未实现」响应：501 + 明确错误，绝不返回假成功。

    降级模式（本 Python 服务）只提供只读 / 已落地的端点；凡是仅在 Go API
    模式下才有真实实现的能力，统一回 501 并指引调用方启动 Go 服务，
    避免给前端造成「202 = 功能对等」的错觉。

    feature  : 人可读的能力名（用于 message 开头，方便排障定位）。
    go_endpoint : 对应的 Go API 路由，调用方可据此直接切换目标。
    """
    return JSONResponse(
        status_code=501,
        content={
            "success": False,
            "error": "not_implemented",
            "message": (
                f"{feature}：此功能仅在 Go API 模式下可用。"
                "请启动 ./api/live2d-api 而非本 Python 降级服务。"
            ),
            "go_endpoint": go_endpoint,
            "data": None,
        },
    )


def generate_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def to_output_url(abs_path: str) -> str:
    """把 OUTPUT_DIR 下的绝对路径转换为浏览器可访问的 /output/ 相对 URL。"""
    if not abs_path:
        return ""
    try:
        rel = Path(abs_path).resolve().relative_to(OUTPUT_DIR.resolve())
        return "/output/" + rel.as_posix()
    except Exception:
        return ""


_SAFE_CHAR_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _safe_char_path(char_id: str) -> Path:
    """角色 ID -> 卡片路径；拒绝包含路径穿越字符的 ID。"""
    if not _SAFE_CHAR_ID.match(char_id or ""):
        raise ValueError(f"非法角色 ID: {char_id!r}")
    return CHARACTERS_DIR / f"{char_id}.json"


def update_character_image(char_id: str, image_url: str) -> None:
    """生成成功后把图片 URL 回写到角色记录，供角色列表展示缩略图。"""
    if not char_id or not image_url:
        return
    try:
        path = _safe_char_path(char_id)
    except ValueError:
        return
    if not path.exists():
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            char = json.load(f)
        char["image_url"] = image_url
        char["thumbnail_url"] = image_url
        char["generation_count"] = int(char.get("generation_count", 0)) + 1
        char["updated_at"] = datetime.now().isoformat()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(char, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


LATEST_GEN_FILE = OUTPUT_DIR / "latest_generation.json"


def _record_generation(result: Dict[str, Any]) -> None:
    """记录最近一次成功的生成，供前端跨页面读取（生成 → 分层 → 导出）。"""
    steps = result.get("steps") or {}
    layering = steps.get("layering") or {}
    psd = steps.get("psd") or {}
    psd_path = psd.get("psd_path", "") or ""
    record = {
        "character_id": result.get("character_id", ""),
        "image_path": result.get("character_image", ""),
        "image_url": result.get("image_url", ""),
        "layers_dir": layering.get("output_dir", ""),
        "layer_count": layering.get("layer_count", 0),
        "segmentation_method": layering.get("method", ""),
        "psd_path": psd_path,
        "psd_url": to_output_url(psd_path),
        "model3_json": result.get("model3_json", ""),
        "created_at": datetime.now().isoformat(),
    }
    try:
        with open(LATEST_GEN_FILE, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------- 运行时探测 ----------
# 契约原则：/api/status 与 /api/diagnostics 只报告**实测**结果。
#
# 早期实现把 image_generator / python_env / segment_engine / live2d_builder
# 一律硬编码成 available=True，于是「装了但跑不动」在 UI 上完全看不出来；
# /api/models 又把 seedream/sensenova 硬编码成 False，用户配了 Key 仍然
# 显示不可用。两边都是谎报，一律改为真探测。
#
# 探测要 import 模块、扫文件系统；前端会轮询 /api/status，每次都重跑会把
# 响应拖到几百毫秒。依赖在进程生命周期内不会变，缓存 30s 足够。


class _ProbeCache:
    """探测结果的 TTL 缓存（进程内，非线程安全但只存不可变快照）。"""

    def __init__(self) -> None:
        self._items: Dict[str, tuple] = {}

    def get(self, key: str, ttl: float, producer):
        now = time.monotonic()
        hit = self._items.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
        value = producer()
        self._items[key] = (time.monotonic(), value)
        return value


_PROBES = _ProbeCache()
_PROBE_TTL = 30.0


def _probe_python_env() -> Dict[str, Any]:
    """解释器与核心依赖的真实状态（对齐 Go CheckPythonEnvironment）。

    降级模式本身就是 Python 服务，所以「解释器可用」恒真；有意义的是
    核心依赖（PIL/numpy）是否齐备 —— 缺了任何一个，分层与网格都跑不动。
    """
    issues: List[str] = []
    for mod in ("PIL", "numpy"):
        try:
            importlib.import_module(mod)
        except Exception as exc:
            issues.append(f"缺少依赖: {mod}（{type(exc).__name__}）")
    return {
        "ok": not issues,
        "issues": issues,
        "version": sys.version.split()[0],
        "executable": sys.executable,
    }


def _probe_local_generator() -> Dict[str, Any]:
    """本地生成器链路是否就位（对齐 Go CheckLocalGeneratorStatus）。"""
    if not (PROJECT_ROOT / "local_image_generator.py").is_file():
        return {"ok": False, "detail": "本地生成器脚本不存在"}
    if not (PROJECT_ROOT / "core" / "workflow.py").is_file():
        return {"ok": False, "detail": "core/workflow.py 缺失"}
    return {"ok": True, "detail": "v0.10.0 工作流就绪"}


def _probe_segment_engine() -> Dict[str, Any]:
    """前景（抠图）模型是否就位。

    找不到 ONNX 权重时分层会退化为颜色启发式 —— 那是**可用但精度低**，
    不是不可用。因此 available 仍为 True，缺口写进 detail，避免把
    「降级」说成「不可用」。
    """
    try:
        from core.segment_engine import isnet_onnx
    except Exception as exc:
        return {
            "ok": False,
            "detail": f"分层引擎不可导入: {type(exc).__name__}",
            "degraded": True,
        }
    try:
        model = isnet_onnx.find_model()
    except Exception as exc:
        model = None
        detail = f"前景模型探测失败: {type(exc).__name__}"
        return {"ok": True, "detail": detail, "degraded": True}
    if model is not None:
        return {"ok": True, "detail": f"ONNX 前景模型就位: {model.name}", "degraded": False}
    return {
        "ok": True,
        "detail": "未找到 ONNX 前景模型，分层退化为颜色启发式（精度低）",
        "degraded": True,
    }


def _probe_live2d_builder() -> Dict[str, Any]:
    """Live2D 构建链路是否就位（领域层可导入即可）。"""
    try:
        from live2d_builder import pipeline as _pipeline  # noqa: F401
    except Exception as exc:
        return {"ok": False, "detail": f"live2d_builder 不可导入: {type(exc).__name__}"}
    return {"ok": True, "detail": "cubism4"}


def _probe_providers() -> Dict[str, Any]:
    """图像生成 provider 的真实可用性。

    数据源与 Go 侧 /api/providers 完全一致（同一个 ProviderRouter），
    只是省掉了 subprocess 往返。
    """
    try:
        from core.image_gen.router import ProviderRouter

        router = ProviderRouter()
        info = router.get_provider_info()
        available = router.get_available_providers()
    except Exception as exc:
        logging.getLogger("api_server").warning("provider 探测失败: %s", exc)
        return {"available": [], "registered": [], "detail": [], "query_ok": False}
    return {
        "available": available,
        "registered": [i.get("name") for i in info if i.get("name")],
        "detail": info,
        "query_ok": True,
    }


def _probe_tracker() -> Dict[str, Any]:
    """面捕组件的真实状态。

    降级模式走 Python mediapipe（不是 Go 的 Rust 原生子进程），但检查项
    的 key 必须与 Go 侧一致（tracker_exe / tracker_model / tracker_dll），
    否则前端自检页在两种后端下显示的项目数会跳变。
    """
    detail: Dict[str, Any] = {}
    try:
        import mediapipe  # noqa: F401

        detail["exe_ok"] = True
        detail["exe_detail"] = "mediapipe 面捕模块可用"
    except Exception as exc:
        detail["exe_ok"] = False
        detail["exe_detail"] = f"mediapipe 不可导入: {type(exc).__name__}"

    try:
        from drivers.face_tracker.mediapipe_tracker import _find_face_landmarker_model

        model_path = _find_face_landmarker_model()
        detail["model_ok"] = model_path is not None
        detail["model_detail"] = model_path or "未找到 face_landmarker.task（面捕不可用）"
    except Exception as exc:
        detail["model_ok"] = False
        detail["model_detail"] = f"面捕模型探测失败: {type(exc).__name__}"

    try:
        importlib.import_module("onnxruntime")

        detail["dll_ok"] = True
        detail["dll_detail"] = "onnxruntime 可用"
    except Exception as exc:
        detail["dll_ok"] = False
        detail["dll_detail"] = f"onnxruntime 不可导入: {type(exc).__name__}"
    return detail


def _probe_output_writable() -> Dict[str, Any]:
    """产物目录可写性：建临时文件再删掉（对齐 Go 的 write-probe）。"""
    issues: List[str] = []
    ok = True
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        probe = OUTPUT_DIR / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as exc:
        ok = False
        issues.append(str(exc))
    return {"ok": ok, "issues": issues}


# ---------- 基础端点 ----------
@app.get("/")
async def root():
    return ok({"version": VERSION, "name": "Live2D Master Agent API"}, "API 运行中")


@app.get("/api/health")
async def health_check():
    """健康检查。

    python_ok 是**安装验收的硬判据**：scripts/windows/verify_desktop_install.ps1
    与 tests/e2e/checklist.md 都读 data.python_ok，缺字段会被判成 FAIL。
    缺依赖时回 503 + 明确原因，避免「前端看到正常、点生成立刻崩」。
    """
    uptime = time.time() - START_TIME
    py = _PROBES.get("python_env", _PROBE_TTL, _probe_python_env)
    if not py["ok"]:
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "error": "Python 环境不可用：" + "；".join(py["issues"]),
                "data": {
                    "version": VERSION,
                    "uptime": f"{int(uptime)}s",
                    "python_ok": False,
                    "hint": "请确认已安装依赖（pip install -r requirements.txt）后重启服务",
                },
            },
        )
    return ok({
        "version": VERSION,
        "uptime": f"{int(uptime)}s",
        "python_ok": True,
    }, "Live2D API 服务正常运行")


@app.get("/api/status")
async def get_system_status():
    """系统状态：每个 service 的 available 都来自实测探测。"""
    now = datetime.now().isoformat()
    py = _PROBES.get("python_env", _PROBE_TTL, _probe_python_env)
    gen = _PROBES.get("local_generator", _PROBE_TTL, _probe_local_generator)
    seg = _PROBES.get("segment_engine", _PROBE_TTL, _probe_segment_engine)
    l2d = _PROBES.get("live2d_builder", _PROBE_TTL, _probe_live2d_builder)

    py_version = py["version"] if py["ok"] else "异常: " + "；".join(py["issues"])
    services = [
        {"name": "image_generator", "available": bool(gen["ok"]),
         "version": gen["detail"], "last_checked": now},
        {"name": "python_env", "available": bool(py["ok"]),
         "version": py_version, "last_checked": now},
        {"name": "segment_engine", "available": bool(seg["ok"]),
         "version": seg["detail"], "last_checked": now},
        {"name": "live2d_builder", "available": bool(l2d["ok"]),
         "version": l2d["detail"], "last_checked": now},
    ]
    return ok({
        "version": VERSION,
        "services": services,
        "uptime": int(time.time() - START_TIME),
    })


@app.get("/api/info")
async def get_api_info():
    """API 元信息。

    features 不再写死：每项能力按实测探测给出，语义分层降级时如实标成
    「颜色启发式分层」而不是仍宣称「语义分层」—— 元信息页写支持、实际
    跑的是另一条路，这就是又一处「假承诺」。
    """
    seg = _PROBES.get("segment_engine", _PROBE_TTL, _probe_segment_engine)
    l2d = _PROBES.get("live2d_builder", _PROBE_TTL, _probe_live2d_builder)
    providers = _PROBES.get("providers", _PROBE_TTL, _probe_providers)

    features: List[str] = []
    if providers["available"]:
        features.append("AI 图像生成")
    else:
        features.append("AI 图像生成不可用：没有可用的生成 provider")
    if seg["ok"] and not seg.get("degraded"):
        features.append("语义分层")
    elif seg["ok"]:
        features.append(f"颜色启发式分层（已降级：{seg['detail']}）")
    else:
        features.append(f"分层不可用：{seg['detail']}")
    if l2d["ok"]:
        features.append("Live2D 自动绑定")
    else:
        features.append(f"Live2D 自动绑定不可用：{l2d['detail']}")
    features.append("角色管理")

    return ok({
        "version": VERSION,
        "name": "Live2D Master Agent",
        "endpoints": {
            "health": "GET /api/health",
            "status": "GET /api/status",
            "models": "GET /api/models",
            "generate": "POST /api/generate",
            "characters": "GET /api/characters",
        },
        "features": features,
    })


@app.get("/api/models")
async def get_models():
    """可用图像模型列表。

    available 取自 ProviderRouter 的实测结果（配了 Key 的 seedream /
    sensenova 会如实变为 true），不再写死。requires_key 告诉调用方
    「缺什么才能启用」。
    """
    providers = _PROBES.get("providers", _PROBE_TTL, _probe_providers)
    detail = {d.get("name"): d for d in providers.get("detail", []) if d.get("name")}
    display = {
        "pollinations": "Pollinations (免费)",
        "seedream": "Seedream 4.0",
        "sensenova": "商汤日日新",
        "openai": "OpenAI 兼容端点",
        "local_placeholder": "本地占位图（离线兜底）",
    }
    out = []
    for pid, pname in display.items():
        info = detail.get(pid, {})
        out.append({
            "id": pid,
            "name": pname,
            "type": "image",
            "available": bool(info.get("available")),
            "requires_key": bool(info.get("requires_key")),
        })
    return ok(out)


# ---------- 环境自检 / 上游 provider / 可选重模型 ----------
def _data_root() -> Path:
    """用户数据根（与 Go config/desktop.go、scripts/optional_components.py 同源）。"""
    env = os.environ.get("LIVE2D_DATA_DIR", "").strip()
    return Path(env) if env else (Path.home() / "Live2DMasterAgent")


# 可选重模型清单：与 Go services/model_download.go 的 optionalRegistry 保持一致。
# 清单不一致的话，同一台机器上换后端会让自检页的可下载项列表变样。
_OPTIONAL_COMPONENTS = [
    {
        "name": "sam2-hiera-small",
        "url": "https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2_hiera_small.pt",
        "rel_path": "sam2/sam2_hiera_small.pt",
        "size_hint_mb": 178,
        "note": "SAM2 语义分割（高精度分层，需另装 torch/torchvision）",
        "requires_pkg": "torch,torchvision,transformers",
    },
]


def _optional_component_state(c: Dict[str, Any]) -> Dict[str, Any]:
    """单个可选重模型的状态：文件在不在就是 ready，不在就如实说明为什么拿不到。"""
    dest = _data_root() / "hf-cache" / c["rel_path"]
    ready = dest.is_file() and dest.stat().st_size > 0
    return {
        "name": c["name"],
        "url": c["url"],
        "rel_path": c["rel_path"],
        "size_hint_mb": c["size_hint_mb"],
        "note": c["note"],
        "requires_pkg": c["requires_pkg"],
        "state": "ready" if ready else "error",
        "progress": 100.0 if ready else 0.0,
        "downloaded_mb": round(dest.stat().st_size / (1 << 20), 1) if ready else 0.0,
        "total_mb": float(c["size_hint_mb"]),
        "message": "" if ready else (
            "当前是 Python 降级服务，不提供在线下载；"
            "请启动 Go API（/api/models/optional/download）后下载此可选模型。"
        ),
    }


@app.get("/api/diagnostics")
async def diagnostics():
    """环境自检：逐项返回运行时状态，全部只读，不触发任何生成。

    检查项的 key 与 Go 侧完全一致（backend / python / scripts_dir / output_dir /
    tracker_exe / tracker_model / tracker_dll / bind_loopback），否则前端自检页
    在两种后端下显示的项目数会跳变。
    """
    py = _PROBES.get("python_env", _PROBE_TTL, _probe_python_env)
    tracker = _PROBES.get("tracker", _PROBE_TTL, _probe_tracker)
    out = _PROBES.get("output_writable", _PROBE_TTL, _probe_output_writable)

    checks = [
        {"key": "backend", "label": "后端服务", "ok": True, "detail": f"{VERSION}（Python 降级模式）"},
        {"key": "python", "label": "Python 解释器", "ok": bool(py["ok"]),
         "detail": py["executable"], "issues": py["issues"]},
        {"key": "scripts_dir", "label": "工程脚本目录", "ok": PROJECT_ROOT.is_dir(),
         "detail": str(PROJECT_ROOT)},
        {"key": "output_dir", "label": "产物目录可写", "ok": bool(out["ok"]),
         "detail": str(OUTPUT_DIR), "issues": out["issues"]},
        {"key": "tracker_exe", "label": "面捕程序", "ok": bool(tracker["exe_ok"]),
         "detail": tracker["exe_detail"]},
        {"key": "tracker_model", "label": "人脸关键点模型", "ok": bool(tracker["model_ok"]),
         "detail": tracker["model_detail"]},
        {"key": "tracker_dll", "label": "ONNX Runtime 运行库", "ok": bool(tracker["dll_ok"]),
         "detail": tracker["dll_detail"]},
        {"key": "bind_loopback", "label": "仅本机回环监听", "ok": _is_loopback(SERVER_HOST),
         "detail": SERVER_HOST},
    ]
    return ok({
        "version": VERSION,
        "os": platform.system().lower(),
        "host": SERVER_HOST,
        "port": SERVER_PORT,
        "checks": checks,
        "python_path": py["executable"],
        "output_dir": str(OUTPUT_DIR),
    })


@app.get("/api/providers")
async def list_image_providers():
    """图像生成上游 provider 的真实可用性（与 Go /api/providers 同源同构）。"""
    providers = _PROBES.get("providers", _PROBE_TTL, _probe_providers)
    return ok({
        "available": providers["available"],
        "registered": providers["registered"],
        "query_ok": providers["query_ok"],
    })


@app.get("/api/models/optional")
async def list_optional_models():
    """可选重模型清单 + 当前状态（对齐 Go /api/models/optional）。"""
    return ok({"components": [_optional_component_state(c) for c in _OPTIONAL_COMPONENTS]})


@app.post("/api/models/optional/download")
async def start_optional_model(payload: Dict[str, Any] = Body(default={})):
    """在线下载重模型：仅 Go API 有实现（后台任务 + 断点续传 + SHA256）。"""
    return not_implemented("可选重模型在线下载", "/api/models/optional/download")


@app.post("/api/models/optional/cancel")
async def cancel_optional_model(payload: Dict[str, Any] = Body(default={})):
    """取消下载：仅 Go API 有实现。"""
    return not_implemented("可选重模型下载取消", "/api/models/optional/cancel")


@app.get("/api/generations/models")
async def list_exported_models():
    """列出 output 下全部已导出的 Live2D 模型（新→旧）。"""
    models = []
    try:
        for path in OUTPUT_DIR.rglob("*.model3.json"):
            try:
                stat = path.stat()
            except OSError:
                continue
            models.append({
                "model3_json": str(path),
                "output_dir": str(path.parent),
                "model3_url": to_output_url(str(path)),
                "name": path.parent.name,
                "mod_time": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
            })
    except Exception as exc:
        logging.getLogger("api_server").warning("扫描已导出模型失败: %s", exc)
    models.sort(key=lambda m: m.get("mod_time") or "", reverse=True)
    return ok({"models": models, "count": len(models)})


# ---------- 上传与外部素材导入 ----------
# 上限与白名单与 Go api/services/upload_service.go 保持一致（25MB / 5 种图片格式）。
_UPLOAD_MAX_BYTES = 25 << 20
_UPLOAD_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_PSD_MAX_BYTES = 500 << 20


def _has_multipart() -> bool:
    """python-multipart 是否可用（requirements.txt 已声明，但装漏了会让 FastAPI
    在**路由注册阶段**直接抛 RuntimeError）。"""
    try:
        import python_multipart  # noqa: F401

        return True
    except ImportError:
        pass
    try:
        import multipart  # noqa: F401

        return True
    except ImportError:
        return False


def _register_multipart_route(path: str, handler: Any) -> None:
    """注册需要 multipart 解析的路由。

    python-multipart 缺失时，FastAPI 注册带 File/Form 参数的路由会抛
    RuntimeError —— 那是**导入期**异常，会让整个服务起不来，一个可选功能
    变成全站故障。缺依赖时改为注册明确报 503 的桩，其余端点照常可用。
    """
    if _has_multipart():
        app.add_api_route(path, handler, methods=["POST"])
        return

    async def _multipart_unavailable() -> JSONResponse:
        return err(
            "该接口需要 python-multipart 依赖，请执行 pip install -r requirements.txt",
            503,
        )

    app.add_api_route(path, _multipart_unavailable, methods=["POST"])


def _sanitize_upload_name(name: str) -> str:
    """只保留文件名中的安全字符，其余折叠为下划线（对齐 Go sanitizeUploadName）。"""
    base = Path(name or "").stem.strip()
    if not base:
        base = "image"
    out = "".join(
        c if (c.isascii() and (c.isalnum() or c in "-_")) else "_" for c in base
    )
    return out[-64:] or "image"


async def upload_image(file: UploadFile = File(...)):
    """上传一张图片到 output/uploads/，返回服务器路径与 Web URL。"""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _UPLOAD_EXTS:
        return err(f"不支持的图片格式 {ext!r}（允许: png/jpg/jpeg/webp/bmp）", 400)
    data = await file.read()
    if not data:
        return err("缺少上传文件字段 file", 400)
    if len(data) > _UPLOAD_MAX_BYTES:
        return err(f"文件大小超出限制（最多 {_UPLOAD_MAX_BYTES >> 20} MB）", 400)

    dest_dir = OUTPUT_DIR / "uploads"
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return _server_error("创建上传目录", exc)
    dest = dest_dir / (
        f"upload_{int(time.time() * 1000)}_{_sanitize_upload_name(file.filename or '')}{ext}"
    )
    try:
        dest.write_bytes(data)
    except Exception as exc:
        return _server_error("保存上传文件", exc)
    return ok({"path": str(dest), "url": to_output_url(str(dest))}, "上传成功")


_register_multipart_route("/api/upload", upload_image)


def _import_psd_layers(psd_path: str, out_dir: str, preset: str) -> Dict[str, Any]:
    """外部 PSD 逐层导出为整幅画布 PNG，并按图层名识别语义部件。

    与 Go api/services/python_bridge.go 的 bridgeImportPSDScript 同逻辑：
    底层在前、前景最后绘制；画布尺寸越界直接拒绝（Live2D 网格要求各层
    共享同一画布，异尺寸会被 moc3 构建器以「画布尺寸不一致」拒绝）。
    """
    import re

    from psd_tools import PSDImage

    from core.psd.part_naming import preset_allows, recognize_part

    psd = PSDImage.open(psd_path)
    width, height = psd.width, psd.height
    if width < 64 or height < 64 or width > 8192 or height > 8192:
        raise ValueError(f"画布尺寸异常: {width}x{height}，需在 64~8192 之间")

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    leaves: List[Any] = []

    def walk(group: Any) -> None:
        for child in group:
            if child.is_group():
                walk(child)
            else:
                leaves.append(child)

    walk(psd)

    def group_path(node: Any) -> str:
        names: List[str] = []
        parent = node.parent
        while parent is not None and parent is not psd and getattr(parent, "is_group", lambda: False)():
            if parent.name:
                names.append(parent.name)
            parent = parent.parent
        return "/".join(reversed(names))

    layers: List[Dict[str, Any]] = []
    for idx, child in enumerate(reversed(leaves)):  # 底层在前，前景最后绘制
        if idx >= 500:
            break
        try:
            comp = child.composite(viewport=psd.viewbox)
        except Exception:
            continue
        if comp is None:
            continue
        comp = comp.convert("RGBA")
        if not comp.getbbox():
            continue
        raw_name = child.name or "layer"
        safe = re.sub(r"[^0-9A-Za-z_-]+", "_", raw_name)[:40] or "layer"
        layer_path = out / f"{len(layers):03d}_{safe}.png"
        comp.save(str(layer_path))
        alpha_hist = comp.getchannel("A").histogram()
        opaque = sum(alpha_hist[1:])
        # 中/英/日图层名 -> 语义部件 + 左右侧向，为下游参数绑定提供稳定目标
        named = recognize_part(raw_name)
        layers.append({
            "name": raw_name,
            "part_name": named.get("part") or safe,
            "part": named.get("part"),
            "side": named.get("side"),
            "mouth_vowel": named.get("mouth_vowel"),
            "path": str(layer_path),
            "pixel_count": opaque,
            "group": getattr(child.parent, "name", "") or "",
            "group_path": group_path(child),
        })

    kept = [layer for layer in layers if preset_allows(preset, layer["part"])]
    return {
        "ok": True,
        "canvas": [width, height],
        "layers_dir": str(out),
        "layer_count": len(kept),
        "layers": kept,
        "preset": preset,
        "total_found": len(leaves),
        "filtered_count": len(layers) - len(kept),
        "recognized_count": sum(1 for layer in kept if layer["part"]),
    }


async def import_psd(file: UploadFile = File(...), preset: str = Form("default")):
    """导入外部 PSD：提取全部像素图层为整幅画布 PNG，供分层工作台与导出直接使用。"""
    if not (file.filename or "").lower().endswith(".psd"):
        return err("仅支持 .psd 文件", 400)
    preset = (preset or "default").strip().lower()
    if preset not in ("minimal", "default", "full"):
        preset = "default"
    data = await file.read()
    if not data:
        return err("缺少上传文件字段 file", 400)
    if len(data) > _PSD_MAX_BYTES:
        return err("PSD 文件超过 500MB 限制", 400)

    upload_dir = OUTPUT_DIR / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    psd_path = upload_dir / f"psd_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.psd"
    psd_path.write_bytes(data)

    out_dir = OUTPUT_DIR / f"psd_import_{uuid.uuid4().hex[:8]}"
    try:
        # psd-tools 逐层导出在超大 PSD 上可能较慢，放到线程池执行并给足超时。
        result = await _to_thread_bounded(
            _import_psd_layers, 600, str(psd_path), str(out_dir), preset
        )
    except ValueError as exc:
        return err(str(exc), 400)
    except ImportError:
        return err("PSD 解析依赖缺失（psd-tools），请先安装依赖", 503)
    except Exception as exc:
        return _server_error("PSD 导入", exc)
    return ok(result, "PSD 导入成功")


_register_multipart_route("/api/import/psd", import_psd)


_PNG_IMPORT_MAX_FILES = 200
_PNG_IMPORT_EXTS = {".png", ".jpg", ".jpeg"}


def _import_png_set(payloads: List[bytes], names: List[str]) -> Dict[str, Any]:
    """把多张 PNG 组装成一个图层集目录。

    与 Go api/services/upload_service.go 的 ImportPNGSet 同语义：所有图层统一
    规范到**最大画布尺寸**（左上对齐）—— Live2D 网格要求各层共享同一画布，
    异尺寸直接导出会被 moc3 构建器以「画布尺寸不一致」拒绝。

    两遍扫描防内存放大：第一遍只读文件头拿最大画布（PIL 惰性打开，不解码
    像素），第二遍逐张解码→贴到同尺寸画布→写盘→释放，任一时刻内存中只有
    一张解码图。
    """
    import io

    from PIL import Image

    # ---- 第一遍：只读 header，取最大画布尺寸与安全文件名 ----
    metas: List[tuple] = []
    max_w = 0
    max_h = 0
    for payload, filename in zip(payloads, names):
        try:
            with Image.open(io.BytesIO(payload)) as img:
                width, height = img.size
        except Exception as exc:
            raise ValueError(f"文件 {filename} 无法导入: {exc}") from exc
        max_w = max(max_w, width)
        max_h = max(max_h, height)
        metas.append((Path(filename).stem, _sanitize_upload_name(filename)))

    out_dir = OUTPUT_DIR / f"png_import_{uuid.uuid4().hex[:8]}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 第二遍：逐张解码 → 贴到统一画布 → 写盘 ----
    layers: List[Dict[str, Any]] = []
    for index, ((stem, base), payload) in enumerate(zip(metas, payloads)):
        with Image.open(io.BytesIO(payload)) as img:
            canvas = Image.new("RGBA", (max_w, max_h), (0, 0, 0, 0))
            canvas.paste(img.convert("RGBA"), (0, 0))
            dest = out_dir / f"{index:03d}_{base}.png"
            canvas.save(str(dest))
            opaque = sum(canvas.getchannel("A").histogram()[1:])
        layers.append({
            "name": stem,
            "path": str(dest),
            "url": to_output_url(str(dest)),
            "order": index,
            "pixel_count": int(opaque),
        })

    return {
        "layers_dir": str(out_dir),
        "layers": layers,
        "layer_count": len(layers),
        "canvas": [max_w, max_h],
    }


async def import_pngs(files: List[UploadFile] = File(...)):
    """导入多张 PNG（按提交顺序作为图层集）。

    与 Go api/handlers/handlers.go 的 ImportPNGs 同契约：字段名是 ``files``，
    可多文件；所有图层规范到同一画布尺寸。
    """
    if not files:
        return err("缺少上传文件字段 files", 400)
    if len(files) > _PNG_IMPORT_MAX_FILES:
        return err(f"单次最多导入 {_PNG_IMPORT_MAX_FILES} 张 PNG", 400)

    payloads: List[bytes] = []
    names: List[str] = []
    total = 0
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in _PNG_IMPORT_EXTS:
            return err(f"不支持的图片格式 {ext!r}（图层导入允许: png/jpg/jpeg）", 400)
        data = await f.read()
        if not data or len(data) > _UPLOAD_MAX_BYTES:
            return err(
                f"文件 {f.filename} 大小超出限制（最多 {_UPLOAD_MAX_BYTES >> 20} MB）", 400
            )
        total += len(data)
        payloads.append(data)
        names.append(f.filename or "layer")
    # 单文件虽 ≤25MB，但 200 个文件的 multipart 信封可达数 GB，必须封顶。
    if total > _PSD_MAX_BYTES:
        return err(
            f"批量导入总量超出限制（累计最多 500MB，当前约 {total >> 20}MB）", 400
        )

    try:
        result = await _to_thread_bounded(_import_png_set, 600, payloads, names)
    except ValueError as exc:
        return err(str(exc), 400)
    except Exception as exc:
        return _server_error("PNG 导入", exc)
    return ok(result, "PNG 导入完成")


_register_multipart_route("/api/import/pngs", import_pngs)


def _extract_visual_embedding(image_path: Path) -> List[float]:
    """提取视觉 embedding（CLIP 可用时用 CLIP，否则退化为直方图）。"""
    from PIL import Image

    from core.character.embedding import EmbeddingExtractor

    with Image.open(image_path) as img:
        return EmbeddingExtractor().extract(img.convert("RGB"))


def _resolve_uploaded_image(image_path: str) -> Optional[Path]:
    """把参考图路径解析到 uploads 目录内（对齐 Go ValidateUploadedImagePath）。"""
    if not image_path:
        return None
    uploads_dir = (OUTPUT_DIR / "uploads").resolve()
    candidate = Path(image_path)
    if not candidate.is_absolute():
        candidate = OUTPUT_DIR / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(uploads_dir)
    except (ValueError, OSError, RuntimeError):
        return None
    return resolved if resolved.is_file() else None


@app.post("/api/characters/{char_id}/references")
async def add_character_reference(char_id: str, req: Dict[str, Any]):
    """为角色登记某个视角的参考图，并尽力提取视觉 embedding。

    参考图路径必须位于 uploads 目录内（与 Go ValidateUploadedImagePath 同约束），
    否则调用方可以借这个端点读取本机任意文件。
    """
    try:
        path = _safe_char_path(char_id)
    except ValueError:
        return err("非法角色 ID", 400)
    if not path.exists():
        return err(f"角色 {char_id} 不存在", 404)
    view = str(req.get("view") or "front").strip().lower()
    if view not in ("front", "side", "back"):
        return err(f"未知参考图视角: {view}（允许 front/side/back）", 400)
    resolved = _resolve_uploaded_image(str(req.get("image_path") or ""))
    if resolved is None:
        return err("图片不存在或不在 uploads 目录内，请先通过 /api/upload 上传", 400)

    with open(path, "r", encoding="utf-8") as f:
        char = json.load(f)
    references = char.get("references")
    if not isinstance(references, dict):
        references = {}
    references[view] = str(resolved)
    char["references"] = references
    char["updated_at"] = datetime.now().isoformat()

    # embedding 提取尽力而为：失败时参考图路径已在卡上，前端可稍后重算。
    embedding_extracted = False
    note = ""
    try:
        embedding = await _to_thread_bounded(_extract_visual_embedding, 120, resolved)
        char["visual_embedding"] = embedding
        embedding_extracted = True
    except Exception as exc:
        logging.getLogger("api_server").warning("参考图 embedding 提取失败: %s", exc)
        note = "参考图已登记，但 embedding 提取失败（可稍后重算）"

    _save_character(char)
    data: Dict[str, Any] = {"card": char, "embedding_extracted": embedding_extracted}
    if note:
        data["embedding_note"] = note
    return ok(data, "参考图已登记")


@app.post("/api/characters/{char_id}/embedding")
async def recompute_character_embedding(char_id: str):
    """为角色重新提取视觉 embedding，返回 {has_embedding, dim, reason?}。"""
    try:
        path = _safe_char_path(char_id)
    except ValueError:
        return err("非法角色 ID", 400)
    if not path.exists():
        return err(f"角色 {char_id} 不存在", 404)
    with open(path, "r", encoding="utf-8") as f:
        char = json.load(f)

    references = char.get("references") or {}
    image_path = ""
    for view in ("front", "side", "back"):
        if references.get(view):
            image_path = references[view]
            break
    if not image_path and char.get("image_url"):
        candidate = _resolve_uploaded_image(str(char["image_url"]))
        if candidate is not None:
            image_path = str(candidate)
    if not image_path:
        return ok({
            "has_embedding": False,
            "dim": 0,
            "reason": "角色没有任何参考图，无法提取视觉 embedding",
        })

    try:
        embedding = await _to_thread_bounded(_extract_visual_embedding, 120, Path(image_path))
    except FileNotFoundError:
        return ok({
            "has_embedding": False,
            "dim": 0,
            "reason": "参考图文件不存在，请重新上传",
        })
    except Exception as exc:
        logging.getLogger("api_server").warning("embedding 重算失败: %s", exc)
        return err("embedding 提取失败，请稍后重试", 500)

    char["visual_embedding"] = embedding
    char["updated_at"] = datetime.now().isoformat()
    _save_character(char)
    return ok({"has_embedding": True, "dim": len(embedding)}, "embedding 已更新")


@app.get("/api/expressions")
async def get_expressions(character_id: str = ""):
    expressions = [
        {"name": "neutral", "file": "neutral.exp3.json", "params": {"ParamMouthOpenY": 0, "ParamEyeLOpen": 1, "ParamEyeROpen": 1}},
        {"name": "happy", "file": "happy.exp3.json", "params": {"ParamMouthOpenY": 0.3, "ParamEyeLOpen": 0.8, "ParamEyeROpen": 0.8}},
        {"name": "angry", "file": "angry.exp3.json", "params": {"ParamBrowLY": -0.5, "ParamBrowRY": -0.5}},
        {"name": "sad", "file": "sad.exp3.json", "params": {"ParamBrowLY": 0.3, "ParamBrowRY": 0.3}},
        {"name": "surprised", "file": "surprised.exp3.json", "params": {"ParamEyeLOpen": 1.2, "ParamEyeROpen": 1.2, "ParamMouthOpenY": 0.5}},
        {"name": "shy", "file": "shy.exp3.json", "params": {"ParamCheek": 0.8}},
    ]
    return ok(expressions)


# ---------- 角色管理 ----------
def _load_characters() -> List[Dict[str, Any]]:
    characters = []
    if CHARACTERS_DIR.exists():
        for f in CHARACTERS_DIR.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    characters.append(json.load(fp))
            except Exception:
                pass
    return sorted(characters, key=lambda c: c.get("created_at", ""), reverse=True)


def _save_character(char: Dict[str, Any]):
    CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    char_id = char.get("character_id", char.get("id", ""))
    path = _safe_char_path(str(char_id))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(char, f, ensure_ascii=False, indent=2)


@app.get("/api/characters")
async def list_characters():
    chars = _load_characters()
    return ok(chars)


@app.post("/api/characters")
async def create_character(req: CharacterCreateRequest):
    char_id = generate_id("char_")
    now = datetime.now().isoformat()
    char = {
        "character_id": char_id,
        "id": char_id,
        "name": req.name,
        "persona": req.persona or {"personality": "", "backstory": ""},
        "palette": req.palette or {"primary_colors": [], "skin_tone": "#FFE4C4", "accent_color": "#FF69B4"},
        "generation_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    _save_character(char)
    return ok(char, "角色创建成功")


# PUT /api/characters 允许修改的字段白名单：
# 禁止客户端通过批量赋值覆盖 character_id/id/created_at 等系统字段。
_CHAR_EDITABLE_FIELDS = {"name", "persona", "palette", "image_url", "thumbnail_url"}


@app.get("/api/characters/{char_id}")
async def get_character(char_id: str):
    try:
        path = _safe_char_path(char_id)
    except ValueError:
        return err("非法角色 ID", 400)
    if not path.exists():
        return err(f"角色 {char_id} 不存在", 404)
    with open(path, "r", encoding="utf-8") as f:
        char = json.load(f)
    return ok(char)


@app.put("/api/characters/{char_id}")
async def update_character(char_id: str, req: Dict[str, Any]):
    try:
        path = _safe_char_path(char_id)
    except ValueError:
        return err("非法角色 ID", 400)
    if not path.exists():
        return err(f"角色 {char_id} 不存在", 404)
    with open(path, "r", encoding="utf-8") as f:
        char = json.load(f)
    for field in _CHAR_EDITABLE_FIELDS:
        if field in req:
            char[field] = req[field]
    char["updated_at"] = datetime.now().isoformat()
    _save_character(char)
    return ok(char, "角色更新成功")


@app.delete("/api/characters/{char_id}")
async def delete_character(char_id: str):
    try:
        path = _safe_char_path(char_id)
    except ValueError:
        return err("非法角色 ID", 400)
    if not path.exists():
        return err(f"角色 {char_id} 不存在", 404)
    path.unlink()
    return ok(None, "角色删除成功")


# ---------- 图像生成 ----------
def _run_generate(req: "GenerateRequest") -> Dict[str, Any]:
    """同步执行单图生成（在线程池中调用，避免阻塞事件循环）。"""
    from core.image_gen.router import get_router
    # M6：用 uuid 而非秒级时间戳命名，并发请求落在同一秒时不会互相覆盖产物。
    output_path = str(OUTPUT_DIR / f"{generate_id('generated_')}.png")
    result = get_router().generate(
        prompt=req.prompt,
        output_path=output_path,
        negative_prompt=req.negative_prompt or None,
        width=req.width,
        height=req.height,
        seed=req.seed or None,
        provider=req.provider or None,
    )
    return {
        "image_path": result.image_path,
        "image_url": to_output_url(result.image_path),
        "provider": result.provider,
        "model": result.model,
        "prompt": result.prompt,
        "width": result.width,
        "height": result.height,
        "seed": req.seed,
        "created_at": datetime.now().isoformat(),
    }


@app.post("/api/generate")
async def generate_image(req: GenerateRequest):
    try:
        _clamp_dimensions(req)  # 边界处钳制，任何下游调用路径都绕不过
        data = await _to_thread_bounded(_run_generate, GEN_TIMEOUT, req)
        return ok(data, "图像生成成功")
    except asyncio.TimeoutError:
        return err(_timeout_message("图像生成", GEN_TIMEOUT, "LIVE2D_GEN_TIMEOUT"), 504)
    except Exception as e:
        return _server_error("图像生成", e)


def _run_generate_character(req: "GenerateCharacterRequest") -> Dict[str, Any]:
    """同步执行完整角色工作流（在线程池中调用，避免阻塞事件循环）。"""
    from core.workflow import WorkflowEngine
    engine = WorkflowEngine(
        output_dir=str(OUTPUT_DIR),
        provider=None,  # None = 自动选择 (seedream → sensenova → pollinations)
        width=req.width,
        height=req.height,
        character_consistency=req.character_id or None,
        use_semantic_segmentation=req.use_semantic,
        export_live2d=req.export_live2d,
    )
    result = engine.run(
        prompt=req.prompt,
        negative_prompt=req.negative_prompt or None,
        seed=req.seed or None,
        deploy_desktop=req.deploy_desktop,
    )
    # 把 rigging 阶段的 model3.json 绝对路径提升为顶层可访问 URL
    rigging = (result.get("steps") or {}).get("rigging") or {}
    model3_abs = rigging.get("model3_json") or ""
    if model3_abs and not result.get("model3_json"):
        result["model3_json"] = to_output_url(model3_abs)
    if result.get("success"):
        image_url = to_output_url(result.get("character_image", ""))
        result["image_url"] = image_url
        char_id = result.get("character_id") or req.character_id
        if char_id:
            result["character_id"] = char_id
            update_character_image(char_id, image_url)
        _record_generation(result)
    else:
        # 工作流可能在分层/PSD 阶段失败，但图已经生成——仍返回图片供预览
        gen_path = result.get("character_image")
        if not gen_path:
            gen_step = (result.get("steps") or {}).get("generate") or {}
            gen_path = gen_step.get("path")
        image_url = to_output_url(gen_path or "")
        if image_url:
            result["image_url"] = image_url
            if req.character_id:
                update_character_image(req.character_id, image_url)
    return result


@app.post("/api/generate/character")
async def generate_character(req: GenerateCharacterRequest):
    try:
        _clamp_dimensions(req)  # 边界处钳制，与 /api/generate 同一条信任边界
        result = await _to_thread_bounded(_run_generate_character, WORKFLOW_TIMEOUT, req)
        return ok(result, "角色生成完成" if result.get("success") else "角色生成失败")
    except asyncio.TimeoutError:
        return err(
            _timeout_message("角色生成工作流", WORKFLOW_TIMEOUT, "LIVE2D_WORKFLOW_TIMEOUT"),
            504,
        )
    except Exception as e:
        return _server_error("角色生成", e)


@app.get("/api/generations/latest")
async def latest_generation():
    """返回最近一次成功生成的产物，供前端跨页面接力使用。"""
    if not LATEST_GEN_FILE.exists():
        return err("尚未有任何生成记录", 404)
    try:
        with open(LATEST_GEN_FILE, "r", encoding="utf-8") as f:
            return ok(json.load(f))
    except Exception as e:
        return _server_error("读取最近生成记录", e)


class ClientRequestError(Exception):
    """请求方错误（参数/路径不合法）。消息本身不含内部路径，可安全回显，
    端点统一映射为 400——与 Go 侧 ErrPathOutsideBase → 400 的契约对齐，
    避免把客户端错误误报成 500（也避免 500 脱敏吞掉可读的业务提示）。"""


def _resolve_segment_source(src: str) -> Path:
    """把分层请求的源图路径限定在输出根内并确认是普通文件。

    与 Go 桥接 validateWithinBase(imagePath, Output.BaseDir) 同一信任锚：
    分层引擎会把源图原样另存为 layers_*/preview.png，而产物可经 /output/
    下载——若接受根之外的绝对路径，等于把磁盘上任意可读图片复制进可下载
    目录（本地文件外传）。相对路径按 OUTPUT_DIR 解析（生成/上传产物都在此根下）。
    """
    if not src:
        raise ClientRequestError("找不到可用的源图片，请先生成角色图")
    p = Path(src)
    if not p.is_absolute():
        p = OUTPUT_DIR / p
    if not _within_output_root(p):
        raise ClientRequestError("源图片必须位于输出目录内")
    if not p.is_file():
        raise ClientRequestError("找不到可用的源图片，请先生成角色图")
    return p


def _run_segment(req: "SegmentRequest") -> Dict[str, Any]:
    """对指定图片执行分层并导出 PSD（在线程池中调用）。"""
    from PIL import Image
    from core.psd.creator import PSDCreator

    src = req.image_path
    if not src and LATEST_GEN_FILE.exists():
        try:
            with open(LATEST_GEN_FILE, "r", encoding="utf-8") as f:
                src = json.load(f).get("image_path", "")
        except Exception:
            src = ""
    # latest_generation.json 记录的路径同样不可信（文件可被外部修改），
    # 统一走输出根归属校验。
    path = _resolve_segment_source(src)

    img = Image.open(path).convert("RGBA")
    # M6：uuid 命名，并发分层不再共享目录导致图层互相串台。
    out_dir = OUTPUT_DIR / generate_id("layers_")

    if req.method == "kmeans":
        from core.segment_engine.kmeans import KMeansLayerer
        result = KMeansLayerer().layer(img, output_dir=str(out_dir))
    else:
        from core.segment_engine.semantic import SemanticSegmenter
        result = SemanticSegmenter(model_type="auto").layer(img, output_dir=str(out_dir))

    names = [l.get("name") for l in (result.get("layers") or []) if l.get("name")]
    psd_result = PSDCreator().create_psd(
        str(out_dir), str(out_dir / "character.psd"), ordered_names=names or None
    )

    from core.segment_engine.layer_manifest import (
        build_manifest,
        source_for_method,
        write_manifest,
    )

    # source 必须如实反映实际使用的分层方法（含降级状态），否则用户会把
    # 「颜色启发式兜底」误当成可用语义分层。
    source = source_for_method(result.get("method", req.method))
    manifest = build_manifest(
        result.get("layers") or [],
        source=source,
        canvas=(img.width, img.height),
    )
    manifest_path = write_manifest(str(out_dir), manifest)

    layers = [{
        "name": l.get("name", ""),
        "part_name": l.get("part_name", l.get("name", "")),
        "url": to_output_url(l.get("path", "")),
        "pixel_count": l.get("pixel_count", 0),
    } for l in (result.get("layers") or [])]

    return {
        "method": result.get("method", req.method),
        "layers_dir": str(out_dir),
        "layer_count": len(layers),
        "layers": layers,
        "composite_preview": to_output_url(result.get("composite_preview", "") or ""),
        "source_image_url": to_output_url(str(path)),
        "psd_path": psd_result.get("psd_path", "") or "",
        "psd_url": to_output_url(psd_result.get("psd_path", "") or ""),
        "psd_success": bool(psd_result.get("success")),
        "manifest_path": manifest_path,
        "manifest_url": to_output_url(manifest_path),
        "manifest_version": manifest.schema_version,
        "source": manifest.source,
        "rig_ready": manifest.rig_ready,
        "preview_ready": manifest.preview_ready,
        "missing_required": manifest.missing_required,
        "warnings": manifest.warnings,
    }


@app.post("/api/segment")
async def segment_image(req: SegmentRequest):
    """对图片运行真实分层并写出 PSD，供「分层工作台」使用。"""
    try:
        data = await _to_thread_bounded(_run_segment, SEGMENT_TIMEOUT, req)
        return ok(data, "分层完成")
    except ClientRequestError as e:
        # 源图路径越界/缺失等请求方错误：文案安全，回 400（与 Go 侧一致）
        return err(str(e), 400)
    except asyncio.TimeoutError:
        return err(_timeout_message("分层", SEGMENT_TIMEOUT, "LIVE2D_SEGMENT_TIMEOUT"), 504)
    except Exception as e:
        return _server_error("图像分层", e)


# ---------- PSD / 分层 ----------
@app.post("/api/psd-plan")
async def create_psd_plan():
    # 返回的是标准分层模板（静态预设），不代表已执行任何处理
    return ok(
        {
            "layers": 18,
            "plan": "standard_v10",
            "kind": "template",
            "applied": False,
        },
        "已返回标准分层模板（未执行处理）",
    )


@app.post("/api/see-through")
async def run_see_through():
    return not_implemented("See-through 抠图", go_endpoint="/api/see-through")


# ---------- 导出 ----------
def _within_output_root(p: Path) -> bool:
    """目录 resolve 后是否仍位于配置的输出根（OUTPUT_DIR）内。

    与 Go 服务 validateWithinBase(target, Output.BaseDir) 同一信任锚、同一套
    规范化 + 前缀检查：图层/模型目录只允许来自应用自己生成产物的根目录
    （分层端点生成的 layers_*、latest_generation.json 记录的路径都在此根下），
    阻止绝对路径穿越读取或打包任意位置的 PNG。
    """
    try:
        p.resolve().relative_to(OUTPUT_DIR.resolve())
        return True
    except (ValueError, OSError):
        return False


def _resolve_export_source(req: "ExportLive2DRequest") -> str:
    """定位导出所用的图层来源目录。

    优先级：显式 layers_dir → model_dir → OUTPUT_DIR 下最新的 layers_* 目录。
    相对路径按输出根 OUTPUT_DIR 解析；所有外部传入路径必须解析后仍位于
    OUTPUT_DIR 内，防止绝对路径穿越读取任意 PNG。
    """
    for candidate in (req.layers_dir, req.model_dir):
        if candidate:
            p = Path(candidate)
            if not p.is_absolute():
                p = OUTPUT_DIR / p
            if _within_output_root(p) and p.is_dir() and any(p.glob("*.png")):
                return str(p)
    for d in sorted(OUTPUT_DIR.glob("layers_*"), key=lambda x: x.stat().st_mtime, reverse=True):
        if d.is_dir() and any(d.glob("*.png")):
            return str(d)
    return ""


def _load_layers_for_export(layers_dir: str) -> "Dict[str, Any]":
    """把目录下的 PNG 图层读成 {名称: PIL.Image}，供 Live2DBuilder 使用。

    收敛说明：剔预览 + draw_index 排序的唯一实现住在
    live2d_builder.layer_loader —— 早前这里内联 ``sorted(base.glob("*.png"))``，
    会把整图预览（满画布不透明）当成图层烘进 moc3，并按字母序喂层
    （口腔盖住嘴唇）。与本模块的 Go 导出桥共用同一份，不再各自内联。
    """
    from live2d_builder.layer_loader import load_layers_for_export

    return dict(load_layers_for_export(layers_dir))


def _build_live2d_model(layers: "Dict[str, Any]", output_dir: str, name: str) -> Dict[str, Any]:
    """同步构建 Live2D 模型包（在线程池中调用）。"""
    from live2d_builder.pipeline import Live2DBuilder

    builder = Live2DBuilder(output_dir=output_dir, character_name=name or "character")
    return builder.build(layers)


def runtime_readiness(result: Dict[str, Any]) -> Dict[str, Any]:
    """导出包能否被 Live2D 运行时直接加载。

    实现已下沉到 ``live2d_builder.readiness``（领域层才是这条契约的决策点）。
    这里保留同名 re-export，使 ``from api_server import runtime_readiness`` 的既有
    调用方与测试无需改动。
    """
    return _runtime_readiness(result)


# ---------- M6：导出产物的唯一命名与陈旧清理 ----------
# 旧导出目录保留 24h：既给预览页留足回看窗口，又避免无限堆积占满磁盘。
EXPORT_ARTIFACT_TTL_SEC = 24 * 3600
_EXPORT_SWEEP_LOCK = threading.Lock()


def _safe_unlink(path: Path) -> None:
    """发送后删除临时 zip；任何失败都不影响响应。"""
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def _cleanup_old_export_artifacts() -> None:
    """尽力清理 TTL 过期的 export_* 目录与系统临时目录中残留的下载 zip。

    崩溃中断的下载会让临时 zip 泄漏，这里顺带扫；全程吞异常，清理永远不能
    拖垮导出主链路。并发导出时用锁避免重复扫盘。
    """
    if not _EXPORT_SWEEP_LOCK.acquire(blocking=False):
        return
    try:
        cutoff = time.time() - EXPORT_ARTIFACT_TTL_SEC
        try:
            for p in OUTPUT_DIR.glob("export_*"):
                try:
                    if p.is_dir() and p.stat().st_mtime < cutoff:
                        shutil.rmtree(p, ignore_errors=True)
                except Exception:
                    continue
            # 旧版本把下载 zip 直接写在输出根，遗留物同样按 TTL 清。
            for p in OUTPUT_DIR.glob("live2d_*.zip"):
                try:
                    if p.is_file() and p.stat().st_mtime < cutoff:
                        p.unlink()
                except Exception:
                    continue
        except Exception:
            pass
        try:
            tmp_dir = Path(tempfile.gettempdir())
            for p in tmp_dir.glob("live2d_*.zip"):
                try:
                    if p.is_file() and p.stat().st_mtime < cutoff:
                        p.unlink()
                except Exception:
                    continue
        except Exception:
            pass
    finally:
        _EXPORT_SWEEP_LOCK.release()


@app.post("/api/export/live2d")
async def export_live2d(req: ExportLive2DRequest, background_tasks: BackgroundTasks):
    try:
        source_dir = _resolve_export_source(req)
        if not source_dir:
            payload = {"success": False, "message": "未找到可导出的图层目录"}
            return ok(payload, "导出失败：缺少图层")

        # character_id 会进入烘焙纹理文件名，必须先过白名单（防穿越写）。
        if req.character_id and not _SAFE_CHAR_ID.match(req.character_id):
            return err("非法角色 ID", 400)

        try:
            layers = await _to_thread_bounded(
                _load_layers_for_export, EXPORT_LOAD_TIMEOUT, source_dir
            )
        except asyncio.TimeoutError:
            return err(
                _timeout_message("图层加载", EXPORT_LOAD_TIMEOUT, "LIVE2D_EXPORT_LOAD_TIMEOUT"),
                504,
            )
        if not layers:
            payload = {"success": False, "message": f"图层目录内没有可用 PNG：{source_dir}"}
            return ok(payload, "导出失败：图层为空")

        # M6：uuid 命名导出目录，并发导出互不覆盖；顺手触发一次陈旧产物清理。
        export_dir = OUTPUT_DIR / generate_id("export_")
        export_dir.mkdir(parents=True, exist_ok=True)
        try:
            await asyncio.to_thread(_cleanup_old_export_artifacts)
        except Exception:
            pass
        try:
            result = await _to_thread_bounded(
                _build_live2d_model,
                EXPORT_BUILD_TIMEOUT,
                layers,
                str(export_dir),
                req.character_id,
            )
        except asyncio.TimeoutError:
            return err(
                _timeout_message("Live2D 导出", EXPORT_BUILD_TIMEOUT, "LIVE2D_EXPORT_BUILD_TIMEOUT"),
                504,
            )

        model3 = result.get("model3_json", "") or ""
        textures = [t for t in (result.get("textures") or []) if t]
        readiness = runtime_readiness(result)
        ready = readiness["runtime_ready"]

        # FF-15：产出物必须内含许可证全文（ADR-001 Rev.4）。缺了它，用户把
        # 模型包再分发出去时就没有任何许可声明 —— 这是合规缺口，不是装饰。
        # 真正的真源在 core/license_bundle.py，Go 导出桥用的是同一份。
        try:
            from core.license_bundle import install_license_bundle

            install_license_bundle(export_dir)
        except Exception as exc:
            logging.getLogger("api_server").warning("产出物许可文件写入失败: %s", exc)

        # Live2D 预览页需要直接下载 ZIP 包。
        # M6：zip 写在系统临时目录（不落在可被 /output/ 直接访问的输出根），
        # 用 mkstemp 拿到无碰撞路径；响应发送完成后立即删除，崩溃残留由
        # _cleanup_old_export_artifacts 按 TTL 兜底回收。
        if req.download:
            # 打包闸门：被官方 Cubism Core 拒绝的产物不许进 zip。
            # 早先这条分支只负责打包，全程不看验收结论 —— 用户拿到一个装进
            # Cubism / VTube Studio 才发现加载不了的包，且不知道原因。
            if not ready:
                return err(
                    f"模型未通过官方 Cubism Core 验收，拒绝打包：{readiness['blocker']}",
                    409,
                )
            fd, tmp_name = tempfile.mkstemp(prefix="live2d_", suffix=".zip")
            os.close(fd)
            zip_path = Path(tmp_name)
            try:
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in export_dir.rglob("*"):
                        if f.is_file():
                            zf.write(f, f.relative_to(export_dir))
            except Exception:
                _safe_unlink(zip_path)
                raise
            download_name = f"live2d_{generate_id()}.zip"
            background_tasks.add_task(_safe_unlink, zip_path)
            return FileResponse(
                str(zip_path), media_type="application/zip", filename=download_name
            )

        # success 必须与 runtime_ready 一致：同一个 payload 里出现
        # success=true 与 runtime_ready=false 会骗过只读 success 的消费者。
        return ok({
            "success": ready,
            "blocker": readiness["blocker"],
            "model3_json": to_output_url(model3),
            "model_path": to_output_url(model3),
            "texture": to_output_url(textures[0]) if textures else "",
            "textures": [to_output_url(t) for t in textures],
            "physics": to_output_url(result.get("physics", "") or ""),
            "expressions": result.get("expressions", []),
            "validation": result.get("validation", {}),
            "output_dir": str(export_dir),
            "elapsed_seconds": result.get("elapsed_seconds"),
            **readiness,
        }, "Live2D 模型导出成功" if ready else "模型已生成，但未通过官方内核验收，不可部署")
    except Exception as e:
        # 未预期构建错误：详情（含临时目录/路径）只进日志，前端拿固定文案。
        # 业务性失败（如「缺少图层」）在 try 内部已提前 return，不会走到这里。
        logging.getLogger("api_server").exception("Live2D 导出失败: %s", e)
        return ok({
            "success": False,
            "model3_url": "",
            "model_path": "",
            "message": "导出服务暂不可用，请稍后重试",
        }, "导出失败，请稍后重试")


@app.get("/api/export/package")
async def export_model_zip(character_id: str = ""):
    return not_implemented(
        "打包下载整个 Live2D 模型（请改用 /api/export/live2d 并传 download=true）",
        go_endpoint="/api/export/live2d",
    )


@app.post("/api/export/psd")
async def export_psd():
    return not_implemented(
        "PSD 导出（可通过 /api/export/live2d 获取模型包）",
        go_endpoint="/api/export/psd",
    )


def _run_deploy_desktop(layers_dir: str) -> Dict[str, Any]:
    """用真实分层图层生成自包含桌宠包（在线程池中调用）。"""
    from drivers.desktop_pet.animator import DesktopPetAnimator

    animator = DesktopPetAnimator(layers_dir)
    animator.load_layers()
    out = OUTPUT_DIR / "pet_packages"
    return animator.create_pet_package(str(out))


class DeployDesktopRequest(BaseModel):
    layers_dir: str = ""  # 为空时用最近一次生成的图层目录


@app.post("/api/deploy/desktop")
async def deploy_desktop(req: DeployDesktopRequest):
    """从分层图层生成可独立运行的桌宠包（含 run_pet.bat）。"""
    try:
        layers_dir = req.layers_dir
        if not layers_dir:
            # 回落到最近一次生成的图层目录
            if LATEST_GEN_FILE.exists():
                with open(LATEST_GEN_FILE, "r", encoding="utf-8") as f:
                    layers_dir = json.load(f).get("layers_dir", "")
        if not layers_dir or not Path(layers_dir).is_dir():
            return err("未找到可用图层目录，请先生成角色", 400)
        # 目录必须位于输出根内（与导出端点同一信任锚），防止指向外部路径打包任意 PNG。
        candidate = Path(layers_dir)
        if not candidate.is_absolute():
            candidate = OUTPUT_DIR / candidate
        if not _within_output_root(candidate):
            return err("图层目录必须位于输出目录内", 400)

        result = await _to_thread_bounded(_run_deploy_desktop, DEPLOY_TIMEOUT, str(candidate))
        if not result.get("success"):
            # 打包器内部失败原因只记日志，响应给固定文案，避免内部路径外泄
            logging.getLogger("api_server").error(
                "桌宠打包失败: %s", result.get("error", "")
            )
            return err("桌宠打包服务暂不可用，请稍后重试", 500)
        pkg_dir = result.get("package_dir", "")
        return ok({
            "success": True,
            "package_dir": pkg_dir,
            "run_script": str(Path(pkg_dir) / "run_pet.bat"),
            "readme": str(Path(pkg_dir) / "README.txt"),
        }, "桌宠包已生成")
    except asyncio.TimeoutError:
        return err(_timeout_message("桌宠打包", DEPLOY_TIMEOUT, "LIVE2D_DEPLOY_TIMEOUT"), 504)
    except Exception as e:
        return _server_error("桌宠部署", e)


# ---------- LLM 对话 ----------
def _make_chat_session(character_id: str = ""):
    """构造 ChatSession（LLMRouter 必须显式注入），并恢复历史与角色人设。"""
    from llm_bridge.providers.router import LLMRouter
    from llm_bridge.chat_session import ChatSession

    persona = "活泼可爱，有点傲娇，喜欢和主人聊天"
    name = "小奈"
    if character_id:
        # character_id 来自请求 body，必须过白名单再拼路径（防绝对路径/穿越读）。
        try:
            path = _safe_char_path(character_id)
        except ValueError:
            path = None
        if path is not None and path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    char = json.load(f)
                p = char.get("persona") or {}
                persona = p.get("personality") or p.get("backstory") or persona
                name = char.get("name") or name
            except Exception:
                pass
    return ChatSession(LLMRouter(), character_persona=persona, character_name=name)


def _seed_history(session, history: "List[Dict[str, str]]") -> None:
    for h in history or []:
        role = (h or {}).get("role", "")
        content = (h or {}).get("content", "")
        if role in ("user", "assistant") and content:
            session.add_context(role, content)


@app.post("/api/chat")
async def chat(req: ChatRequest):
    try:
        session = _make_chat_session(req.character_id)
        _seed_history(session, req.history)
        parts: List[str] = []
        emotion = "neutral"
        async for chunk in session.send_message(req.message):
            ctype = chunk.get("type")
            if ctype == "text":
                parts.append(chunk.get("text", ""))
            elif ctype == "emotion":
                emotion = chunk.get("emotion", emotion)
            elif ctype == "error":
                raise RuntimeError(chunk.get("error", "对话出错"))
        reply = "".join(parts).strip()
        if not reply:
            raise RuntimeError("模型未返回内容")
        return ok({"reply": reply, "emotion": emotion}, "对话完成")
    except Exception as e:
        # 上游错误原文（可能含内部 URL/Key 片段）只进日志，回复用固定文案
        logging.getLogger("api_server").exception("对话失败: %s", e)
        return ok({
            "reply": f"（对话功能暂不可用，请稍后再试）\n\n我听到你说：「{req.message}」",
            "emotion": "neutral",
        }, "对话功能降级模式")


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    """SSE 流式对话接口"""
    async def generate():
        try:
            session = _make_chat_session(req.character_id)
            _seed_history(session, req.history)
            async for chunk in session.send_message(req.message):
                ctype = chunk.get("type")
                if ctype == "text":
                    text = chunk.get("text", "")
                    if text:
                        yield f"data: {json.dumps({'chunk': text, 'type': 'text'})}\n\n"
                elif ctype == "emotion":
                    yield f"data: {json.dumps({'type': 'emotion', 'emotion': chunk.get('emotion'), 'expression': chunk.get('expression')})}\n\n"
                elif ctype == "error":
                    raise RuntimeError(chunk.get("error", "对话出错"))
            yield f"data: {json.dumps({'finished': True, 'type': 'done'})}\n\n"
        except Exception as e:
            # 降级模式：逐字返回。异常详情只进日志，不拼进给用户的文本。
            logging.getLogger("api_server").exception("流式对话失败: %s", e)
            text = f"（对话功能暂不可用，请稍后再试）\n\n我听到你说：「{req.message}」"
            for char in text:
                yield f"data: {json.dumps({'chunk': char, 'type': 'text'})}\n\n"
                await asyncio.sleep(0.03)
            yield f"data: {json.dumps({'finished': True, 'type': 'done'})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------- 静态文件服务 ----------
def _safe_resolve(base: Path, rel: str) -> Optional[Path]:
    """在 base 目录内安全解析相对路径；越界（路径穿越）返回 None。"""
    try:
        path = (base / rel).resolve()
        path.relative_to(base.resolve())
        return path
    except (ValueError, OSError, RuntimeError):
        return None


@app.get("/output/{filepath:path}")
async def serve_output(filepath: str):
    path = _safe_resolve(OUTPUT_DIR, filepath)
    if path is None:
        return err("非法访问路径", 403)
    if not path.is_file():
        return err("文件不存在", 404)
    return FileResponse(str(path))


@app.get("/api/preview/{filepath:path}")
async def serve_preview(filepath: str):
    # 依次在 output / assets 白名单目录内查找，均做路径穿越校验
    for base in (OUTPUT_DIR, ASSETS_DIR):
        path = _safe_resolve(base, filepath)
        if path and path.is_file():
            return FileResponse(str(path))
    if _safe_resolve(OUTPUT_DIR, filepath) is None and _safe_resolve(ASSETS_DIR, filepath) is None:
        return err("非法访问路径", 403)
    return err("文件不存在", 404)


# ---------- WebSocket ----------
# ---------- 面捕追踪 ----------
class _TrackerState:
    """面捕追踪器全局状态：单例 + 后台线程 + 最新参数缓存。

    FaceTracker 的摄像头读取是阻塞调用，放在独立线程里跑；最新一帧的
    Live2D 参数存在 shared dict 中，由 WS 端点按固定频率推送给浏览器。
    """

    tracker = None
    thread = None
    running = False
    lock = None  # threading.Lock，延迟初始化
    params: Dict[str, float] = {}
    blendshapes: Dict[str, float] = {}
    head_rotation: Dict[str, float] = {}
    error: str = ""

    # 面捕 WS 连接计数：面捕参数是本机摄像头的高频推流，必须限制并发连接数，
    # 防止恶意页面开大量 WS 把事件循环与带宽占满。
    WS_CLIENT_LIMIT = 10
    _ws_clients_lock = threading.Lock()
    ws_clients = 0

    @classmethod
    def acquire_ws_slot(cls) -> bool:
        with cls._ws_clients_lock:
            if cls.ws_clients >= cls.WS_CLIENT_LIMIT:
                return False
            cls.ws_clients += 1
            return True

    @classmethod
    def release_ws_slot(cls) -> None:
        with cls._ws_clients_lock:
            cls.ws_clients = max(0, cls.ws_clients - 1)

    @classmethod
    def reset(cls):
        cls.tracker = None
        cls.thread = None
        cls.running = False
        cls.params = {}
        cls.blendshapes = {}
        cls.head_rotation = {}
        cls.error = ""


def _tracking_loop() -> None:
    """后台线程：读取摄像头帧 → blendshapes → Live2D 参数。"""
    from drivers.face_tracker.blendshape_mapper import BlendShapeMapper

    mapper = BlendShapeMapper()
    while _TrackerState.running:
        try:
            tr = _TrackerState.tracker
            if tr is None:
                break
            blends = tr.get_blendshapes() or {}
            rot = tr.get_head_rotation() or {}
            params = mapper.map_to_live2d_params(blends, None)
            if rot:
                params["ParamAngleX"] = float(rot.get("yaw", 0))
                params["ParamAngleY"] = float(-rot.get("pitch", 0))
                params["ParamAngleZ"] = float(rot.get("roll", 0))
            if _TrackerState.lock:
                with _TrackerState.lock:
                    _TrackerState.params = params
                    # 原始 blendshapes / 头旋转一并留存：Go 侧 native_bridge
                    # 推的 tracking_frame 里也带这两项，前端按同一协议解析。
                    _TrackerState.blendshapes = dict(blends)
                    _TrackerState.head_rotation = dict(rot)
            time.sleep(1 / 30)
        except Exception as e:
            _TrackerState.error = str(e)
            time.sleep(0.5)


@app.get("/api/tracking/status")
async def tracking_status():
    """面捕运行状态（对齐 Go /api/tracking/status）。

    降级模式没有 Rust 原生子进程，source 标为 mediapipe；smoothing 取
    BlendShapeMapper 的默认平滑系数，deadzone 恒为 0（Python 侧未实现死区）。
    """
    tracker = _PROBES.get("tracker", _PROBE_TTL, _probe_tracker)
    return ok({
        "running": bool(_TrackerState.running),
        "available": bool(tracker["exe_ok"] and tracker["model_ok"]),
        "enabled": True,
        "source": "mediapipe",
        "camera_id": 0,
        "smoothing": 0.5,
        "deadzone": 0,
    })


@app.post("/api/tracking/start")
async def tracking_start():
    """启动摄像头面捕（mediapipe），参数经 /ws/tracking 推送。"""
    if _TrackerState.running:
        return ok({"running": True}, "面捕已在运行")
    try:
        from drivers.face_tracker.mediapipe_tracker import FaceTracker
    except Exception as e:
        # 模块缺失/加载失败的原文（含安装路径）只进日志
        logging.getLogger("api_server").exception("面捕模块加载失败: %s", e)
        return err("面捕模块当前不可用，请检查依赖安装", 500)

    def _open():
        from drivers.face_tracker.mediapipe_tracker import FaceTracker
        t = FaceTracker()
        t.start()
        return t

    try:
        tracker = await _to_thread_bounded(_open, TRACKING_START_TIMEOUT)
    except asyncio.TimeoutError:
        return err(
            _timeout_message("摄像头启动", TRACKING_START_TIMEOUT, "LIVE2D_TRACKING_START_TIMEOUT")
            + "请检查摄像头是否被其他程序占用。",
            504,
        )
    except Exception as e:
        return _server_error("摄像头启动", e)

    _TrackerState.tracker = tracker
    # 诚实性修复：mediapipe/摄像头缺失时 FaceTracker.start() 会静默返回，
    # 旧实现无条件把 running 置 true，导致前端「假报已启动」。
    if not getattr(tracker, "is_running", lambda: False)():
        try:
            tracker.stop()
        except Exception:
            pass
        return err(
            "面捕未能启动：摄像头未打开，或 mediapipe/cv2 依赖缺失。"
            "请检查摄像头是否被占用并确认依赖已安装后重试。",
            503,
        )
    _TrackerState.lock = threading.Lock()
    _TrackerState.running = True
    _TrackerState.error = ""
    _TrackerState.thread = threading.Thread(target=_tracking_loop, daemon=True)
    _TrackerState.thread.start()
    return ok({"running": True}, "面捕已启动")


@app.post("/api/tracking/stop")
async def tracking_stop():
    """停止面捕并释放摄像头。"""
    _TrackerState.running = False
    if _TrackerState.tracker is not None:
        try:
            # 释放是 best-effort：超时/异常都继续清理状态，避免 stop 接口本身挂死。
            await _to_thread_bounded(
                _TrackerState.tracker.stop, TRACKING_STOP_TIMEOUT
            )
        except Exception:
            pass
    _TrackerState.reset()
    return ok({"running": False}, "面捕已停止")


@app.websocket("/ws/tracking")
async def ws_tracking(websocket: WebSocket):
    """向浏览器推送实时面捕参数（Live2D 参数映射，30fps 节流）。"""
    # 浏览器 WS 握手不触发 CORS：恶意网页可直连本端点窃取摄像头面捕数据，
    # 必须在 accept 之前按 Origin 白名单拒绝（1008 = Policy Violation）。
    if not _ws_origin_allowed(websocket):
        await websocket.close(code=1008)
        return
    if not _TrackerState.acquire_ws_slot():
        # 1013 = Try Again Later：面捕推流连接数已达上限
        await websocket.close(code=1013)
        return
    await websocket.accept()
    try:
        await websocket.send_json({"type": "tracking_ready", "timestamp": datetime.now().isoformat()})
        while True:
            if not _TrackerState.running:
                await websocket.send_json({"type": "tracking_stopped"})
                break
            if _TrackerState.lock:
                with _TrackerState.lock:
                    params = dict(_TrackerState.params)
            else:
                params = {}
            if params:
                await websocket.send_json({
                    "type": "params",
                    "params": params,
                    "error": _TrackerState.error or None,
                    "timestamp": datetime.now().isoformat(),
                })
            await asyncio.sleep(1 / 30)
    except WebSocketDisconnect:
        pass
    finally:
        _TrackerState.release_ws_slot()


class _GenericWSState:
    """/api/ws 通用通道的全局连接计数（M7）。

    与 _TrackerState 同样的考虑：页面脚本泄漏或恶意网页可以建立大量空闲
    WebSocket，占满文件描述符与事件循环资源。上限必须在 accept 之前判定，
    超额以 1013（Try Again Later）拒绝，浏览器拿不到已建立的连接。
    可用 LIVE2D_WS_CLIENT_LIMIT 覆盖默认 100。
    """

    CLIENT_LIMIT = 100
    _clients_lock = threading.Lock()
    clients = 0

    @classmethod
    def _effective_limit(cls) -> int:
        raw = os.environ.get("LIVE2D_WS_CLIENT_LIMIT", "").strip()
        if not raw:
            return cls.CLIENT_LIMIT
        try:
            val = int(raw)
            return val if val > 0 else cls.CLIENT_LIMIT
        except ValueError:
            return cls.CLIENT_LIMIT

    @classmethod
    def acquire(cls) -> bool:
        limit = cls._effective_limit()
        with cls._clients_lock:
            if cls.clients >= limit:
                return False
            cls.clients += 1
            return True

    @classmethod
    def release(cls) -> None:
        with cls._clients_lock:
            cls.clients = max(0, cls.clients - 1)


async def _ws_receive_loop(websocket: WebSocket) -> None:
    """通用 WS 通道的接收循环：ping/pong + ack。"""
    while True:
        data = await websocket.receive_text()
        try:
            msg = json.loads(data)
        except json.JSONDecodeError:
            continue
        msg_type = msg.get("type", "")
        if msg_type == "ping":
            await websocket.send_json({
                "type": "pong",
                "timestamp": datetime.now().isoformat(),
            })
        else:
            await websocket.send_json({
                "type": "ack",
                "data": {"received": msg_type},
                "timestamp": datetime.now().isoformat(),
            })


async def _ws_tracking_pusher(websocket: WebSocket) -> None:
    """在通用 WS 通道上推送原生面捕帧。

    协议与 Go 侧 native_bridge 推的 tracking_frame 一致
    （web/lib/native-tracker.ts 只认 /api/ws 上的 tracking_frame）：
        {"type": "tracking_frame", "data": {live2d_params, ...}, "time": <ms>}

    早先本端点只有一个阻塞的 receive 循环，推帧无处可去 —— 前端连上了
    却永远收不到帧，降级模式下表现为「连着但模型不动」。
    """
    last_sent = 0.0
    while True:
        if not _TrackerState.running:
            await asyncio.sleep(1 / 30)
            continue
        params: Dict[str, float] = {}
        blendshapes: Dict[str, float] = {}
        head_rotation: Dict[str, float] = {}
        if _TrackerState.lock:
            with _TrackerState.lock:
                params = dict(_TrackerState.params)
                blendshapes = dict(_TrackerState.blendshapes)
                head_rotation = dict(_TrackerState.head_rotation)
        if not params:
            await asyncio.sleep(1 / 30)
            continue
        now = time.monotonic()
        fps = round(1.0 / max(now - last_sent, 1e-6), 1) if last_sent else 0.0
        last_sent = now
        stamp = int(time.time() * 1000)
        data: Dict[str, Any] = {
            "source": "mediapipe",
            "timestamp": stamp,
            "fps": fps,
            "live2d_params": params,
        }
        if blendshapes:
            data["blendshapes"] = blendshapes
        if head_rotation:
            data["head_rotation"] = head_rotation
        await websocket.send_json({
            "type": "tracking_frame",
            "data": data,
            "time": stamp,
        })
        await asyncio.sleep(1 / 30)


@app.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket):
    # 与 /ws/tracking 同一道 Origin 闸门：跨站页面不得建立 WS 连接。
    if not _ws_origin_allowed(websocket):
        await websocket.close(code=1008)
        return
    # M7：accept 之前先占槽，超额直接 1013 拒绝（不升级、不发 connected）。
    if not _GenericWSState.acquire():
        await websocket.close(code=1013)
        return
    try:
        await websocket.accept()
        await websocket.send_json({
            "type": "connected",
            "data": {"version": VERSION},
            "timestamp": datetime.now().isoformat(),
        })
        # 接收与推帧并发：接收循环负责 ping/pong，推帧循环负责 tracking_frame。
        receiver = asyncio.create_task(_ws_receive_loop(websocket))
        pusher = asyncio.create_task(_ws_tracking_pusher(websocket))
        try:
            done, _pending = await asyncio.wait(
                {receiver, pusher}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                if task.cancelled():
                    continue
                exc = task.exception()
                if exc is not None and not isinstance(exc, WebSocketDisconnect):
                    raise exc
        finally:
            for task in (receiver, pusher):
                if not task.done():
                    task.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        _GenericWSState.release()


@app.get("/ws/progress")
async def ws_progress():
    return ok({"status": "websocket_progress_endpoint"}, "使用 /api/ws 连接")


# ---------- 缓存管理 ----------
# 说明：本 Python 降级服务没有内存缓存层（Go API 才有 RequestCache）。
# 早期版本这里回 {enabled:True, ...} / {cleared:True} 是假成功 —— 给前端
# 造成「缓存可用」的错觉，但 entries 永远是 0、cleared 也没真正清理任何
# 东西。按路由收缩原则改为 501，让调用方明确知道要切到 Go 服务。
@app.get("/api/cache/stats")
async def get_cache_stats():
    return not_implemented("缓存统计", go_endpoint="/api/cache/stats")


@app.post("/api/cache/clear")
async def clear_cache():
    return not_implemented("缓存清理", go_endpoint="/api/cache/clear")


@app.get("/api/scripts")
async def get_python_scripts():
    scripts = []
    scripts_dir = PROJECT_ROOT / "scripts"
    if scripts_dir.exists():
        for f in scripts_dir.glob("*.py"):
            scripts.append({"name": f.stem, "path": str(f)})
    return ok(scripts)


# ---------- 主入口 ----------
if __name__ == "__main__":
    import uvicorn
    # v0.10.3: 默认端口从 8000 对齐到 8080，与 Go 后端及前端 Next.js 代理目标一致。
    # 此前端口不一致会导致降级模式（python api_server.py）启动后前端所有请求 404。
    uvicorn.run(
        "api_server:app",
        host=SERVER_HOST,
        port=SERVER_PORT,
        reload=False,
        log_level="info",
    )
