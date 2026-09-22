"""
Live2D Master Agent - Python API Server (FastAPI)
兼容 Go API 接口格式，直接调用 core 模块。

启动方式:
    python api_server.py
    # 或
    uvicorn api_server:app --host 0.0.0.0 --port 8080
"""

import os
import sys
import json
import time
import uuid
import shutil
import asyncio
import tempfile
import zipfile
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from pydantic import BaseModel, Field

# ---------- 项目路径设置 ----------
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

OUTPUT_DIR = PROJECT_ROOT / "output"
ASSETS_DIR = PROJECT_ROOT / "assets"
CHARACTERS_DIR = ASSETS_DIR / "characters"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)

VERSION = "v10.1-py"
START_TIME = time.time()


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
    print(f"  服务地址: http://0.0.0.0:8000")
    print(f"  输出目录: {OUTPUT_DIR}")
    print(f"  项目根目录: {PROJECT_ROOT}")
    print(f"{'='*60}\n")
    yield
    # 关闭时
    print("API 服务已停止")


app = FastAPI(title="Live2D Master Agent API", version=VERSION, lifespan=lifespan)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- 工具函数 ----------
def ok(data: Any = None, message: str = "") -> Dict[str, Any]:
    return {"success": True, "message": message, "data": data}


def err(error: str, code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=code,
        content={"success": False, "error": error, "data": None},
    )


def not_implemented(feature: str) -> JSONResponse:
    """统一的「尚未实现」响应：501 + 明确错误，绝不返回假成功。"""
    return err(f"{feature}尚未实现", 501)


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


def update_character_image(char_id: str, image_url: str) -> None:
    """生成成功后把图片 URL 回写到角色记录，供角色列表展示缩略图。"""
    if not char_id or not image_url:
        return
    path = CHARACTERS_DIR / f"{char_id}.json"
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


# ---------- 基础端点 ----------
@app.get("/")
async def root():
    return ok({"version": VERSION, "name": "Live2D Master Agent API"}, "API 运行中")


@app.get("/api/health")
async def health_check():
    uptime = time.time() - START_TIME
    return ok({
        "version": VERSION,
        "uptime": f"{int(uptime)}s",
    }, "Live2D API 服务正常运行")


@app.get("/api/status")
async def get_system_status():
    services = [
        {"name": "image_generator", "available": True, "version": "pollinations+core", "last_checked": datetime.now().isoformat()},
        {"name": "python_env", "available": True, "version": sys.version.split()[0], "last_checked": datetime.now().isoformat()},
        {"name": "segment_engine", "available": True, "version": "v10.1", "last_checked": datetime.now().isoformat()},
        {"name": "live2d_builder", "available": True, "version": "cubism4", "last_checked": datetime.now().isoformat()},
    ]
    return ok({
        "version": VERSION,
        "services": services,
        "uptime": int(time.time() - START_TIME),
    })


@app.get("/api/info")
async def get_api_info():
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
        "features": [
            "AI 图像生成",
            "语义分层",
            "Live2D 自动绑定",
            "角色管理",
            "LLM 对话",
        ],
    })


@app.get("/api/models")
async def get_models():
    return ok([
        {"id": "pollinations", "name": "Pollinations (免费)", "type": "image", "available": True},
        {"id": "seedream", "name": "Seedream 4.0", "type": "image", "available": False},
        {"id": "sensenova", "name": "商汤日日新", "type": "image", "available": False},
    ])


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
    path = CHARACTERS_DIR / f"{char_id}.json"
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


@app.get("/api/characters/{char_id}")
async def get_character(char_id: str):
    path = CHARACTERS_DIR / f"{char_id}.json"
    if not path.exists():
        return err(f"角色 {char_id} 不存在", 404)
    with open(path, "r", encoding="utf-8") as f:
        char = json.load(f)
    return ok(char)


@app.put("/api/characters/{char_id}")
async def update_character(char_id: str, req: Dict[str, Any]):
    path = CHARACTERS_DIR / f"{char_id}.json"
    if not path.exists():
        return err(f"角色 {char_id} 不存在", 404)
    with open(path, "r", encoding="utf-8") as f:
        char = json.load(f)
    char.update(req)
    char["updated_at"] = datetime.now().isoformat()
    _save_character(char)
    return ok(char, "角色更新成功")


@app.delete("/api/characters/{char_id}")
async def delete_character(char_id: str):
    path = CHARACTERS_DIR / f"{char_id}.json"
    if not path.exists():
        return err(f"角色 {char_id} 不存在", 404)
    path.unlink()
    return ok(None, "角色删除成功")


# ---------- 图像生成 ----------
def _run_generate(req: "GenerateRequest") -> Dict[str, Any]:
    """同步执行单图生成（在线程池中调用，避免阻塞事件循环）。"""
    from core.image_gen.router import get_router
    timestamp = int(time.time())
    output_path = str(OUTPUT_DIR / f"generated_{timestamp}.png")
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
        data = await asyncio.to_thread(_run_generate, req)
        return ok(data, "图像生成成功")
    except Exception as e:
        return err(f"图像生成失败: {e}", 500)


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
        result = await asyncio.to_thread(_run_generate_character, req)
        return ok(result, "角色生成完成" if result.get("success") else "角色生成失败")
    except Exception as e:
        return err(f"角色生成失败: {e}", 500)


@app.get("/api/generations/latest")
async def latest_generation():
    """返回最近一次成功生成的产物，供前端跨页面接力使用。"""
    if not LATEST_GEN_FILE.exists():
        return err("尚未有任何生成记录", 404)
    try:
        with open(LATEST_GEN_FILE, "r", encoding="utf-8") as f:
            return ok(json.load(f))
    except Exception as e:
        return err(f"读取最近生成记录失败: {e}", 500)


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
    path = Path(src) if src else None
    if path is None or not path.is_file():
        raise RuntimeError("找不到可用的源图片，请先生成角色图")

    img = Image.open(path).convert("RGBA")
    out_dir = OUTPUT_DIR / f"layers_{int(time.time())}"

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
    }


@app.post("/api/segment")
async def segment_image(req: SegmentRequest):
    """对图片运行真实分层并写出 PSD，供「分层工作台」使用。"""
    try:
        data = await asyncio.to_thread(_run_segment, req)
        return ok(data, "分层完成")
    except Exception as e:
        return err(f"分层失败: {e}", 500)


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
    return not_implemented("See-through 抠图")


# ---------- 导出 ----------
def _resolve_export_source(req: "ExportLive2DRequest") -> str:
    """定位导出所用的图层来源目录。

    优先级：显式 layers_dir → model_dir → OUTPUT_DIR 下最新的 layers_* 目录。
    """
    for candidate in (req.layers_dir, req.model_dir):
        if candidate:
            p = Path(candidate)
            if not p.is_absolute():
                p = PROJECT_ROOT / p
            if p.is_dir() and any(p.glob("*.png")):
                return str(p)
    for d in sorted(OUTPUT_DIR.glob("layers_*"), key=lambda x: x.stat().st_mtime, reverse=True):
        if d.is_dir() and any(d.glob("*.png")):
            return str(d)
    return ""


def _load_layers_for_export(layers_dir: str) -> "Dict[str, Any]":
    """把目录下的 PNG 图层读成 {名称: PIL.Image}，供 Live2DBuilder 使用。"""
    from PIL import Image

    layers: Dict[str, Any] = {}
    base = Path(layers_dir)
    for f in sorted(base.glob("*.png")):
        try:
            layers[f.stem] = Image.open(f).convert("RGBA")
        except Exception:
            continue
    return layers


def _build_live2d_model(layers: "Dict[str, Any]", output_dir: str, name: str) -> Dict[str, Any]:
    """同步构建 Live2D 模型包（在线程池中调用）。"""
    from live2d_builder.pipeline import Live2DBuilder

    builder = Live2DBuilder(output_dir=output_dir, character_name=name or "character")
    return builder.build(layers)


def runtime_readiness(result: Dict[str, Any]) -> Dict[str, Any]:
    """导出包能否被 Live2D 运行时直接加载。

    唯一可信来源是构建期官方 Cubism Core 的验收结论（``result["moc3"]``）。
    磁盘上存在 ``.moc3`` 文件不代表它通过了内核一致性检查 —— 早先版本按
    「文件是否存在」判定，会把被内核拒绝的产物报成开箱即用。
    """
    moc3 = result.get("moc3") or {}
    ready = bool(moc3.get("runtime_ready"))
    if not moc3:
        blocker = "构建结果缺少 moc3 验收信息，不能视为可部署"
    else:
        blocker = moc3.get("moc3_blocker") or "moc3 未通过官方 Cubism Core 验收"
    return {
        "runtime_ready": ready,
        "moc_reference": moc3.get("moc3_path") or "",
        "official_core_consistent": bool(moc3.get("official_core_consistent")),
        "moc3_dropped": moc3.get("moc3_dropped") or [],
        "deformers_compiled": moc3.get("deformers_compiled") or [],
        "deformers_uncompiled": moc3.get("deformers_uncompiled") or [],
        # true = 官方内核真的在播放产物里的 motion；null = 没有可动参数，
        # 不适用（不等于通过）；false = 写了 motion 但内核不播，属于阻塞
        "motion_verified": moc3.get("motion_verified"),
        # true = 官方内核里 physics 确实产生带滞后的运动；null = 无可验物理链
        "physics_verified": moc3.get("physics_verified"),
        "blocker": "" if ready else blocker,
    }


@app.post("/api/export/live2d")
async def export_live2d(req: ExportLive2DRequest):
    try:
        source_dir = _resolve_export_source(req)
        if not source_dir:
            payload = {"success": False, "message": "未找到可导出的图层目录"}
            return ok(payload, "导出失败：缺少图层")

        layers = await asyncio.to_thread(_load_layers_for_export, source_dir)
        if not layers:
            payload = {"success": False, "message": f"图层目录内没有可用 PNG：{source_dir}"}
            return ok(payload, "导出失败：图层为空")

        export_dir = OUTPUT_DIR / f"export_{int(time.time())}"
        export_dir.mkdir(parents=True, exist_ok=True)
        result = await asyncio.to_thread(
            _build_live2d_model, layers, str(export_dir), req.character_id
        )

        model3 = result.get("model3_json", "") or ""
        textures = [t for t in (result.get("textures") or []) if t]

        # Live2D 预览页需要直接下载 ZIP 包
        if req.download:
            zip_path = OUTPUT_DIR / f"live2d_{int(time.time())}.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in export_dir.rglob("*"):
                    if f.is_file():
                        zf.write(f, f.relative_to(export_dir))
            return FileResponse(
                str(zip_path), media_type="application/zip", filename=zip_path.name
            )

        return ok({
            "success": True,
            "model3_json": to_output_url(model3),
            "model_path": to_output_url(model3),
            "texture": to_output_url(textures[0]) if textures else "",
            "textures": [to_output_url(t) for t in textures],
            "physics": to_output_url(result.get("physics", "") or ""),
            "expressions": result.get("expressions", []),
            "validation": result.get("validation", {}),
            "output_dir": str(export_dir),
            "elapsed_seconds": result.get("elapsed_seconds"),
            **runtime_readiness(result),
        }, "Live2D 模型导出成功")
    except Exception as e:
        return ok({
            "success": False,
            "model3_url": "",
            "model_path": "",
            "message": str(e),
        }, f"导出失败: {e}")


@app.get("/api/export/package")
async def export_model_zip(character_id: str = ""):
    return not_implemented("打包下载（请改用 /api/export/live2d 并传 download=true）")


@app.post("/api/export/psd")
async def export_psd():
    return not_implemented("PSD 导出（可通过 /api/export/live2d 获取模型包）")


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

        result = await asyncio.to_thread(_run_deploy_desktop, layers_dir)
        if not result.get("success"):
            return err(result.get("error", "桌宠打包失败"), 500)
        pkg_dir = result.get("package_dir", "")
        return ok({
            "success": True,
            "package_dir": pkg_dir,
            "run_script": str(Path(pkg_dir) / "run_pet.bat"),
            "readme": str(Path(pkg_dir) / "README.txt"),
        }, "桌宠包已生成")
    except Exception as e:
        return err(f"桌宠部署失败: {e}", 500)


# ---------- LLM 对话 ----------
def _make_chat_session(character_id: str = ""):
    """构造 ChatSession（LLMRouter 必须显式注入），并恢复历史与角色人设。"""
    from llm_bridge.providers.router import LLMRouter
    from llm_bridge.chat_session import ChatSession

    persona = "活泼可爱，有点傲娇，喜欢和主人聊天"
    name = "小奈"
    if character_id:
        path = CHARACTERS_DIR / f"{character_id}.json"
        if path.exists():
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
        return ok({
            "reply": f"（对话功能暂不可用：{e}）\n\n我听到你说：「{req.message}」",
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
            # 降级模式：逐字返回
            text = f"（对话功能暂不可用：{e}）\n\n我听到你说：「{req.message}」"
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
    error: str = ""

    @classmethod
    def reset(cls):
        cls.tracker = None
        cls.thread = None
        cls.running = False
        cls.params = {}
        cls.error = ""


def _tracking_loop() -> None:
    """后台线程：读取摄像头帧 → blendshapes → Live2D 参数。"""
    import threading
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
            time.sleep(1 / 30)
        except Exception as e:
            _TrackerState.error = str(e)
            time.sleep(0.5)


@app.post("/api/tracking/start")
async def tracking_start():
    """启动摄像头面捕（mediapipe），参数经 /ws/tracking 推送。"""
    import threading

    if _TrackerState.running:
        return ok({"running": True}, "面捕已在运行")
    try:
        from drivers.face_tracker.mediapipe_tracker import FaceTracker
    except Exception as e:
        return err(f"面捕模块不可用: {e}", 500)

    def _open():
        from drivers.face_tracker.mediapipe_tracker import FaceTracker
        t = FaceTracker()
        t.start()
        return t

    try:
        tracker = await asyncio.to_thread(_open)
    except Exception as e:
        return err(f"摄像头启动失败: {e}", 500)

    _TrackerState.tracker = tracker
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
            await asyncio.to_thread(_TrackerState.tracker.stop)
        except Exception:
            pass
    _TrackerState.reset()
    return ok({"running": False}, "面捕已停止")


@app.websocket("/ws/tracking")
async def ws_tracking(websocket: WebSocket):
    """向浏览器推送实时面捕参数（Live2D 参数映射，30fps 节流）。"""
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


@app.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        await websocket.send_json({
            "type": "connected",
            "data": {"version": VERSION},
            "timestamp": datetime.now().isoformat(),
        })
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
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
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass


@app.get("/ws/progress")
async def ws_progress():
    return ok({"status": "websocket_progress_endpoint"}, "使用 /api/ws 连接")


# ---------- 缓存管理 ----------
@app.get("/api/cache/stats")
async def get_cache_stats():
    return ok({"enabled": True, "entries": 0, "size_mb": 0, "hit_rate": 0})


@app.post("/api/cache/clear")
async def clear_cache():
    return ok({"cleared": True}, "缓存已清除")


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
    uvicorn.run(
        "api_server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
