# 🚀 快速入门

## 安装

### Windows
1. 安装 Python 3.11+ 和 Node.js 18+
2. 双击 `install.bat`

### macOS / Linux
```bash
bash install.sh
```

### Docker
```bash
docker compose up -d
# Web: http://localhost:3000  API: http://localhost:8080
```

## 第一个角色

```bash
# 免费生成（Pollinations，无需 Key）
python -m core.cli generate "蓝发猫耳少女，白色背景" --deploy-desktop

# 运行桌宠
python -m core.cli pet

# Web 工作台
cd web && npm run dev  # http://localhost:3000
```

## Python SDK

```python
from core.workflow import WorkflowEngine

engine = WorkflowEngine(output_dir="./output", provider="auto")
result = engine.run(
    prompt="粉发双马尾少女，白色背景",
    deploy_desktop=True,
    use_semantic=True,
)
```

## 角色一致性

```python
from core.character.manager import CharacterManager
mgr = CharacterManager()
card = mgr.create_character(name="小樱", hair_color="#FFB7C5")
prompt = mgr.get_generation_prompt(card.character_id, "穿圣诞装")
```

## 实时面捕
```bash
python -m drivers.desktop_pet.runner --tracking
```

## LLM 对话
```bash
python -m core.cli chat
```
