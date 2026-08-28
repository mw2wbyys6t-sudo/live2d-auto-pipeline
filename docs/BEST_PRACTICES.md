# 💡 Live2D Master Agent - 最佳实践指南

**专业技巧，让你的作品更出色！**

---

## 📋 目录

1. [提示词工程](#提示词工程)
2. [角色设计](#角色设计)
3. [分层优化](#分层优化)
4. [工作流程](#工作流程)
5. [效率提升](#效率提升)

---

## 🎯 提示词工程

### 基础结构

**完美提示词公式**：
```
[主体] + [详细特征] + [风格/氛围] + [质量修饰词]
```

### 示例对比

#### ❌ 糟糕的提示词
```bash
python master_tool.py "girl"
```

**结果**：模糊、不准确的生成

#### ✅ 优秀的提示词
```bash
python master_tool.py "beautiful anime girl, long flowing pink hair, bright blue eyes, wearing white dress with blue ribbon, standing pose, soft lighting, pastel colors, clean lineart, white background, best quality, masterpiece"
```

**结果**：清晰、高质量的角色立绘

---

### 关键词类别

#### 主体描述词
| 类别 | 推荐词汇 |
|------|---------|
| 角色类型 | anime girl, chibi character, realistic girl |
| 性别 | female, male, gender-neutral |
| 年龄感 | young, mature, childlike |

#### 特征描述词
| 类别 | 推荐词汇 |
|------|---------|
| 发型 | long hair, short hair, twin tails, ponytail |
| 发色 | pink, silver, blue, red, blonde |
| 眼睛 | large eyes, detailed eyes, heterochromia |
| 服装 | school uniform, casual, kimono, dress |
| 配饰 | hair ribbon, glasses, hat, earrings |

#### 风格词
| 类别 | 推荐词汇 |
|------|---------|
| 整体风格 | anime style, soft style, realistic |
| 光线 | soft lighting, dramatic lighting, natural light |
| 色彩 | pastel colors, vibrant colors, muted tones |
| 背景 | white background, simple background, transparent |

#### 质量词
| 类别 | 推荐词汇 |
|------|---------|
| 质量 | best quality, high quality, ultra detailed |
| 细节 | detailed, intricate, clean |
| Live2D | perfect for Live2D, rigging ready, clean lineart |

---

### 高级技巧

#### 1. 使用权重强调
```bash
# 强调某个特征（通过位置和重复）
python master_tool.py "anime girl, pink pink pink hair, blue eyes"
```

#### 2. 负面提示（虽然工具不直接支持，但可以思考）
- 避免描述你不想要的特征
- 明确你想要的内容

#### 3. 组合多个参考
```bash
# 描述组合
python master_tool.py "anime girl combining elegant grace of Japanese kimono with modern school uniform style"
```

---

## 🎨 角色设计

### 创建一致的角色系列

#### 主角设计
```bash
python master_tool.py "anime hero, blue hair, determined eyes, red cape, heroic pose, golden armor accents, confident expression"
```

#### 同世界观角色
```bash
# 导师
python master_tool.py "anime mentor, long white hair, wise eyes, traditional robes, mystical staff"

# 队友
python master_tool.py "anime companion, short green hair, cheerful smile, light armor, friendly pose"

# 反派
python master_tool.py "anime villain, dark purple hair, cold eyes, black armor, menacing aura"
```

### 创建多样化团队

```bash
# 生成5个角色
python master_tool.py -n 5 "anime mage, magical academy uniform, mystical atmosphere"

# 确保团队多样性
# 角色1: 白发红眼
# 角色2: 蓝发绿眼
# 角色3: 粉发金眼
# 角色4: 黑发紫眼
# 角色5: 绿发蓝眼
```

### 避免"撞衫"技巧

系统自动随机94个特征组合，但你可以：

1. **指定核心特征**
   ```bash
   # 确保每个角色都有独特标识
   python master_tool.py "anime girl with fox features, orange fur, fluffy tail"
   ```

2. **指定服装风格**
   ```bash
   python master_tool.py "anime girl, cyberpunk outfit, neon lights, futuristic"
   ```

3. **指定特殊元素**
   ```bash
   python master_tool.py "anime girl, angel wings, holy aura, divine pose"
   ```

---

## 📐 分层优化

### Live2D友好图片特征

#### ✅ 最佳分层条件

| 特征 | 说明 |
|------|------|
| 背景 | 纯白或简单背景 |
| 轮廓 | 清晰、锐利的边缘 |
| 分辨率 | 1024x1024 或更高 |
| 对比度 | 主体与背景明显区分 |
| 线稿 | 干净、清晰的线条 |

#### ❌ 避免的特征

| 特征 | 问题 |
|------|------|
| 复杂背景 | 难以分离主体 |
| 烟雾/特效 | 图层混乱 |
| 低分辨率 | 分层不准确 |
| 模糊图片 | 边缘不清晰 |
| 过多装饰 | 增加分层难度 |

### 生成Live2D专用图片

```bash
# 强调Clean Lineart
python master_tool.py "anime girl, clean lineart, sharp edges, no shading, minimalist style"

# 强调白色背景
python master_tool.py "anime girl, pure white background, no background elements, isolated character"

# 强调清晰轮廓
python master_tool.py "anime girl, clear silhouette, distinct layers, separated hair strands"
```

### 分层前预处理

如果原图不够理想：

1. **使用PS清理背景**
   - 删除复杂背景
   - 调整为纯白背景

2. **提高对比度**
   - 增强主体与背景分离

3. **锐化边缘**
   - 让分层更准确

4. **调整分辨率**
   - 确保足够清晰

---

## ⚙️ 工作流程

### 推荐的完整工作流

#### 阶段1：概念设计（5分钟）
```bash
# 生成多个草稿
python master_tool.py -n 10 "anime girl character concept"

# 评估并选择最佳
# 考虑：独特性、可分层性、风格一致性
```

#### 阶段2：精细生成（1分钟）
```bash
# 基于选定的概念，添加细节
python master_tool.py "anime girl, detailed concept, best quality, white background, clean lineart"
```

#### 阶段3：分层处理（1分钟）
```bash
# 生成PSD分层
python live2d_layer_pro.py selected_character.png
```

#### 阶段4：导入Live2D Cubism
```bash
# 使用生成的PSD
# 在Cubism中打开
# 进行Rigging
```

### 不同场景工作流

#### VTuber角色创建
1. 生成多个候选角色（-n 10）
2. 选择最具辨识度的设计
3. 优化为Live2D专用
4. 分层并导入Cubism
5. 完成Rigging

#### 游戏角色设计
1. 确定角色定位（主角/NPC/Boss）
2. 生成符合定位的变体
3. 创建角色变体系列
4. 统一风格和分层规范
5. 批量分层处理

#### 动画项目
1. 创建角色设计规范
2. 生成符合规范的多个角色
3. 确保系列一致性
4. 统一分层结构
5. 批量处理

---

## ⚡ 效率提升

### 批量生成技巧

#### 1. 预设提示词模板
创建常用提示词库：

```bash
# 模板1：可爱女孩
python master_tool.py -n 5 "cute anime girl, kawaii style, pink accents, cheerful expression"

# 模板2：冷酷御姐
python master_tool.py -n 5 "anime woman, cool expression, elegant pose, mature style"

# 模板3：奇幻角色
python master_tool.py -n 5 "fantasy anime character, magical outfit, mystical atmosphere"
```

#### 2. 自动化工作流
```bash
# 创建批量脚本
#!/bin/bash

# 生成系列1
for i in {1..5}; do
    python master_tool.py "anime warrior $i"
done

# 生成系列2
for i in {1..5}; do
    python master_tool.py "anime mage $i"
done
```

### 质量控制

#### 快速筛选
1. 生成10个候选
2. 快速浏览选择3-5个候选
3. 仔细评估选定图片
4. 选择最终版本

#### 质量检查清单
- [ ] 清晰的主体轮廓
- [ ] 适当的背景复杂度
- [ ] 足够的分辨率
- [ ] 适合分层的特征
- [ ] 符合项目风格

### 时间优化

| 任务 | 传统方式 | 使用工具 | 节省 |
|------|---------|---------|------|
| 角色设计 | 2-3小时 | 5分钟 | **95%** |
| PSD分层 | 1-2小时 | 1分钟 | **98%** |
| 变体生成 | 30分钟/个 | 1分钟/个 | **97%** |

---

## 🎯 常见问题解决方案

### 问题：图片不够清晰
**解决方案**：
```bash
python master_tool.py "anime girl, ultra detailed, best quality, high resolution, sharp image"
```

### 问题：分层不准确
**解决方案**：
1. 重新生成更高质量的原图
2. 使用PS预处理清理背景
3. 确保图片有足够分辨率

### 问题：角色风格不一致
**解决方案**：
```bash
# 创建风格规范提示词
python master_tool.py "anime girl, [你的统一风格描述], consistent art style"
```

### 问题：生成时间太长
**解决方案**：
1. 配置付费API（更快）
2. 避开高峰期使用
3. 使用较小图片尺寸

---

## 📚 资源链接

- 📖 [QUICKSTART.md](QUICKSTART.md) - 快速入门
- 📖 [USER_GUIDE.md](USER_GUIDE.md) - 完整教程
- ❓ [FAQ.md](FAQ.md) - 常见问题

---

## 🎉 总结

### 核心要点

1. ✅ 使用清晰、具体的提示词
2. ✅ 生成Live2D友好的图片
3. ✅ 采用高效的批量工作流
4. ✅ 遵循分层最佳实践
5. ✅ 持续优化和改进

### 下一步

- 🎨 开始创建你的第一个角色
- 📚 阅读完整用户教程
- 💡 应用这些最佳实践
- 🚀 大幅提升工作效率！

---

**让Live2D创作更简单、更高效！** 🎨

---

*最后更新：2026-05-22*
*版本：v5.0*
