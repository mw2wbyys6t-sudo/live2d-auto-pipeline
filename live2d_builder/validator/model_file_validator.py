
#!/usr/bin/env python3
"""
Live2D 模型文件完整性校验器

校验范围：
- .model3.json 格式规范、文件引用完整性、纹理路径有效性
- .moc3 文件存在性与基本头部校验
- .cdi3.json 口型同步配置结构合法性
- .physics3.json 物理配置引用完整性
- 跨文件引用一致性检测（model3.json 中引用的文件是否真实存在）

用途：模型加载前自动检测，避免因配置错误导致运行时崩溃。
"""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.logger import get_logger

log = get_logger("validator.file")


class ModelFileValidator:
    """Live2D 模型文件完整性校验器。

    使用方法::

        validator = ModelFileValidator()
        ok, errors, warnings = validator.validate_model_package("/path/to/model")

    """

    # .model3.json 必需顶层字段
    REQUIRED_MODEL3_KEYS = ("Version", "FileReferences", "Groups")

    # FileReferences 必需字段
    REQUIRED_FILE_REFS = ("Moc", "Textures")

    # .cdi3.json 必需字段
    REQUIRED_CDI3_KEYS = ("Version", "Parameters")

    # .physics3.json 必需字段
    REQUIRED_PHYSICS3_KEYS = ("Version", "Meta")

    # 支持的纹理格式
    SUPPORTED_TEXTURE_FORMATS = {".png", ".webp", ".jpg", ".jpeg", ".dds"}

    # .moc3 文件魔术字节 (Cubism 4/5)
    MOC3_MAGIC = b"MOC3"

    # Cubism 5 扩展字段
    CUBISM5_OPTIONAL_KEYS = ("TextureAtlas", "Physics", "Pose", "DisplayInfo")

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def validate_model_package(
        self, model_dir: str
    ) -> Tuple[bool, List[str], List[str]]:
        """校验整个模型目录。

        Args:
            model_dir: 模型目录路径（包含 .model3.json 的目录）

        Returns:
            (ok, errors, warnings) — errors 为致命错误（模型无法加载），
            warnings 为优化建议。
        """
        all_errors: List[str] = []
        all_warnings: List[str] = []

        model_dir_path = Path(model_dir)
        if not model_dir_path.is_dir():
            return False, [f"目录不存在: {model_dir}"], []

        # 1. 查找 .model3.json
        model3_files = list(model_dir_path.glob("*.model3.json"))
        if not model3_files:
            return False, [f"目录中未找到 .model3.json 文件: {model_dir}"], []

        for model3_path in model3_files:
            ok, errs, warns = self.validate_model3(str(model3_path))
            all_errors.extend(errs)
            all_warnings.extend(warns)

            if not ok:
                continue

            # 2. 校验 .moc3 引用
            moc_ok, moc_errs = self._validate_moc3_reference(
                str(model3_path)
            )
            all_errors.extend(moc_errs)
            if not moc_ok:
                continue

            # 3. 校验纹理引用
            tex_errs, tex_warns = self._validate_texture_references(
                str(model3_path)
            )
            all_errors.extend(tex_errs)
            all_warnings.extend(tex_warns)

            # 4. 校验 .cdi3.json（如果存在）
            cdi3_path = self._find_companion_file(model3_path, ".cdi3.json")
            if cdi3_path:
                _, cdi3_errs, cdi3_warns = self.validate_cdi3(str(cdi3_path))
                all_errors.extend(cdi3_errs)
                all_warnings.extend(cdi3_warns)

            # 5. 校验 .physics3.json（如果存在）
            phys_path = self._find_companion_file(
                model3_path, ".physics3.json"
            )
            if phys_path:
                _, phys_errs = self._validate_physics3_reference(
                    str(model3_path), str(phys_path)
                )
                all_errors.extend(phys_errs)

        is_ok = len(all_errors) == 0
        return is_ok, all_errors, all_warnings

    def validate_model3(
        self, model3_path: str
    ) -> Tuple[bool, List[str], List[str]]:
        """校验单个 .model3.json 文件。

        Returns:
            (ok, errors, warnings)
        """
        errors: List[str] = []
        warnings: List[str] = []
        path = Path(model3_path)

        if not path.exists():
            return False, [f".model3.json 文件不存在: {path}"], []

        # JSON 解析
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return False, [f".model3.json JSON 格式错误: {exc}"], []
        except UnicodeDecodeError as exc:
            return False, [f".model3.json 编码错误（非 UTF-8）: {exc}"], []

        # 版本检查
        version = data.get("Version", 0)
        if version < 3:
            errors.append(
                f"model3.json Version={version} 过低，需要 ≥ 3 (Cubism 4+)"
            )
        if version >= 5:
            warnings.append(
                "model3.json 声明为 Cubism 5 格式，确保已适配 Cubism 5 SDK"
            )

        # 必需字段检查
        for key in self.REQUIRED_MODEL3_KEYS:
            if key not in data:
                errors.append(f"model3.json 缺少必需字段: {key}")

        # FileReferences 检查
        file_refs = data.get("FileReferences", {})
        for key in self.REQUIRED_FILE_REFS:
            if key not in file_refs:
                errors.append(
                    f"model3.json FileReferences 缺少: {key}"
                )

        # Moc 路径检查
        moc_path = file_refs.get("Moc", "")
        if moc_path and not moc_path.endswith(".moc3"):
            errors.append(
                f"Moc 文件扩展名异常: {moc_path}（期望 .moc3）"
            )

        # Textures 列表检查
        textures = file_refs.get("Textures", [])
        if not isinstance(textures, list):
            errors.append("FileReferences.Textures 应为数组")

        if len(textures) == 0:
            errors.append("FileReferences.Textures 为空，模型缺少纹理")
        elif len(textures) > 4 and version < 5:
            warnings.append(
                f"纹理数量 {len(textures)} 超过 4，Cubism 4 仅支持单纹理集；"
                "如需多纹理集需升级到 Cubism 5"
            )

        # 检查纹理路径格式
        for i, tex in enumerate(textures):
            if not isinstance(tex, str):
                errors.append(f"Textures[{i}] 不是字符串类型")
                continue
            if ".." in tex or tex.startswith("/"):
                errors.append(
                    f"Textures[{i}] 路径不安全: {tex}（含 .. 或绝对路径）"
                )

        return len(errors) == 0, errors, warnings

    def validate_cdi3(
        self, cdi3_path: str
    ) -> Tuple[bool, List[str], List[str]]:
        """校验 .cdi3.json 口型同步配置文件。

        Returns:
            (ok, errors, warnings)
        """
        errors: List[str] = []
        warnings: List[str] = []
        path = Path(cdi3_path)

        if not path.exists():
            return False, [f".cdi3.json 文件不存在: {path}"], []

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return False, [f".cdi3.json 解析失败: {exc}"], []

        # 版本检查
        version = data.get("Version", 0)
        if version < 1:
            errors.append(f"cdi3.json Version={version} 无效")

        # Parameters 检查
        params = data.get("Parameters", [])
        if not isinstance(params, list) or len(params) == 0:
            errors.append("cdi3.json Parameters 为空或格式错误")
            return False, errors, warnings

        # 检查每个参数组
        required_param_keys = {"Id", "GroupId", "Name"}
        for i, param_group in enumerate(params):
            if not isinstance(param_group, dict):
                errors.append(f"Parameters[{i}] 不是对象")
                continue
            for key in required_param_keys:
                if key not in param_group:
                    errors.append(f"Parameters[{i}] 缺少: {key}")

            # 检查 Blend 映射
            blends = param_group.get("Blend", [])
            if blends:
                for j, blend in enumerate(blends):
                    if "Id" not in blend or "Type" not in blend:
                        warnings.append(
                            f"Parameters[{i}].Blend[{j}] 定义不完整"
                        )

        return len(errors) == 0, errors, warnings

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _validate_moc3_reference(
        self, model3_path: str
    ) -> Tuple[bool, List[str]]:
        """校验 .moc3 引用文件存在性及基本头部。"""
        errors: List[str] = []
        path = Path(model3_path)
        base_dir = path.parent

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False, [f"无法读取 {model3_path}"]

        moc_rel = data.get("FileReferences", {}).get("Moc", "")
        if not moc_rel:
            errors.append("model3.json 中未指定 Moc 文件路径")
            return False, errors

        moc_path = base_dir / moc_rel
        if not moc_path.exists():
            errors.append(f"引用的 .moc3 文件不存在: {moc_path}")
            return False, errors

        # 基本头部校验
        try:
            with open(moc_path, "rb") as f:
                header = f.read(4)
            if header[:4] != self.MOC3_MAGIC:
                errors.append(
                    f".moc3 文件幻数错误，期望 {self.MOC3_MAGIC!r}，"
                    f"得到 {header[:4]!r}（可能已损坏或不是 .moc3 文件）"
                )
        except OSError as exc:
            errors.append(f"无法读取 .moc3 文件 {moc_path}: {exc}")

        return len(errors) == 0, errors

    def _validate_texture_references(
        self, model3_path: str
    ) -> Tuple[List[str], List[str]]:
        """校验 model3.json 中引用的纹理文件是否存在，并给出尺寸建议。"""
        errors: List[str] = []
        warnings: List[str] = []
        path = Path(model3_path)
        base_dir = path.parent

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return [f"无法读取 {model3_path}"], []

        textures = data.get("FileReferences", {}).get("Textures", [])
        for tex_rel in textures:
            tex_path = base_dir / tex_rel
            if not tex_path.exists():
                errors.append(f"纹理文件不存在: {tex_path}")
                continue

            # 检查格式
            ext = tex_path.suffix.lower()
            if ext not in self.SUPPORTED_TEXTURE_FORMATS:
                warnings.append(
                    f"纹理 {tex_rel} 格式为 {ext}，建议使用 .png 或 .webp"
                )

            # 检查尺寸
            try:
                size = tex_path.stat().st_size
                if size > 8 * 1024 * 1024:  # 8 MB
                    warnings.append(
                        f"纹理 {tex_rel} 过大 ({size / 1024 / 1024:.1f} MB)，"
                        "建议压缩或使用 WebP"
                    )
            except OSError:
                pass

            # 尝试读取图片尺寸（用 Pillow）
            try:
                from PIL import Image

                with Image.open(tex_path) as img:
                    w, h = img.size
                    if w > 4096 or h > 4096:
                        warnings.append(
                            f"纹理 {tex_rel} 尺寸 {w}x{h} 过大，"
                            "建议 ≤ 4096x4096"
                        )
            except Exception:
                pass  # Pillow 可能未安装或文件不是图片

        return errors, warnings

    def _validate_physics3_reference(
        self, model3_path: str, physics3_path: str
    ) -> Tuple[bool, List[str]]:
        """校验 physics3.json 文件。"""
        errors: List[str] = []
        path = Path(physics3_path)

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return False, [f"physics3.json 解析失败: {exc}"]

        for key in self.REQUIRED_PHYSICS3_KEYS:
            if key not in data:
                errors.append(f"physics3.json 缺少: {key}")

        return len(errors) == 0, errors

    def _find_companion_file(
        self, model3_path: Path, suffix: str
    ) -> Optional[Path]:
        """根据 model3.json 路径查找同名 companion 文件。

        例如 model3.json → name.cdi3.json（去掉 .model3.json 再加 suffix）。
        """
        stem = model3_path.name
        if stem.endswith(".model3.json"):
            base = stem[: -len(".model3.json")]
        else:
            base = model3_path.stem
        candidate = model3_path.parent / f"{base}{suffix}"
        return candidate if candidate.exists() else None


# ------------------------------------------------------------------
# 便捷函数
# ------------------------------------------------------------------

def validate_model_directory(model_dir: str) -> Dict[str, Any]:
    """快速校验模型目录，返回结构化结果。

    返回格式::

        {
            "ok": True/False,
            "errors": [...],
            "warnings": [...],
            "model3_files": [...],
            "total_issues": N
        }
    """
    validator = ModelFileValidator()
    ok, errors, warnings = validator.validate_model_package(model_dir)

    model_dir_path = Path(model_dir)
    model3_files = (
        [str(p) for p in model_dir_path.glob("*.model3.json")]
        if model_dir_path.is_dir()
        else []
    )

    return {
        "ok": ok,
        "errors": errors,
        "warnings": warnings,
        "model3_files": model3_files,
        "total_issues": len(errors) + len(warnings),
    }
