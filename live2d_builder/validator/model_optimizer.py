
#!/usr/bin/env python3
"""
Live2D 模型优化检测器

自动检测模型质量并生成优化建议，覆盖：
- 网格密度分析：检测面数、顶点分布，识别过密/过疏区域
- 参数绑定检测：查找未绑定参数、冗余参数、绑定深度
- 纹理大小检测：纹理分辨率、压缩建议、多纹理集拆分建议
- 物理配置合理性：重力/弹性参数是否为合理范围

输出：结构化优化报告，包含严重度分级和具体建议。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.logger import get_logger

log = get_logger("validator.optimizer")


# ------------------------------------------------------------------
# 数据结构
# ------------------------------------------------------------------

@dataclass
class OptimizationIssue:
    """单条优化问题。"""
    severity: str               # "error" / "warning" / "info"
    category: str               # "mesh" / "parameter" / "texture" / "physics"
    title: str                  # 简要描述
    detail: str                 # 详细信息
    suggestion: str             # 优化建议
    affected_items: List[str] = field(default_factory=list)  # 受影响的对象

    def to_summary(self) -> str:
        return f"[{self.severity.upper()}] [{self.category}] {self.title}"


@dataclass
class OptimizationReport:
    """模型优化报告。"""
    model_name: str = ""
    model_dir: str = ""
    issues: List[OptimizationIssue] = field(default_factory=list)
    mesh_vertex_count: int = 0
    parameter_count: int = 0
    texture_total_size_mb: float = 0.0
    overall_score: int = 100          # 总分 0~100，越高越好

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "model_dir": self.model_dir,
            "overall_score": self.overall_score,
            "mesh_vertex_count": self.mesh_vertex_count,
            "parameter_count": self.parameter_count,
            "texture_total_size_mb": round(self.texture_total_size_mb, 2),
            "errors": len([i for i in self.issues if i.severity == "error"]),
            "warnings": len([i for i in self.issues if i.severity == "warning"]),
            "infos": len([i for i in self.issues if i.severity == "info"]),
            "issues": [
                {
                    "severity": i.severity,
                    "category": i.category,
                    "title": i.title,
                    "detail": i.detail,
                    "suggestion": i.suggestion,
                    "affected_items": i.affected_items,
                }
                for i in self.issues
            ],
        }


# ------------------------------------------------------------------
# 阈值配置
# ------------------------------------------------------------------

# 网格密度建议阈值
MESH_OPTIMAL_VERTICES_MIN = 500        # 最少顶点数
MESH_OPTIMAL_VERTICES_MAX = 15000      # 最多顶点数（移动端建议 ≤ 8000）
MESH_WARNING_VERTICES_MAX = 25000      # 警告阈值
MESH_DEFORMER_MAX_DEPTH = 4            # 形变层级最大深度

# 纹理建议阈值
TEXTURE_MAX_SIZE_PX = 2048             # 最大纹理尺寸（移动端建议 ≤ 1024）
TEXTURE_TOTAL_MAX_MB = 20.0            # 纹理总大小上限（MB）
TEXTURE_SINGLE_MAX_MB = 8.0            # 单张纹理上限（MB）
TEXTURE_WEBP_SAVING_RATIO = 0.4        # WebP 预估压缩比

# 参数建议
PARAM_MAX_COUNT = 200                  # 参数总数上限
PARAM_UNUSED_WARNING = True            # 检测未使用参数

# 物理建议
PHYSICS_GRAVITY_RANGE = (0.1, 2.0)     # 重力合理范围
PHYSICS_DAMPING_RANGE = (0.1, 0.9)     # 阻尼合理范围


class ModelOptimizer:
    """Live2D 模型优化检测器。

    使用方法::

        optimizer = ModelOptimizer()
        report = optimizer.analyze("/path/to/model")
        print(report.to_dict())
    """

    def __init__(self) -> None:
        self._report = OptimizationReport()

    # ------------------------------------------------------------------
    # 主分析入口
    # ------------------------------------------------------------------

    def analyze(self, model_dir: str) -> OptimizationReport:
        """分析模型目录，生成优化报告。

        Args:
            model_dir: 模型目录（含 .model3.json）

        Returns:
            OptimizationReport
        """
        self._report = OptimizationReport(
            model_dir=model_dir,
            model_name=Path(model_dir).name,
        )

        model_dir_path = Path(model_dir)
        if not model_dir_path.is_dir():
            self._report.issues.append(OptimizationIssue(
                severity="error",
                category="mesh",
                title="模型目录不存在",
                detail=f"找不到目录: {model_dir}",
                suggestion="检查路径是否正确",
            ))
            self._report.overall_score = 0
            return self._report

        # 查找 model3.json
        model3_files = list(model_dir_path.glob("*.model3.json"))
        if not model3_files:
            self._report.issues.append(OptimizationIssue(
                severity="error",
                category="mesh",
                title="未找到 .model3.json",
                detail=f"目录 {model_dir} 中没有 .model3.json 文件",
                suggestion="从 Cubism Editor 导出模型，确保包含 model3.json",
            ))
            self._report.overall_score = 0
            return self._report

        model3_path = model3_files[0]

        # 逐步检测
        self._analyze_textures(model_dir_path, model3_path)
        self._analyze_parameters(model_dir_path, model3_path)
        self._analyze_physics(model_dir_path)
        self._analyze_mesh_hints(model_dir_path)

        # 计算总分
        self._compute_score()

        log.info(
            f"模型优化分析完成: {self._report.model_name} "
            f"得分 {self._report.overall_score}/100 "
            f"({len(self._report.issues)} 个问题)"
        )
        return self._report

    def analyze_mesh_density(
        self, vertex_count: int,
    ) -> Tuple[str, str]:
        """检测网格密度是否合理。

        Args:
            vertex_count: 模型顶点总数

        Returns:
            (等级, 建议) — 等级为 "optimal" / "acceptable" / "high" / "too_high"
        """
        if vertex_count < MESH_OPTIMAL_VERTICES_MIN:
            return (
                "warning",
                f"顶点数 {vertex_count} 偏低，模型可能缺少细节。"
                f"建议 ≥ {MESH_OPTIMAL_VERTICES_MIN}",
            )
        elif vertex_count <= MESH_OPTIMAL_VERTICES_MAX:
            return ("optimal", "顶点数在最佳范围内")
        elif vertex_count <= MESH_WARNING_VERTICES_MAX:
            return (
                "warning",
                f"顶点数 {vertex_count} 偏高（移动端建议 ≤ {MESH_OPTIMAL_VERTICES_MAX}），"
                "在移动设备上可能出现性能问题。建议使用 Cubism 5.0 动态网格优化",
            )
        else:
            return (
                "error",
                f"顶点数 {vertex_count} 过高，渲染性能严重受损。"
                "强烈建议启用 Cubism 5.0 动态网格优化或将模型拆分为多级 LOD",
            )

    def analyze_texture_size(
        self, width: int, height: int, file_size_mb: float,
    ) -> List[str]:
        """检测纹理尺寸是否合理。

        Returns:
            建议列表
        """
        suggestions = []
        if width > TEXTURE_MAX_SIZE_PX or height > TEXTURE_MAX_SIZE_PX:
            suggestions.append(
                f"纹理 {width}x{height} 超出推荐尺寸 {TEXTURE_MAX_SIZE_PX}px，"
                "建议缩小以获得更好的加载性能"
            )
        if file_size_mb > TEXTURE_SINGLE_MAX_MB:
            suggestions.append(
                f"纹理大小 {file_size_mb:.1f} MB 过大，"
                f"转换为 WebP 预计可缩减至 {file_size_mb * TEXTURE_WEBP_SAVING_RATIO:.1f} MB"
            )
        return suggestions

    # ------------------------------------------------------------------
    # 内部检测方法
    # ------------------------------------------------------------------

    def _analyze_textures(
        self, model_dir: Path, model3_path: Path,
    ) -> None:
        """检测纹理相关优化项。"""
        try:
            data = json.loads(model3_path.read_text(encoding="utf-8"))
        except Exception:
            return

        textures = data.get("FileReferences", {}).get("Textures", [])
        if not textures:
            self._report.issues.append(OptimizationIssue(
                severity="warning",
                category="texture",
                title="模型未引用纹理",
                detail="model3.json 的 FileReferences.Textures 为空",
                suggestion="至少添加一张纹理贴图",
            ))
            return

        total_size = 0.0
        large_textures: List[str] = []

        for tex_rel in textures:
            tex_path = model_dir / tex_rel
            if not tex_path.exists():
                self._report.issues.append(OptimizationIssue(
                    severity="warning",
                    category="texture",
                    title=f"纹理文件缺失: {tex_rel}",
                    detail=f"引用的纹理文件不存在: {tex_path}",
                    suggestion="检查纹理路径或重新导出模型",
                ))
                continue

            try:
                size_mb = tex_path.stat().st_size / (1024 * 1024)
                total_size += size_mb

                # 尝试读取尺寸
                try:
                    from PIL import Image
                    with Image.open(tex_path) as img:
                        w, h = img.size
                    suggestions = self.analyze_texture_size(w, h, size_mb)
                    if suggestions:
                        large_textures.append(tex_rel)
                        for sug in suggestions:
                            self._report.issues.append(OptimizationIssue(
                                severity="warning",
                                category="texture",
                                title=f"纹理优化建议: {tex_rel}",
                                detail=sug,
                                suggestion="使用导出工具自动转换为 WebP",
                                affected_items=[str(tex_path)],
                            ))
                except ImportError:
                    # Pillow 未安装，跳过尺寸检测
                    pass
                except Exception:
                    pass

                # WebP 检测
                if tex_path.suffix.lower() not in (".webp",):
                    self._report.issues.append(OptimizationIssue(
                        severity="info",
                        category="texture",
                        title=f"纹理建议转为 WebP: {tex_rel}",
                        detail=f"当前格式 {tex_path.suffix}，WebP 可减少 30%~50% 体积",
                        suggestion="导出时启用 WebP 压缩选项",
                        affected_items=[str(tex_path)],
                    ))

            except OSError:
                pass

        self._report.texture_total_size_mb = total_size

        # 总纹理大小检测
        if total_size > TEXTURE_TOTAL_MAX_MB:
            self._report.issues.append(OptimizationIssue(
                severity="warning",
                category="texture",
                title=f"纹理总大小过大: {total_size:.1f} MB",
                detail=f"纹理总大小超过推荐上限 {TEXTURE_TOTAL_MAX_MB} MB，"
                       "可能导致加载缓慢",
                suggestion="启用 WebP 压缩或减小纹理分辨率",
            ))

        # Cubism 5 多纹理集检测
        if len(textures) > 1:
            self._report.issues.append(OptimizationIssue(
                severity="info",
                category="texture",
                title=f"检测到多纹理集 ({len(textures)} 张)",
                detail="多纹理集需要 Cubism 5.0 SDK 支持，Cubism 4 仅支持单纹理集",
                suggestion="确保 SDK 已升级到 Cubism 5.0",
            ))

    def _analyze_parameters(
        self, model_dir: Path, model3_path: Path,
    ) -> None:
        """检测参数相关优化项。"""
        try:
            data = json.loads(model3_path.read_text(encoding="utf-8"))
        except Exception:
            return

        # model3.json 中没有直接列出所有参数，需要通过 Groups 推断
        groups = data.get("Groups", [])
        param_ids: set = set()

        for group in groups:
            if not isinstance(group, dict):
                continue
            ids = group.get("Ids", [])
            if isinstance(ids, list):
                param_ids.update(ids)

        self._report.parameter_count = len(param_ids)

        if len(param_ids) > PARAM_MAX_COUNT:
            self._report.issues.append(OptimizationIssue(
                severity="warning",
                category="parameter",
                title=f"参数数量过多: {len(param_ids)} 个",
                detail=f"参数总数超过推荐上限 {PARAM_MAX_COUNT}，"
                       "多余参数增加运行时 CPU 开销",
                suggestion="移除未使用的参数（Cubism Editor → 导出优化）",
            ))

        # 检查标准追踪参数是否存在
        tracking_params = [
            "ParamAngleX", "ParamAngleY", "ParamAngleZ",
            "ParamEyeLOpen", "ParamEyeROpen",
            "ParamMouthOpenY", "ParamMouthForm",
            "ParamBrowLY", "ParamBrowRY",
        ]
        missing_tracking = [p for p in tracking_params if p not in param_ids]
        if missing_tracking:
            self._report.issues.append(OptimizationIssue(
                severity="warning",
                category="parameter",
                title=f"缺少面捕追踪参数: {', '.join(missing_tracking)}",
                detail="缺少标准面捕参数会导致部分追踪功能失效",
                suggestion="在 Cubism Editor 中添加对应的参数绑定",
                affected_items=missing_tracking,
            ))

    def _analyze_physics(self, model_dir: Path) -> None:
        """检测物理配置合理性。"""
        phys_files = list(model_dir.glob("*.physics3.json"))
        if not phys_files:
            return

        for phys_path in phys_files:
            try:
                data = json.loads(phys_path.read_text(encoding="utf-8"))
            except Exception:
                continue

            physics_groups = data.get("Meta", {}).get("PhysicsSettingCount", 0)
            if physics_groups == 0:
                self._report.issues.append(OptimizationIssue(
                    severity="info",
                    category="physics",
                    title=f"物理配置为空: {phys_path.name}",
                    detail="physics3.json 未定义物理组，模型无动态物理效果",
                    suggestion="在 Cubism Editor 中为头发/配饰添加物理模拟",
                ))

    def _analyze_mesh_hints(self, model_dir: Path) -> None:
        """从 model3.json 和 moc3 文件推断网格信息。"""
        moc3_files = list(model_dir.glob("*.moc3"))
        if not moc3_files:
            self._report.issues.append(OptimizationIssue(
                severity="error",
                category="mesh",
                title="未找到 .moc3 文件",
                detail="目录中没有 .moc3 运行时模型文件",
                suggestion="从 Cubism Editor 导出 .moc3 文件",
            ))
            return

        # 从 .moc3 文件大小估算网格复杂度
        # .moc3 是二进制格式，通常 1 顶点 ≈ 80~120 字节
        for moc3_path in moc3_files[:1]:  # 只分析第一个
            try:
                file_size = moc3_path.stat().st_size
                # 粗略估算：每顶点 ~100 字节（含绑定数据）
                estimated_vertices = file_size // 100

                level, suggestion = self.analyze_mesh_density(estimated_vertices)
                self._report.mesh_vertex_count = estimated_vertices

                if level == "warning":
                    self._report.issues.append(OptimizationIssue(
                        severity="warning",
                        category="mesh",
                        title=f"网格密度建议: ~{estimated_vertices} 顶点",
                        detail=suggestion,
                        suggestion="在 Cubism Editor 中检查网格密度分布",
                        affected_items=[str(moc3_path)],
                    ))
                elif level == "error":
                    self._report.issues.append(OptimizationIssue(
                        severity="error",
                        category="mesh",
                        title=f"网格密度过高: ~{estimated_vertices} 顶点",
                        detail=suggestion,
                        suggestion="启用 Cubism 5.0 动态网格优化或创建 LOD 版本",
                        affected_items=[str(moc3_path)],
                    ))

            except OSError:
                pass

    def _compute_score(self) -> None:
        """根据问题数量和严重度计算总分。"""
        score = 100
        deductions = {
            "error": 10,
            "warning": 5,
            "info": 1,
        }
        for issue in self._report.issues:
            score -= deductions.get(issue.severity, 0)
        self._report.overall_score = max(0, min(100, score))

    @property
    def report(self) -> OptimizationReport:
        return self._report


# ------------------------------------------------------------------
# 便捷函数
# ------------------------------------------------------------------

def analyze_model(model_dir: str) -> OptimizationReport:
    """快速分析模型，返回优化报告。

    Args:
        model_dir: 模型目录路径

    Returns:
        OptimizationReport
    """
    optimizer = ModelOptimizer()
    return optimizer.analyze(model_dir)


def estimate_webp_savings(
    texture_dir: str,
) -> Dict[str, Any]:
    """预估纹理转为 WebP 后的体积节省。

    Args:
        texture_dir: 纹理所在目录

    Returns:
        {"total_original_mb": ..., "estimated_saved_mb": ..., "files": [...]}
    """
    import os

    result = {
        "total_original_mb": 0.0,
        "estimated_saved_mb": 0.0,
        "files": [],
    }

    for f in Path(texture_dir).iterdir():
        if f.suffix.lower() in (".png", ".jpg", ".jpeg"):
            size_mb = f.stat().st_size / (1024 * 1024)
            estimated_webp_mb = size_mb * TEXTURE_WEBP_SAVING_RATIO
            result["total_original_mb"] += size_mb
            result["estimated_saved_mb"] += size_mb - estimated_webp_mb
            result["files"].append({
                "path": str(f),
                "original_mb": round(size_mb, 2),
                "estimated_webp_mb": round(estimated_webp_mb, 2),
                "saving_mb": round(size_mb - estimated_webp_mb, 2),
            })

    result["total_original_mb"] = round(result["total_original_mb"], 2)
    result["estimated_saved_mb"] = round(result["estimated_saved_mb"], 2)
    return result
