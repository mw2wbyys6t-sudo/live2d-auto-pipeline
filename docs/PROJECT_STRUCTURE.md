# 📁 Live2D Master Agent - 项目结构说明

本文档详细说明了项目的文件结构和每个文件的用途。

---

## 📂 目录结构

```
live2d-master-agent/
├── 📄 README.md                    # 项目主文档
├── 📄 LICENSE                      # MIT许可证
├── 📄 requirements.txt             # Python依赖
├── 📄 .gitignore               # Git忽略配置
├── 📄 .env.example             # 环境变量示例
│
├── 📁 docs/                      # 文档目录
│   └── 📄 RIGGING_GUIDE.md      # Live2D绑定指南
│
├── 📁 scripts/                   # 辅助脚本
│   ├── 📄 auto_naming.py          # 自动命名工具
│   ├── 📄 layer_checker.py        # 图层检查工具
│   ├── 📄 parameter_designer_enhanced.py # 参数设计器
│   ├── 📄 physics_helper.py       # 物理设置助手
│   ├── 📄 qa_engine_enhanced.py  # 质量检查引擎
│   └── 📄 seedream_image_generate.py # Seedream图像生成
│
├── 📁 prompts/                  # 提示词模板
│   ├── 📄 image_generation.md     # 图像生成提示词
│   ├── 📄 naming.md             # 命名提示词
│   ├── 📄 physics.md            # 物理提示词
│   ├── 📄 qa.md               # 质量检查提示词
│   ├── 📄 rigging.md          # 绑定提示词
│   └── 📄 split.md            # 分层提示词
│
├── 📁 templates/               # 模板文件
│   ├── 📄 cubism_params.md     # Cubism参数模板
│   ├── 📄 export_rules.md     # 导出规则模板
│   └── 📄 psd_structure.md    # PSD结构模板
│
├── 📁 docs/                    # 文档目录
│
├── 📄 master_tool.py              # 🎯 主工具（v6.1）
├── 📄 live2d_layer_v6.py         # 🎯 v6.0分层工具（K-means）
├── 📄 live2d_layer_pro.py       # 🎯 v5.0分层工具（简单）
├── 📄 install_comfyui_advanced.py # 🎯 See-through安装器
├── 📄 install_comfyui.py         # ComfyUI安装器（旧版）
├── 📄 config_api.py          # API配置工具
├── 📄 config.py             # 配置文件
├── 📄 comfyui_integration.py    # ComfyUI集成
├── 📁 tests/                    # 测试脚本目录
│   ├── 📄 create_test_image.py   # 测试图像生成工具
│   ├── 📄 test_workflow.py       # 工作流基础测试
│   ├── 📄 test_full_coverage.py  # 功能全覆盖测试
│   └── 📄 test_deep_coverage.py  # 30 项深度测试
│
├── 📄 AI_LAYERING_GUIDE.md     # AI分层指南
├── 📄 BEST_PRACTICES.md        # 最佳实践
├── 📄 CHANGELOG.md           # 更新日志
├── 📄 FAQ.md                 # 常见问题
├── 📄 GITHUB_RESEARCH.md        # GitHub研究报告
├── 📄 LIMITATIONS.md          # 已知限制
├── 📄 QUICKSTART.md         # 快速入门
├── 📄 SEE_THROUGH_INTEGRATION.md # See-through集成指南
├── 📄 SKILL.md               # Skill文档
└── 📄 USER_GUIDE.md          # 用户指南
```

---

## 📄 核心工具详解

### 🏆 主要工具

| 文件名 | 版本 | 说明 | 推荐度 |
|--------|------|------|--------|
| **[master_tool.py](master_tool.py)** | v6.1 | 一站式主工具，集成所有功能 | ⭐⭐⭐⭐⭐ |
| **[live2d_layer_v6.py](live2d_layer_v6.py)** | v6.0 | K-means聚类分层工具 | ⭐⭐⭐⭐ |
| **[install_comfyui_advanced.py](install_comfyui_advanced.py)** | v2.0 | See-through安装器 | ⭐⭐⭐⭐⭐ |
| **[live2d_layer_pro.py](live2d_layer_pro.py)** | v5.0 | 简单颜色检测分层 | ⭐⭐⭐ |

### 🔧 辅助工具

| 文件名 | 说明 |
|--------|------|
| [config_api.py](config_api.py) | API配置工具 |
| [tests/create_test_image.py](tests/create_test_image.py) | 测试图像生成 |

---

## 📚 文档文件

| 文件名 | 内容 |
|--------|------|
| [README.md](README.md) | 项目主文档 |
| [QUICKSTART.md](QUICKSTART.md) | 快速入门指南（3分钟） |
| [USER_GUIDE.md](USER_GUIDE.md) | 完整使用教程 |
| [SEE_THROUGH_INTEGRATION.md](SEE_THROUGH_INTEGRATION.md) | See-through集成指南 |
| [FAQ.md](FAQ.md) | 常见问题解答 |
| [BEST_PRACTICES.md](BEST_PRACTICES.md) | 最佳实践和技巧 |
| [LIMITATIONS.md](LIMITATIONS.md) | 已知限制说明 |
| [CHANGELOG.md](CHANGELOG.md) | 更新日志 |
| [GITHUB_RESEARCH.md](GITHUB_RESEARCH.md) | GitHub项目研究报告 |
| [SKILL.md](SKILL.md) | Skill文档 |

---

## 📁 目录说明

### docs/
详细文档目录

### scripts/
辅助脚本集合
- 质量检查、参数设计、物理设置等工具

### prompts/
AI提示词模板
- 图像生成、命名、物理等

### templates/
工作模板文件
- Cubism参数、导出规则、PSD结构

---

## 🎯 工作流程建议

### 1️⃣ 快速开始
```bash
# 1. 安装
python master_tool.py "cute anime girl"
```

### 2️⃣ 专业分层
```bash
# 1. 安装 See-through
python install_comfyui_advanced.py
# 2. 使用工作流
```

### 3️⃣ 测试工具
```bash
# 生成测试图像
python tests/create_test_image.py
# 使用v6分层
python live2d_layer_v6.py test_character.png
```

---

## 🔧 配置文件
