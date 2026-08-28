
#!/usr/bin/env python3
"""Model validation and compatibility checking for Live2D model packages.

校验工具集:
- ModelValidator: Cubism 4 模型完整性校验（参数、物理、表情、兼容性）
- ModelFileValidator: .model3.json / .moc3 / .cdi3.json 文件级校验
- ModelOptimizer: 模型优化检测（网格密度、纹理大小、参数绑定、物理配置）
"""

from live2d_builder.validator.model_validator import ModelValidator
from live2d_builder.validator.model_file_validator import (
    ModelFileValidator,
    validate_model_directory,
)
from live2d_builder.validator.model_optimizer import (
    ModelOptimizer,
    analyze_model,
    estimate_webp_savings,
)

__all__ = [
    "ModelValidator",
    "ModelFileValidator",
    "validate_model_directory",
    "ModelOptimizer",
    "analyze_model",
    "estimate_webp_savings",
]
