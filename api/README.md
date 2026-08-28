# 🚀 Live2D Master Agent API (Go Edition)

基于 Go 语言构建的高性能 API 服务，提供 Live2D 图片生成、PSD 分层、See-through 工作流等完整功能。

## ✨ 特性

- 🎯 **多源图片生成**：SD WebUI (本地) + Pollinations.ai (在线)
- 🔄 **智能降级**：SD WebUI 不可用时自动降级到 Pollinations
- 🎨 **PSD 分层规划**：自动创建 Live2D 兼容的分层结构
- 🏆 **See-through 集成**：SIGGRAPH 2026 级别 AI 分层
- 🔌 **Python 桥接**：与现有 Python 工具链无缝集成
- 📊 **健康监控**：实时检查所有服务状态

## 📦 项目结构

```
api/
├── main.go                    # 入口文件
├── go.mod                     # Go 模块定义
├── config/
│   └── config.go              # 配置管理
├── models/
│   └── models.go              # 数据模型
├── services/
│   ├── image_generator.go     # 图片生成服务
│   └── python_bridge.go       # Python 桥接服务
├── handlers/
│   └── handlers.go            # HTTP 处理器
├── live2d-api                 # 编译后的可执行文件
└── README.md                  # 本文档
```

## 🚀 快速开始

### 1. 编译

```bash
cd api/
go build -o live2d-api .
```

### 2. 启动服务

```bash
# 默认配置
./live2d-api

# 指定端口
./live2d-api -port 9090

# 指定主机
./live2d-api -host 127.0.0.1 -port 8080
```

### 3. 验证服务

```bash
curl http://localhost:8080/api/health
```

## 📡 API 端点

### 健康检查
```bash
GET /api/health
```

### 系统状态
```bash
GET /api/status
```

### 生成图片
```bash
POST /api/generate
Content-Type: application/json

{
  "prompt": "cute anime girl with pink hair",
  "width": 768,
  "height": 768,
  "seed": 12345,
  "use_sd_webui": true,
  "sd_webui_url": "http://127.0.0.1:7860"
}
```

**响应示例：**
```json
{
  "success": true,
  "message": "图片生成成功",
  "data": {
    "image_path": "/path/to/output.png",
    "image_url": "/output/filename.png",
    "seed": 12345,
    "width": 768,
    "height": 768,
    "source": "sd_webui",
    "created_at": "2026-05-29T13:20:32Z"
  }
}
```

### 创建 PSD 分层规划
```bash
POST /api/psd-plan
Content-Type: application/json

{
  "image_path": "/path/to/image.png",
  "use_ai": true
}
```

### 运行 See-through 工作流
```bash
POST /api/see-through
Content-Type: application/json

{
  "image_path": "/path/to/image.png",
  "comfyui_dir": "/path/to/comfyui"
}
```

### 获取 Python 脚本列表
```bash
GET /api/scripts
```

### 获取输出文件
```bash
GET /output/:filename
```

## ⚙️ 配置

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LIVE2D_API_HOST` | 服务器地址 | `0.0.0.0` |
| `LIVE2D_API_PORT` | 服务器端口 | `8080` |
| `LIVE2D_SD_WEBUI_URL` | SD WebUI 地址 | `http://127.0.0.1:7860` |
| `LIVE2D_PYTHON_PATH` | Python 路径 | `python3` |
| `LIVE2D_OUTPUT_DIR` | 输出目录 | `../output` |

### 配置文件

创建 `config.json`：

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8080
  },
  "sd_webui": {
    "base_url": "http://127.0.0.1:7860",
    "timeout": 300,
    "enabled": true
  },
  "python": {
    "python_path": "python3",
    "scripts_dir": ".."
  },
  "output": {
    "base_dir": "../output",
    "max_file_size": 52428800
  },
  "comfyui": {
    "base_dir": "../comfyui",
    "enabled": false
  }
}
```

使用配置文件启动：
```bash
./live2d-api -config config.json
```

## 🔌 前后端连通架构

```
┌─────────────────────────────────────────────────────────────┐
│                     前端 (Frontend)                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ Web UI      │  │ CLI Tool    │  │ Third-party App     │ │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘ │
└─────────┼────────────────┼────────────────────┼────────────┘
          │                │                    │
          └────────────────┴────────────────────┘
                           │
                    HTTP/REST API
                           │
┌──────────────────────────┼──────────────────────────────────┐
│                     Go API Service                           │
│  ┌───────────────────────┼────────────────────────────────┐ │
│  │  /api/generate        │  多源图片生成引擎               │ │
│  │  /api/psd-plan        │  PSD 分层规划                   │ │
│  │  /api/see-through     │  See-through 工作流             │ │
│  │  /api/status          │  系统状态监控                   │ │
│  └───────────────────────┼────────────────────────────────┘ │
└──────────────────────────┼──────────────────────────────────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
    ┌─────────▼─────────┐  │  ┌─────────▼─────────┐
    │ SD WebUI (本地)    │  │  │ Python 桥接层      │
    │ http://localhost   │  │  │ master_tool.py     │
    └────────────────────┘  │  │ live2d_layer_*.py  │
                            │  └────────────────────┘
    ┌────────────────────┐  │
    │ Pollinations.ai    │  │
    │ (在线降级)         │  │
    └────────────────────┘  │
                            │
              ┌─────────────▼─────────────┐
              │     ComfyUI + See-through  │
              │     (SIGGRAPH 2026)        │
              └────────────────────────────┘
```

## 🧪 测试示例

### 测试图片生成

```bash
# 使用 Pollinations（默认）
curl -X POST http://localhost:8080/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "cute anime girl with blue eyes",
    "width": 768,
    "height": 768
  }'

# 尝试使用 SD WebUI
curl -X POST http://localhost:8080/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "beautiful anime character",
    "use_sd_webui": true
  }'
```

### 测试 PSD 分层

```bash
curl -X POST http://localhost:8080/api/psd-plan \
  -H "Content-Type: application/json" \
  -d '{
    "image_path": "/path/to/your/image.png"
  }'
```

### 查看生成的图片

```bash
# 获取图片 URL 后，通过浏览器或 curl 访问
curl http://localhost:8080/output/live2d_poll_xxx_xxx.png -o output.png
```

## 🔧 故障排除

### SD WebUI 连接失败

```bash
# 检查 SD WebUI 是否运行
curl http://127.0.0.1:7860/sdapi/v1/health

# 启动 SD WebUI
cd /path/to/stable-diffusion-webui
python launch.py --api --listen
```

### Python 依赖缺失

```bash
# 安装 Python 依赖
pip install -r ../requirements.txt
```

### 端口被占用

```bash
# 使用其他端口
./live2d-api -port 9090
```

## 📈 性能优化

- Go 的并发模型支持高并发请求
- 图片生成使用异步处理
- 支持连接池和超时控制
- 静态文件直接由 Gin 提供

## 🤝 与 Python 工具链集成

Go API 服务通过以下方式与现有 Python 工具集成：

1. **直接 HTTP 调用**：调用 SD WebUI API
2. **子进程执行**：运行 Python 脚本进行分层
3. **文件系统共享**：共享 output/ 目录

## 📄 许可证

与主项目相同，详见 [LICENSE](../LICENSE)
