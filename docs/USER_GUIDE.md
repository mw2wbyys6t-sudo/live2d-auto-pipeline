# 📖 Live2D Master Agent - 完整使用教程

**详细指南，让你成为Live2D大师！**

---

## 📋 目录

1. [工具介绍](#工具介绍)
2. [基础使用](#基础使用)
3. [高级功能](#高级功能)
4. [配置选项](#配置选项)
5. [最佳实践](#最佳实践)

---

## 🛠️ 工具介绍

### 核心工具

#### 1. master_tool.py ⭐推荐
**一站式工具箱**，集成所有核心功能

```bash
python master_tool.py "your character description"
```

**功能**：
- AI图像生成（免费）
- 多样化特征组合
- PSD分层规划
- 质量检查

#### 2. live2d_layer_pro.py
**专业分层工具**，生成符合Live2D规范的PSD

```bash
python live2d_layer_pro.py character.png
```

**功能**：
- 25+图层自动分层
- 眼部细节分离
- 口型变化生成
- Live2D标准命名

#### 3. config_api.py
**API配置工具**，配置可选的付费API

```bash
python config_api.py
```

---

## 🎯 基础使用

### 生成角色立绘

#### 基本生成

```bash
python master_tool.py "cute anime girl with pink hair"
```

**推荐提示词结构**：
```
[角色描述] + [特征] + [风格] + [质量词]
```

**示例**：
```bash
# 清晰描述
python master_tool.py "beautiful anime girl, long pink hair, blue eyes, school uniform"

# 强调风格
python master_tool.py "chibi anime character, cute, pastel colors, soft lighting"

# 强调Live2D适用
python master_tool.py "anime girl, clean lineart, white background, perfect for Live2D"
```

#### 生成多个角色

```bash
# 生成5个不同角色
python master_tool.py -n 5 "anime girl"

# 生成10个角色
python master_tool.py -n 10 "cute catgirl"
```

系统会自动为每个角色组合不同的特征（发型、发色、服装等），确保每个都独一无二！

#### 使用已有图片

```bash
# 图片在当前目录
python master_tool.py --skip-generate

# 指定图片路径
python master_tool.py --skip-generate my_character.png
```

---

## 🔧 高级功能

### 多样化特征系统

系统会自动随机组合以下特征：

| 特征类型 | 示例选项 |
|---------|---------|
| 发型 | 长直发、双马尾、短发、丸子头... |
| 发色 | 粉色、紫色、蓝色、金色、银色... |
| 眼睛颜色 | 蓝色、绿色、粉色、红色、异瞳... |
| 服装 | 校服、和服、女仆装、泳装、西装... |
| 配饰 | 发带、眼镜、帽子、项链、耳环... |
| 表情 | 微笑、害羞、冷酷、惊讶、生气... |
| 姿势 | 站立、坐着、挥手、奔跑、跳舞... |

### 手动指定特征

**提示词示例**：
```bash
# 指定发型和发色
python master_tool.py "anime girl, long twintails, silver hair"

# 指定服装
python master_tool.py "anime girl, maid outfit, pink apron"

# 指定多个特征
python master_tool.py "anime girl with fox ears, white hair, red eyes, kimono"
```

### 质量优化提示词

在描述末尾添加这些词可以提升质量：

```bash
python master_tool.py "anime girl, best quality, masterpiece, ultra detailed"
```

**推荐质量词**：
- `best quality` - 最佳质量
- `masterpiece` - 杰作级
- `ultra detailed` - 超精细
- `perfect for Live2D` - 适合Live2D
- `clean lineart` - 干净线稿
- `white background` - 白色背景

---

## ⚙️ 配置选项

### 查看帮助

```bash
python master_tool.py --help
```

### 配置API（可选）

#### 为什么配置API？
- 更精细的图像控制
- 更高的生成质量
- 更快生成速度

#### 如何配置

```bash
python config_api.py
```

按照提示输入火山引擎API密钥。

**获取API密钥**：
1. 访问 https://www.volcengine.com/
2. 注册账号
3. 获取API密钥

#### 使用配置

配置后，系统会优先使用付费API。

### 环境变量配置

创建 `.env` 文件（参考 `.env.example`）：

```bash
# 可选：火山引擎API
ARK_API_KEY=your-api-key-here
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3

# 输出配置
OUTPUT_DIR=./output
MAX_PSD_SIZE_MB=50
```

---

## 💡 最佳实践

### 提示词技巧

#### ✅ 推荐做法

1. **具体描述**
   ```bash
   # ❌ 模糊
   python master_tool.py "girl"
   
   # ✅ 具体
   python master_tool.py "anime girl, long pink hair, blue eyes, white dress, standing pose"
   ```

2. **分层描述**
   ```bash
   # 从整体到细节
   "beautiful anime girl, long flowing hair, green eyes, school uniform, red bow in hair, smiling"
   ```

3. **添加风格词**
   ```bash
   "anime girl, detailed eyes, soft lighting, pastel colors, clean lineart"
   ```

#### ❌ 避免做法

1. **过长描述** - 50-100词最佳
2. **矛盾描述** - 不要同时说"可爱"和"恐怖"
3. **模糊词汇** - "好看的"不如"微笑"

### 角色设计技巧

#### 创建一致性角色系列

```bash
# 主角
python master_tool.py "anime girl, blue hair, determined eyes, red cape, hero outfit"

# 同系列角色
python master_tool.py "anime girl, blue hair, cheerful smile, blue dress, white apron"

# 反派
python master_tool.py "anime girl, blue hair, cold eyes, dark armor, villain"
```

#### 创建多样化团队

```bash
# 生成5个团队成员
python master_tool.py -n 5 "anime girl wizard, magical academy uniform"

# 每个都有独特发型和服装
```

### 分层技巧

#### 最佳分层图片特征

✅ **适合分层**：
- 清晰的前景/背景分离
- 单色或简单背景
- 清晰的轮廓线
- 无过多特效

❌ **难以分层**：
- 复杂背景
- 烟雾/火焰效果
- 低分辨率
- 模糊图片

#### 提高分层质量

1. **使用白色/纯色背景**
   ```bash
   python master_tool.py "anime girl, white background, clean lineart"
   ```

2. **指定清晰轮廓**
   ```bash
   python master_tool.py "anime girl, sharp edges, clear silhouette, isolated"
   ```

3. **避免过多细节**
   ```bash
   # ❌ 过多装饰
   "anime girl with 100 accessories"
   
   # ✅ 适度装饰
   "anime girl with hair ribbon and simple earrings"
   ```

---

## 🐛 故障排除

### 生成失败

**问题**：网络错误
```
Connection error, please try again
```

**解决方案**：
1. 检查网络连接
2. 等待几分钟后重试
3. 使用 `--skip-generate` 用已有图片

**问题**：生成图片模糊
```
Image quality issue
```

**解决方案**：
1. 添加质量提示词
2. 配置付费API
3. 使用更高分辨率提示

### 分层失败

**问题**：图层不准确
```
Layer segmentation issue
```

**解决方案**：
1. 使用更清晰的原图
2. 确保背景干净
3. 尝试不同角度的图片

### 安装问题

**问题**：缺少依赖
```
ModuleNotFoundError: No module named 'xxx'
```

**解决方案**：
```bash
pip install -r requirements.txt
```

---

## 📞 获取帮助

- 📖 查看 [QUICKSTART.md](QUICKSTART.md) - 快速入门
- ❓ 查看 [FAQ.md](FAQ.md) - 常见问题
- 💡 查看 [BEST_PRACTICES.md](BEST_PRACTICES.md) - 最佳实践
- 🐛 提交 [Issue](https://github.com/mw2wbyys6t-sudo/Live2D/issues) - 报告问题

---

## 🎉 下一步

恭喜你完成了完整教程！现在你可以：

- 🎨 创建独特的角色设计
- 📐 生成专业的PSD分层
- ⚡ 提高工作效率
- 💡 分享你的作品

**享受Live2D创作！** 🎨

---

*最后更新：2026-05-22*
*版本：v5.0*
