#!/usr/bin/env python3
"""ComfyUI 本地生图 Provider。

通过 ComfyUI 的 HTTP API 完成「提交图 → 轮询队列 → 取回图片」：
``POST /prompt`` → ``GET /history/{prompt_id}`` → ``GET /view``。

设计要点：
1. **只连本机**：地址经 ``ensure_local_endpoint`` 校验，公网地址直接拒绝。
2. **不硬编码模型**：未显式配置 ``COMFYUI_CHECKPOINT`` 时，从
   ``/object_info/CheckpointLoaderSimple`` 读取本机已安装的 checkpoint 并选第一个；
   一个都没有时给出可执行的修复提示，而不是抛一个看不懂的异常。
3. **支持自定义 workflow**：``COMFYUI_WORKFLOW`` 指向一个 API 格式的 workflow JSON
   时优先使用它（可替换 ``{{PROMPT}}`` / ``{{SEED}}`` 等占位符），这样 Flux /
   SDXL / LoRA 等任意工作流都能直接复用。
"""

from __future__ import annotations

import json
import os
import random
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from core.image_gen.base import (
    GenerationError,
    GenerationResult,
    ImageProvider,
    read_bounded,
)
from core.image_gen.local_http import ensure_local_endpoint
from core.logger import get_logger

log = get_logger("image_gen.comfyui")

DEFAULT_NEGATIVE_PROMPT = (
    "lowres, bad anatomy, bad hands, text, watermark, signature, "
    "blurry, jpeg artifacts, worst quality, low quality"
)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


class ComfyUIProvider(ImageProvider):
    """本地 ComfyUI 生图引擎。"""

    name = "comfyui"
    display_name = "ComfyUI（本地）"
    requires_api_key = False
    is_local = True
    max_retries = 0
    timeout = 600

    def __init__(self, config=None, session: Optional[requests.Session] = None):
        super().__init__(config)
        self.base_url = ensure_local_endpoint(
            os.environ.get("COMFYUI_BASE_URL") or "http://127.0.0.1:8188"
        )
        self.checkpoint = (os.environ.get("COMFYUI_CHECKPOINT") or "").strip()
        self.workflow_path = (os.environ.get("COMFYUI_WORKFLOW") or "").strip()
        # LoRA 是「角色一致性」的落点：训练产物放进 ComfyUI 的 models/loras 后，
        # 用 COMFYUI_LORA 指认文件名（可省略扩展名）。未设置即不注入，保持原图结构。
        self.lora = (os.environ.get("COMFYUI_LORA") or "").strip()
        self._session = session
        self._probe_cache: tuple[float, bool] = (0.0, False)

    # ---------- 基础设施 ----------

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def is_available(self, force: bool = False) -> bool:
        """探测 ``/system_stats``；结果缓存 3 秒，避免每次生成都重复探测。"""
        cached_at, cached = self._probe_cache
        if not force and cached_at and time.monotonic() - cached_at < 3.0:
            return cached
        available = False
        try:
            resp = self.session.get(self._url("/system_stats"), timeout=1.5)
            available = resp.status_code == 200
        except Exception:
            available = False
        self._probe_cache = (time.monotonic(), available)
        return available

    def get_setup_guide(self) -> str:
        return (
            "启动 ComfyUI（默认 http://127.0.0.1:8188）后即可使用。\n"
            "1) 在 ComfyUI 里下载至少一个 checkpoint（models/checkpoints）；\n"
            "   6GB 显存：先用 SD1.5 二次元模型快速调提示词，定稿再换 SDXL\n"
            "   （Illustrious-XL / NoobAI-XL，需开 --lowvram，出图更慢但更精细）；\n"
            "2) 若模型不在默认目录，设置 COMFYUI_CHECKPOINT 指定文件名；\n"
            "3) 角色一致性：把训练好的 LoRA 放进 models/loras，用 COMFYUI_LORA\n"
            "   指认文件名（可省略 .safetensors），强度用 COMFYUI_LORA_STRENGTH；\n"
            "4) 想用自定义工作流时，设置 COMFYUI_WORKFLOW 指向 API 格式的 workflow JSON\n"
            "   （支持 {{LORA}} / {{LORA_STRENGTH}} / {{LORA_STRENGTH_CLIP}} 占位符）。\n"
            f"当前地址：{self.base_url}"
        )

    # ---------- 主流程 ----------

    def generate(
        self,
        prompt: str,
        output_path: str,
        width: int = 1024,
        height: int = 1024,
        negative_prompt: Optional[str] = None,
        seed: Optional[int] = None,
        **kwargs,
    ) -> GenerationResult:
        if seed is None:
            seed = random.randint(0, 2**31 - 1)

        if not self.is_available(force=True):
            raise GenerationError(
                f"ComfyUI 未运行或无法访问（{self.base_url}）。"
                "请先启动 ComfyUI，或设置 COMFYUI_BASE_URL 指向正确端口。",
                provider=self.name,
                retryable=False,
            )

        self._report_progress("准备本地工作流", 10)
        graph = self._build_graph(
            prompt=prompt,
            negative_prompt=negative_prompt or DEFAULT_NEGATIVE_PROMPT,
            width=int(width),
            height=int(height),
            seed=int(seed),
            lora=kwargs.get("lora"),
            model=kwargs.get("model"),
        )

        self._report_progress("提交到 ComfyUI", 20)
        prompt_id = self._queue_prompt(graph)

        self._report_progress("本地推理中", 40)
        image_info = self._wait_for_image(prompt_id)

        self._report_progress("取回生成结果", 90)
        data = self._download_image(image_info)
        saved = self._save_bytes(data, output_path)

        used_checkpoint = getattr(self, "_pending_checkpoint", "") or self.checkpoint
        used_lora = getattr(self, "_pending_lora", "") or ""
        # 复盘需要知道「这张图用的是哪个底模 + 哪个 LoRA」，缺一不可：
        # 多模型并存后，只记 checkpoint 无法解释同一底模下画风不同。
        model_label = f"{used_checkpoint}+{used_lora}" if used_lora else used_checkpoint
        self._report_progress("完成", 100)
        log.success(f"ComfyUI 本地生成完成: {saved}")
        return GenerationResult(
            success=True,
            image_path=saved,
            provider=self.name,
            model=model_label,
            prompt=prompt or "",
            width=int(width),
            height=int(height),
            metadata={
                "local": True,
                "engine": "comfyui",
                "prompt_id": prompt_id,
                "seed": seed,
                "filename": image_info.get("filename", ""),
                "checkpoint": used_checkpoint,
                "lora": used_lora,
                "lora_strength": (getattr(self, "_pending_lora_strength", None)
                                  if used_lora else None),
            },
        )

    # ---------- workflow 构造 ----------

    def _apply_placeholders(self, node: Any) -> Any:
        """递归替换占位符；数值型占位符保持 int，避免 ComfyUI 类型校验失败。"""
        numeric = {"{{WIDTH}}": self._pending_width, "{{HEIGHT}}": self._pending_height,
                   "{{SEED}}": self._pending_seed, "{{STEPS}}": self._pending_steps,
                   "{{CFG}}": self._pending_cfg,
                   # 强度必须是 float：ComfyUI 会对 LoraLoader 的这两个输入做
                   # 类型校验，留成字符串会被直接拒收。
                   "{{LORA_STRENGTH}}": getattr(self, "_pending_lora_strength", 1.0),
                   "{{LORA_STRENGTH_CLIP}}": getattr(
                       self, "_pending_lora_strength_clip", 1.0)}
        if isinstance(node, dict):
            return {k: self._apply_placeholders(v) for k, v in node.items()}
        if isinstance(node, list):
            return [self._apply_placeholders(v) for v in node]
        if isinstance(node, str):
            if node in numeric:
                return numeric[node]
            replacements = {
                "{{PROMPT}}": self._pending_prompt,
                "{{NEGATIVE_PROMPT}}": self._pending_negative,
                "{{CHECKPOINT}}": self._pending_checkpoint,
                "{{LORA}}": getattr(self, "_pending_lora", ""),
                "{{SAMPLER}}": os.environ.get("COMFYUI_SAMPLER", "dpmpp_2m"),
                "{{SCHEDULER}}": os.environ.get("COMFYUI_SCHEDULER", "karras"),
                "{{PREFIX}}": "live2d",
            }
            out = node
            for key, value in replacements.items():
                out = out.replace(key, str(value))
            return out
        return node

    def _build_graph(
        self, prompt: str, negative_prompt: str,
        width: int, height: int, seed: int,
        lora: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._pending_prompt = prompt or ""
        self._pending_negative = negative_prompt or ""
        self._pending_width = width
        self._pending_height = height
        self._pending_seed = seed
        self._pending_steps = _env_int("COMFYUI_STEPS", 28)
        self._pending_cfg = _env_float("COMFYUI_CFG", 7.0)
        # LoRA 在**每次构建**时解析，不用 __init__ 的快照：一个 provider 实例要能
        # 按角色切换 LoRA（角色一致性正是这么用的）。请求级 lora 参数优先于环境变量。
        # 解析失败要抛错 —— 静默忽略会让「以为加了 LoRA」的产物无法解释。
        self._pending_lora = self._resolve_lora(lora)
        self._pending_lora_strength = _env_float("COMFYUI_LORA_STRENGTH", 1.0)
        self._pending_lora_strength_clip = _env_float(
            "COMFYUI_LORA_STRENGTH_CLIP", self._pending_lora_strength)

        if self.workflow_path:
            self._pending_checkpoint = self._resolve_checkpoint(
                required=False, override=model) or ""
            return self._load_workflow_file(self.workflow_path)

        self._pending_checkpoint = self._resolve_checkpoint(required=True, override=model)
        sampler = os.environ.get("COMFYUI_SAMPLER", "dpmpp_2m")
        scheduler = os.environ.get("COMFYUI_SCHEDULER", "karras")

        graph = self._txt2img_graph(
            width, height, seed, sampler, scheduler,
            self._pending_steps, self._pending_cfg)
        return self._inject_lora(graph)

    def _txt2img_graph(
        self, width: int, height: int, seed: int,
        sampler: str, scheduler: str, steps: int, cfg: float,
    ) -> Dict[str, Any]:
        """标准 txt2img 图：Checkpoint → 双 CLIPTextEncode → EmptyLatent → KSampler → VAE → SaveImage。"""
        return {
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": steps,
                    "cfg": cfg,
                    "sampler_name": sampler,
                    "scheduler": scheduler,
                    "denoise": 1.0,
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["5", 0],
                },
            },
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": self._pending_checkpoint},
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": width, "height": height, "batch_size": 1},
            },
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": self._pending_prompt, "clip": ["4", 1]},
            },
            "7": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": self._pending_negative, "clip": ["4", 1]},
            },
            "8": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
            },
            "9": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "live2d", "images": ["8", 0]},
            },
        }

    #: LoRA 节点编号。选 10 是为了不动既有 3~9 的编号（黄金断言依赖它们）。
    LORA_NODE_ID = "10"

    def _inject_lora(self, graph: Dict[str, Any]) -> Dict[str, Any]:
        """把 LoraLoader 插到 Checkpoint 与采样器之间。

        关键点：**model 与 clip 都要改接** LoRA 输出。只改 model 会让提示词
        绕过 LoRA 的词嵌入，等于只生效一半（画风变了但触发词不认）。
        未配置 LoRA 时原样返回 —— 结构逐键不变。
        """
        if not self._pending_lora:
            return graph
        graph[self.LORA_NODE_ID] = {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": self._pending_lora,
                "strength_model": self._pending_lora_strength,
                "strength_clip": self._pending_lora_strength_clip,
                "model": ["4", 0],
                "clip": ["4", 1],
            },
        }
        graph["3"]["inputs"]["model"] = [self.LORA_NODE_ID, 0]
        for node_id in ("6", "7"):
            graph[node_id]["inputs"]["clip"] = [self.LORA_NODE_ID, 1]
        return graph

    def _load_workflow_file(self, path: str) -> Dict[str, Any]:
        p = Path(path).expanduser()
        if not p.is_file():
            raise GenerationError(f"COMFYUI_WORKFLOW 指向的文件不存在: {path}",
                                  provider=self.name, retryable=False)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            raise GenerationError(f"读取 workflow JSON 失败: {e}",
                                  provider=self.name, retryable=False) from e
        # 兼容「UI 导出的 {nodes:[...]}」——这种格式不能直接提交 API
        if isinstance(raw, dict) and "nodes" in raw and "prompt" not in raw:
            raise GenerationError(
                "该 workflow 是 ComfyUI 界面格式，不是 API 格式。"
                "请在 ComfyUI 里使用「Save (API Format)」导出后再指定。",
                provider=self.name, retryable=False,
            )
        graph = raw.get("prompt", raw) if isinstance(raw, dict) else raw
        if not isinstance(graph, dict):
            raise GenerationError("workflow 结构不是 API 格式的节点字典",
                                  provider=self.name, retryable=False)
        return self._apply_placeholders(graph)

    def _resolve_lora(self, override: Optional[str] = None) -> str:
        """把 LoRA 名解析成 ComfyUI 里真实的 lora 文件名。

        优先级：请求级 ``override`` > 环境变量 ``COMFYUI_LORA``。
        - 都为空 → 返回空串（不注入 LoRA，属正常路径）；
        - 配置了 → 必须在 ``/object_info/LoraLoader`` 的列表里能对上。
          允许省略 ``.safetensors`` 扩展名，但**不做模糊匹配**：猜错一个
          LoRA 等于把角色换成别人，这比失败更难排查。
        """
        # 每次都读环境变量（而不是 __init__ 的快照）：设置页可以在进程运行中改配置，
        # 一个 provider 实例也要能按角色切换 LoRA。
        wanted = (override or os.environ.get("COMFYUI_LORA") or self.lora or "").strip()
        if not wanted:
            return ""
        try:
            resp = self.session.get(self._url("/object_info/LoraLoader"), timeout=5)
            if resp.status_code != 200:
                raise GenerationError(
                    f"查询 LoRA 列表失败: HTTP {resp.status_code}", provider=self.name)
            info = resp.json()
            node = info.get("LoraLoader", {}) if isinstance(info, dict) else {}
            available = [str(x) for x in (
                node.get("input", {})
                .get("required", {})
                .get("lora_name", [[]])[0] or []
            )]
        except GenerationError:
            raise
        except Exception as e:
            raise GenerationError(
                f"查询 ComfyUI LoRA 失败: {e}", provider=self.name) from e

        for candidate in (wanted, f"{wanted}.safetensors"):
            if candidate in available:
                log.info(f"使用 LoRA: {candidate}")
                return candidate
        raise GenerationError(
            f"COMFYUI_LORA 指定的 '{wanted}' 不在 ComfyUI 的 models/loras 里。"
            f"可用：{available or '（空）'}。请把训练好的 .safetensors 放进 "
            f"models/loras，或用 COMFYUI_LORA 指向正确文件名。",
            provider=self.name, retryable=False, code="lora_not_found",
        )

    def _resolve_checkpoint(self, required: bool = True,
                            override: Optional[str] = None) -> str:
        """解析底模文件名。优先级：请求级 override > COMFYUI_CHECKPOINT > 自动取第一个。

        显式 override 直接采信、不去对象列表里核对：ComfyUI 的 checkpoint 列表
        在模型刚放进去时可能还没刷新，而猜错/拒绝的代价比让 ComfyUI 自己报错更高。
        """
        if override and override.strip():
            return override.strip()
        if self.checkpoint:
            return self.checkpoint
        try:
            resp = self.session.get(
                self._url("/object_info/CheckpointLoaderSimple"), timeout=5
            )
            if resp.status_code != 200:
                raise GenerationError(f"查询 checkpoint 列表失败: HTTP {resp.status_code}")
            info = resp.json()
            node = info.get("CheckpointLoaderSimple", {}) if isinstance(info, dict) else {}
            ckpts = (
                node.get("input", {})
                .get("required", {})
                .get("ckpt_name", [[]])[0]
            )
        except GenerationError:
            raise
        except Exception as e:
            raise GenerationError(
                f"查询 ComfyUI checkpoint 失败: {e}", provider=self.name
            ) from e

        if ckpts:
            log.info(f"自动选择 checkpoint: {ckpts[0]}")
            return str(ckpts[0])
        if not required:
            return ""
        raise GenerationError(
            "ComfyUI 里没有任何 checkpoint。请先下载一个模型放到 "
            "models/checkpoints（RTX 2060 6G 建议 SD1.5，如 DreamShaper 8），"
            "或在环境变量里指定 COMFYUI_CHECKPOINT。",
            provider=self.name,
            retryable=False,
        )

    # ---------- HTTP 交互 ----------

    def _queue_prompt(self, graph: Dict[str, Any]) -> str:
        payload = {"prompt": graph, "client_id": uuid.uuid4().hex}
        try:
            resp = self.session.post(self._url("/prompt"), json=payload, timeout=30)
        except requests.RequestException as e:
            raise GenerationError(f"提交 ComfyUI 任务失败: {e}",
                                  provider=self.name) from e
        if resp.status_code != 200:
            raise GenerationError(
                f"提交 ComfyUI 任务返回 HTTP {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
            )
        try:
            data = resp.json()
        except ValueError as e:
            raise GenerationError(f"ComfyUI 返回了非 JSON 响应: {resp.text[:200]}",
                                  provider=self.name) from e
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise GenerationError(f"ComfyUI 未返回 prompt_id: {str(data)[:300]}",
                                  provider=self.name)
        return str(prompt_id)

    def _wait_for_image(self, prompt_id: str) -> Dict[str, Any]:
        deadline = time.monotonic() + _env_int("COMFYUI_TIMEOUT", 600)
        interval = 1.0
        while time.monotonic() < deadline:
            try:
                resp = self.session.get(
                    self._url(f"/history/{prompt_id}"), timeout=30
                )
                if resp.status_code == 200:
                    history = resp.json()
                    entry = history.get(prompt_id) if isinstance(history, dict) else None
                    if entry:
                        status = entry.get("status") or {}
                        if status.get("status_str") == "error":
                            raise GenerationError(
                                f"ComfyUI 执行失败: {status.get('messages')}",
                                provider=self.name, retryable=False,
                            )
                        images = self._extract_images(entry)
                        if images:
                            return images[0]
            except GenerationError:
                raise
            except Exception as e:
                log.warning(f"轮询 ComfyUI 状态失败（将重试）: {e}")
            time.sleep(interval)
            interval = min(interval + 0.5, 3.0)

        raise GenerationError(
            "等待 ComfyUI 生成超时（默认 600 秒）。"
            "首次加载模型可能较慢，可用 COMFYUI_TIMEOUT 调大。",
            provider=self.name,
        )

    @staticmethod
    def _extract_images(entry: Dict[str, Any]) -> list:
        found = []
        outputs = entry.get("outputs") or {}
        if isinstance(outputs, dict):
            for node_output in outputs.values():
                if not isinstance(node_output, dict):
                    continue
                for item in node_output.get("images") or []:
                    if isinstance(item, dict) and item.get("filename"):
                        found.append(item)
        return found

    def _download_image(self, image_info: Dict[str, Any]) -> bytes:
        params = {
            "filename": image_info.get("filename", ""),
            "subfolder": image_info.get("subfolder", ""),
            "type": image_info.get("type", "output"),
        }
        try:
            resp = self.session.get(
                self._url("/view"), params=params, timeout=60,
                stream=True, allow_redirects=False,
            )
        except requests.RequestException as e:
            raise GenerationError(f"从 ComfyUI 取图失败: {e}", provider=self.name) from e
        try:
            if resp.status_code != 200:
                raise GenerationError(
                    f"从 ComfyUI 取图返回 HTTP {resp.status_code}",
                    provider=self.name,
                )
            return read_bounded(resp)
        finally:
            resp.close()


if __name__ == "__main__":  # pragma: no cover - 手动自检入口
    p = ComfyUIProvider()
    print(f"ComfyUI: {p.base_url}")
    print(f"可用: {p.is_available(force=True)}")
    if p.is_available(force=True):
        try:
            print(f"checkpoint: {p._resolve_checkpoint(required=False) or '(无)'}")
        except GenerationError as e:
            print(f"checkpoint: 查询失败 - {e}")
