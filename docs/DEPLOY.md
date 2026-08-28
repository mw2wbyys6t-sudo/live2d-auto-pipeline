# 🚀 部署指南

## Docker 部署（推荐）

```bash
# 克隆项目
git clone https://github.com/mw2wbyys6t-sudo/Live2D.git
cd Live2D

# 配置环境变量
cp .env.example .env
# 编辑 .env 添加 API Key（可选）

# 启动
docker compose up -d

# 查看日志
docker compose logs -f
```

访问 http://localhost:3000 使用 Web 工作台。

## 手动部署（服务器）

```bash
# 1. 安装系统依赖
sudo apt update && sudo apt install -y python3.11 python3-pip nodejs npm golang-go

# 2. 安装 Python 依赖
pip3 install -r requirements.txt

# 3. 编译 Go API
cd api && go build -o live2d-api . && cd ..

# 4. 构建前端
cd web && npm ci && npm run build && cd ..

# 5. 启动服务
./api/live2d-api &          # API on :8080
cd web && npx next start -p 3000 &  # Web on :3000
```

## 本地开发

```bash
# 一键安装开发环境
python install.py --dev

# 运行测试
python -m pytest tests/ -v
cd api && go test ./...
cd web && npm run build
```

## 环境变量

见 `.env.example`。关键配置：

| 变量 | 说明 | 默认 |
|------|------|------|
| GO_API_PORT | API 端口 | 8080 |
| OUTPUT_DIR | 输出目录 | ./output |
| ARK_API_KEY | 火山引擎 Key | 空 |
| OPENAI_API_KEY | OpenAI Key | 空 |
| REDIS_URL | Redis 连接 | 空(内存) |
