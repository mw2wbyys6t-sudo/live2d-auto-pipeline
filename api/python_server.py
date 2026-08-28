#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live2D Master Agent v10.0 - Python API Server
A lightweight Python replacement for the Go API server using only stdlib.
"""
import http.server
import json
import os
import sys
import time
import subprocess
import threading
import urllib.parse
import mimetypes
import traceback
from pathlib import Path
from datetime import datetime

# Project root detection
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent  # api/ -> workspace/
if not (PROJECT_ROOT / "core" / "workflow.py").exists():
    # Fallback: try cwd
    cwd = Path.cwd()
    if (cwd / "core" / "workflow.py").exists():
        PROJECT_ROOT = cwd
    elif (cwd.parent / "core" / "workflow.py").exists():
        PROJECT_ROOT = cwd.parent

OUTPUT_DIR = PROJECT_ROOT / "output"
ASSETS_DIR = PROJECT_ROOT / "assets"
CHARACTERS_DIR = ASSETS_DIR / "characters"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)

START_TIME = time.time()
PORT = int(os.environ.get("GO_API_PORT", 8080))
HOST = os.environ.get("GO_API_HOST", "127.0.0.1")

# Expressions list
EXPRESSIONS = [
    {"name": "默认", "id": "default", "params": ["ParamEyeLOpen", "ParamMouthForm"]},
    {"name": "微笑", "id": "smile", "params": ["ParamEyeLSmile", "ParamMouthForm"]},
    {"name": "生气", "id": "angry", "params": ["ParamBrowLY", "ParamMouthForm"]},
    {"name": "惊讶", "id": "surprised", "params": ["ParamEyeLOpen", "ParamMouthOpenY"]},
    {"name": "害羞", "id": "shy", "params": ["ParamBrowLAngle", "ParamMouthForm"]},
    {"name": "闭眼", "id": "closed_eyes", "params": ["ParamEyeLOpen", "ParamEyeROpen"]},
    {"name": "开心", "id": "happy", "params": ["ParamEyeLSmile", "ParamMouthForm"]},
    {"name": "难过", "id": "sad", "params": ["ParamBrowLAngle", "ParamMouthForm"]},
]


def run_python(code, timeout=120):
    """Execute inline Python code in project context."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env["LIVE2D_PROJECT_ROOT"] = str(PROJECT_ROOT)
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(PROJECT_ROOT), env=env, encoding="utf-8", errors="replace"
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Python execution timed out"
    except Exception as e:
        return -1, "", str(e)


def run_python_script(script_path, args, timeout=180):
    """Execute a Python script."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env["LIVE2D_PROJECT_ROOT"] = str(PROJECT_ROOT)
    try:
        cmd = [sys.executable, str(script_path)] + args
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=str(PROJECT_ROOT), env=env, encoding="utf-8", errors="replace"
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Script execution timed out"
    except Exception as e:
        return -1, "", str(e)


class Live2DHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for Live2D API."""

    # Suppress default logging for cleaner output
    def log_message(self, format, *args):
        sys.stderr.write(f"[{datetime.now().strftime('%H:%M:%S')}] {format % args}\n")

    def _set_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, PUT, DELETE")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Credentials", "true")

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._set_cors()
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        body = self.rfile.read(content_length)
        try:
            return json.loads(body.decode("utf-8"))
        except:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self._set_cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/api/health":
            uptime = time.time() - START_TIME
            self._send_json({
                "success": True,
                "message": "Live2D API 服务正常运行",
                "data": {"version": "v10.0-python", "uptime": f"{int(uptime)}s"}
            })
            return

        if path == "/api/status":
            # Check Python env
            py_ok = True
            py_msg = "正常"
            try:
                import PIL, numpy
            except ImportError as e:
                py_ok = False
                py_msg = f"异常: {e}"

            services = [
                {"name": "local_generator", "available": True, "version": "Pollinations (free)", "last_checked": datetime.now().isoformat()},
                {"name": "python_env", "available": py_ok, "version": py_msg, "last_checked": datetime.now().isoformat()},
                {"name": "see_through", "available": False, "version": "not installed", "last_checked": datetime.now().isoformat()},
            ]
            self._send_json({
                "success": True,
                "data": {
                    "services": services,
                    "uptime": time.time() - START_TIME,
                    "version": "v10.0-python",
                }
            })
            return

        if path == "/api/info":
            self._send_json({
                "success": True,
                "data": {
                    "name": "Live2D Master Agent API",
                    "version": "v10.0",
                    "description": "AI 虚拟主播生产工具 API",
                    "python_path": sys.executable,
                    "project_root": str(PROJECT_ROOT),
                    "endpoints": [
                        "GET /api/health", "GET /api/status", "GET /api/info",
                        "GET /api/models", "GET /api/expressions",
                        "POST /api/generate", "POST /api/generate/character",
                        "POST /api/psd-plan", "POST /api/export/live2d",
                        "GET /api/characters", "POST /api/characters",
                        "POST /api/chat", "POST /api/chat/stream",
                        "GET /output/:filename",
                    ]
                }
            })
            return

        if path == "/api/models":
            self._send_json({
                "success": True,
                "data": {
                    "image_generators": ["pollinations", "seedream", "sensenova"],
                    "llm_providers": ["openai", "ollama", "anthropic"],
                    "tts_providers": ["edge-tts"],
                    "default_image": "pollinations",
                    "default_tts": "edge-tts",
                }
            })
            return

        if path == "/api/expressions":
            self._send_json({"success": True, "data": EXPRESSIONS})
            return

        if path == "/api/characters":
            chars = []
            if CHARACTERS_DIR.exists():
                for f in CHARACTERS_DIR.glob("*.json"):
                    try:
                        with open(f, "r", encoding="utf-8") as fp:
                            chars.append(json.load(fp))
                    except:
                        pass
            self._send_json({"success": True, "data": chars})
            return

        if path.startswith("/api/characters/"):
            cid = path.split("/")[-1]
            cfile = CHARACTERS_DIR / f"{cid}.json"
            if cfile.exists():
                with open(cfile, "r", encoding="utf-8") as fp:
                    self._send_json({"success": True, "data": json.load(fp)})
            else:
                self._send_json({"success": False, "error": "角色不存在"}, 404)
            return

        if path == "/api/scripts":
            scripts = [
                {"name": "core/workflow.py", "description": "完整工作流引擎 v10.0", "available": (PROJECT_ROOT / "core" / "workflow.py").exists()},
                {"name": "core/cli.py", "description": "交互式命令行工具", "available": (PROJECT_ROOT / "core" / "cli.py").exists()},
            ]
            self._send_json({"success": True, "data": scripts})
            return

        if path == "/api/cache/stats":
            self._send_json({"success": True, "data": {"entries": 0, "size_mb": 0}})
            return

        if path.startswith("/output/"):
            filename = path[len("/output/"):]
            filepath = OUTPUT_DIR / filename
            if filepath.exists() and filepath.is_file():
                mime, _ = mimetypes.guess_type(str(filepath))
                if not mime:
                    mime = "application/octet-stream"
                with open(filepath, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self._set_cors()
                self.end_headers()
                self.wfile.write(data)
            else:
                self._send_json({"success": False, "error": "文件不存在"}, 404)
            return

        if path == "/":
            self._send_json({
                "success": True,
                "message": "Live2D Master Agent API v10.0 (Python Edition)",
                "health": "/api/health",
            })
            return

        self._send_json({"success": False, "error": f"Not found: {path}"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self._read_json()

        if path == "/api/generate":
            self._handle_generate(body)
            return

        if path == "/api/generate/character":
            self._handle_generate_character(body)
            return

        if path == "/api/psd-plan":
            self._handle_psd_plan(body)
            return

        if path == "/api/see-through":
            self._send_json({
                "success": False,
                "message": "ComfyUI 未安装，请先运行 python install_comfyui_advanced.py"
            })
            return

        if path == "/api/export/live2d":
            self._handle_export_live2d(body)
            return

        if path == "/api/characters":
            self._handle_create_character(body)
            return

        if path == "/api/chat":
            self._handle_chat(body)
            return

        if path == "/api/chat/stream":
            self._handle_chat_stream(body)
            return

        if path == "/api/cache/clear":
            self._send_json({"success": True, "message": "缓存已清除"})
            return

        self._send_json({"success": False, "error": f"Not found: {path}"}, 404)

    def _handle_generate(self, body):
        """Handle image generation request."""
        prompt = body.get("prompt", "蓝发猫耳少女，白色背景，日系赛璐璐风格")
        width = body.get("width", 1024)
        height = body.get("height", 1024)
        seed = body.get("seed", -1)
        provider = body.get("provider", "pollinations")

        self._send_json({
            "success": True,
            "message": "开始生成",
            "data": {
                "prompt": prompt,
                "width": width,
                "height": height,
                "provider": provider,
                "status": "processing",
            }
        }, 202)

        # Run generation in background
        def do_generate():
            try:
                ts = int(time.time())
                out_dir = OUTPUT_DIR / f"gen_{ts}"
                out_dir.mkdir(parents=True, exist_ok=True)
                # Use Pollinations via Python directly
                py_code = f'''
import sys, json
sys.path.insert(0, {str(PROJECT_ROOT)!r})
from core.image_gen.pollinations import PollinationsGenerator
gen = PollinationsGenerator()
result = gen.generate(prompt={prompt!r}, width={width}, height={height}, seed={seed}, output_dir={str(out_dir)!r})
print(json.dumps(result, ensure_ascii=False, default=str))
'''
                rc, stdout, stderr = run_python(py_code, timeout=120)
                result_file = out_dir / "result.json"
                with open(result_file, "w", encoding="utf-8") as f:
                    f.write(json.dumps({"stdout": stdout, "stderr": stderr, "rc": rc}, ensure_ascii=False))
            except Exception as e:
                traceback.print_exc()

        threading.Thread(target=do_generate, daemon=True).start()

    def _handle_generate_character(self, body):
        """Handle character-consistent generation."""
        name = body.get("name", "新角色")
        prompt = body.get("prompt", "")
        self._send_json({
            "success": True,
            "message": "角色生成已启动",
            "data": {"name": name, "prompt": prompt, "status": "processing"}
        }, 202)

    def _handle_psd_plan(self, body):
        """Handle PSD layer planning."""
        image_path = body.get("image_path", "")
        if not image_path:
            self._send_json({"success": False, "error": "缺少 image_path"}, 400)
            return
        self._send_json({
            "success": True,
            "message": "PSD 分层规划已启动",
            "data": {"image_path": image_path, "status": "processing"}
        }, 202)

    def _handle_export_live2d(self, body):
        """Handle Live2D model export."""
        character_id = body.get("character_id", "")
        layers_dir = body.get("layers_dir", "")
        output_dir = body.get("output_dir", "")
        self._send_json({
            "success": True,
            "message": "Live2D 导出已启动",
            "data": {"character_id": character_id, "status": "processing"}
        }, 202)

    def _handle_create_character(self, body):
        """Create a new character card."""
        import uuid
        cid = body.get("id", str(uuid.uuid4())[:8])
        name = body.get("name", "新角色")
        card = {
            "id": cid,
            "name": name,
            "description": body.get("description", ""),
            "prompt": body.get("prompt", ""),
            "created_at": datetime.now().isoformat(),
            "reference_images": body.get("reference_images", []),
            "appearance": body.get("appearance", {}),
            "personality": body.get("personality", ""),
        }
        cfile = CHARACTERS_DIR / f"{cid}.json"
        with open(cfile, "w", encoding="utf-8") as f:
            json.dump(card, f, ensure_ascii=False, indent=2)
        self._send_json({"success": True, "data": card})

    def _handle_chat(self, body):
        """Handle chat request."""
        message = body.get("message", "")
        character_id = body.get("character_id", "")
        # Simple echo/response for now; full LLM integration requires API keys
        self._send_json({
            "success": True,
            "data": {
                "response": f"你好！我是你的虚拟角色。你说：「{message}」\n（请配置 OPENAI_API_KEY 以启用完整 LLM 对话功能）",
                "emotion": "neutral",
                "expression": "default",
                "character_id": character_id,
            }
        })

    def _handle_chat_stream(self, body):
        """Handle streaming chat (simple non-streaming fallback)."""
        self._handle_chat(body)

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path.startswith("/api/characters/"):
            cid = path.split("/")[-1]
            cfile = CHARACTERS_DIR / f"{cid}.json"
            if cfile.exists():
                cfile.unlink()
                self._send_json({"success": True, "message": "角色已删除"})
            else:
                self._send_json({"success": False, "error": "角色不存在"}, 404)
            return
        self._send_json({"success": False, "error": "Not found"}, 404)

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self._read_json()
        if path.startswith("/api/characters/"):
            cid = path.split("/")[-1]
            cfile = CHARACTERS_DIR / f"{cid}.json"
            if cfile.exists():
                with open(cfile, "r", encoding="utf-8") as f:
                    card = json.load(f)
                card.update(body)
                with open(cfile, "w", encoding="utf-8") as f:
                    json.dump(card, f, ensure_ascii=False, indent=2)
                self._send_json({"success": True, "data": card})
            else:
                self._send_json({"success": False, "error": "角色不存在"}, 404)
            return
        self._send_json({"success": False, "error": "Not found"}, 404)


class ThreadedHTTPServer(http.server.ThreadingHTTPServer):
    """Threaded HTTP server for handling concurrent requests."""
    allow_reuse_address = True
    daemon_threads = True


def main():
    server = ThreadedHTTPServer((HOST, PORT), Live2DHandler)
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  Live2D Master Agent API v10.0 (Python Edition)")
    print(f"  轻量 Python API 服务器 - 无需 Go 编译即可运行")
    print(sep)
    print(f"  服务地址: http://localhost:{PORT}")
    print(f"  项目根目录: {PROJECT_ROOT}")
    print(f"  输出目录: {OUTPUT_DIR}")
    print(f"  Python: {sys.executable}")
    print(sep)
    print(f"  API 端点:")
    print(f"    GET  /api/health      - 健康检查")
    print(f"    GET  /api/status      - 系统状态")
    print(f"    GET  /api/info        - API 信息")
    print(f"    GET  /api/expressions - 表情列表")
    print(f"    POST /api/generate    - 生成图片")
    print(f"    GET  /api/characters  - 角色列表")
    print(f"    POST /api/chat        - AI 对话")
    print(f"    GET  /output/:file    - 输出文件")
    print(sep)
    print(f"  按 Ctrl+C 停止服务器\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
