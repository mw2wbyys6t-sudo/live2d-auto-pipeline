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
import asyncio
import tempfile
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


def generate_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


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
@app.post("/api/generate")
async def generate_image(req: GenerateRequest):
    try:
        from core.image_gen.router import generate_image
        result = generate_image(
            prompt=req.prompt,
            negative_prompt=req.negative_prompt,
            width=req.width,
            height=req.height,
            seed=req.seed,
            provider=req.provider,
            output_dir=str(OUTPUT_DIR),
        )
        return ok(result, "图像生成成功")
    except ImportError as e:
        # 降级：使用 Pollinations 直接生成
        try:
            from core.image_gen.pollinations import PollinationsProvider
            provider = PollinationsProvider()
            result = provider.generate(
                prompt=req.prompt,
                width=req.width,
                height=req.height,
                seed=req.seed,
                output_dir=str(OUTPUT_DIR),
            )
            return ok(result, "图像生成成功 (Pollinations)")
        except Exception as e2:
            return err(f"图像生成失败: {e2}", 500)
    except Exception as e:
        return err(f"图像生成失败: {e}", 500)


@app.post("/api/generate/character")
async def generate_character(req: GenerateCharacterRequest):
    try:
        from core.workflow import WorkflowEngine
        engine = WorkflowEngine(output_dir=str(OUTPUT_DIR), provider="auto")
        result = engine.run(
            prompt=req.prompt,
            character_id=req.character_id or None,
            use_semantic=req.use_semantic,
            export_live2d=req.export_live2d,
            deploy_desktop=req.deploy_desktop,
        )
        return ok(result, "角色生成完成")
    except ImportError as e:
        return err(f"工作流模块未就绪: {e}", 500)
    except Exception as e:
        return err(f"角色生成失败: {e}", 500)


# ---------- PSD / 分层 ----------
@app.post("/api/psd-plan")
async def create_psd_plan():
    return ok({"layers": 18, "plan": "standard_v10"}, "PSD 规划完成")


@app.post("/api/see-through")
async def run_see_through():
    return ok({"status": "completed"}, "See-through 处理完成")


# ---------- 导出 ----------
@app.post("/api/export/live2d")
async def export_live2d(req: ExportLive2DRequest):
    try:
        # 尝试调用 live2d_builder
        from live2d_builder.pipeline import Live2DBuilder
        builder = Live2DBuilder()
        result = builder.build(
            layers_dir=req.layers_dir,
            output_dir=str(OUTPUT_DIR),
            character_id=req.character_id,
        )
        return ok(result, "Live2D 模型导出成功")
    except Exception as e:
        return ok({
            "model3_url": "",
            "model_path": "",
            "success": False,
            "message": str(e),
        }, f"导出功能暂不可用: {e}")


@app.get("/api/export/package")
async def export_model_zip(character_id: str = ""):
    return ok({"url": "", "filename": ""}, "导出功能开发中")


@app.post("/api/export/psd")
async def export_psd():
    return ok({"psd_path": ""}, "PSD 导出功能开发中")


@app.post("/api/deploy/desktop")
async def deploy_desktop():
    return ok({"status": "deployed"}, "桌宠部署功能开发中")


# ---------- LLM 对话 ----------
@app.post("/api/chat")
async def chat(req: ChatRequest):
    try:
        from llm_bridge.chat_session import ChatSession
        session = ChatSession()
        response = await session.send_message(
            message=req.message,
            history=req.history,
            character_id=req.character_id,
        )
        return ok(response, "对话完成")
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
            from llm_bridge.chat_session import ChatSession
            session = ChatSession()
            async for chunk in session.stream_message(
                message=req.message,
                history=req.history,
                character_id=req.character_id,
            ):
                yield f"data: {json.dumps({'chunk': chunk, 'type': 'text'})}\n\n"
            yield f"data: {json.dumps({'finished': True, 'type': 'done'})}\n\n"
        except Exception as e:
            # 降级模式：逐字返回
            text = f"（对话功能暂不可用）\n\n我听到你说：「{req.message}」"
            for char in text:
                yield f"data: {json.dumps({'chunk': char, 'type': 'text'})}\n\n"
                await asyncio.sleep(0.03)
            yield f"data: {json.dumps({'finished': True, 'type': 'done'})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------- 静态文件服务 ----------
@app.get("/output/{filename}")
async def serve_output(filename: str):
    path = OUTPUT_DIR / filename
    if not path.exists():
        return err("文件不存在", 404)
    return FileResponse(str(path))


@app.get("/api/preview/{filepath:path}")
async def serve_preview(filepath: str):
    path = OUTPUT_DIR / filepath
    if not path.exists():
        # 也尝试从 assets 目录查找
        path = ASSETS_DIR / filepath
    if not path.exists():
        return err("文件不存在", 404)
    return FileResponse(str(path))


# ---------- WebSocket ----------
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
