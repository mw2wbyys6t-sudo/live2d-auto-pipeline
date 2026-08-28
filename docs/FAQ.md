# ❓ Live2D Master Agent - 常见问题

**FAQ - 解答你的疑惑！**

---

## 📚 目录

1. [基础问题](#基础问题)
2. [安装问题](#安装问题)
3. [使用问题](#使用问题)
4. [生成问题](#生成问题)
5. [技术问题](#技术问题)

---

## 🔰 基础问题

### Q1: Live2D Master Agent是什么？

**A**: 
Live2D Master Agent是一款AI辅助Live2D制作工具。它可以帮助你：
- 从文本描述生成角色立绘
- 自动分层生成PSD文件
- 提供Rigging和参数设计指导
- 大幅提升Live2D制作效率

### Q2: 这个工具需要付费吗？

**A**: 
**完全免费！** 
- 使用 Pollinations.ai 服务，无需任何费用
- 无需注册账号
- 无需API密钥（可选配置）

### Q3: 需要安装什么？

**A**:
- Python 3.8 或更高版本
- 网络连接（用于图像生成）
- 可选：火山引擎API密钥（用于更高质量）

### Q4: 生成一张图需要多长时间？

**A**:
- 免费服务：通常30秒-2分钟
- 付费API：通常10-30秒
- 取决于网络和服务器负载

### Q5: 生成的图片可以商用吗？

**A**:
- 请查看 Pollinations.ai 的使用条款
- 建议商用前咨询法律专业人士
- 付费API可能有不同的使用限制

---

## 📦 安装问题

### Q6: pip安装失败怎么办？

**A**:
```bash
# 升级pip
pip install --upgrade pip

# 使用国内镜像
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 单独安装
pip install Pillow numpy requests psd-tools
```

### Q7: 提示 "python: command not found"

**A**:
1. 确保已安装Python
2. 使用 `python3` 代替 `python`
   ```bash
   python3 master_tool.py "anime girl"
   ```

### Q8: 缺少依赖 "ModuleNotFoundError"

**A**:
```bash
# 重新安装所有依赖
pip install -r requirements.txt

# 或单独安装缺失的包
pip install Pillow
pip install numpy
pip install requests
pip install psd-tools
```

### Q9: Windows系统下无法运行

**A**:
1. 确保Python已添加到PATH
2. 使用命令提示符或PowerShell
3. 或使用Git Bash / WSL

### Q10: Mac/Linux系统权限错误

**A**:
```bash
# 使用pip3
pip3 install -r requirements.txt

# 或使用sudo（不推荐）
sudo pip3 install -r requirements.txt
```

---

## 💻 使用问题

### Q11: 命令行参数怎么使用？

**A**:
```bash
# 查看帮助
python master_tool.py --help

# 常用参数
python master_tool.py "描述"              # 生成图片
python master_tool.py -n 5 "描述"         # 生成5张
python master_tool.py --skip-generate     # 使用已有图片
```

### Q12: 如何生成多个不同的角色？

**A**:
```bash
# 生成5个不同角色
python master_tool.py -n 5 "anime girl"

# 每个都会自动组合不同特征
```

### Q13: 可以使用中文提示词吗？

**A**:
建议使用**英文提示词**，效果会更好。

如果必须使用中文，可以尝试：
```bash
python master_tool.py "可爱的动漫女孩，粉色头发"
```

### Q14: 提示词有什么技巧？

**A**:
**推荐结构**：
```
[角色] + [特征] + [风格] + [质量词]
```

**示例**：
```bash
python master_tool.py "anime girl, long pink hair, blue eyes, school uniform, best quality"
```

### Q15: 如何指定角色的某些特征？

**A**:
直接在提示词中描述：
```bash
python master_tool.py "anime girl, silver hair, red eyes, fox ears, white kimono"
```

---

## 🎨 生成问题

### Q16: 生成的图片模糊怎么办？

**A**:
1. 添加质量词：
   ```bash
   python master_tool.py "anime girl, best quality, ultra detailed"
   ```

2. 配置付费API（更高质量）

3. 使用更高分辨率的原图

### Q17: 图片背景太复杂？

**A**:
1. 在提示词中添加：
   ```bash
   python master_tool.py "anime girl, white background, clean"
   ```

2. 生成后用PS等工具处理背景

3. 使用 `--skip-generate` 配合自己的白底图片

### Q18: 生成的角色"撞衫"？

**A**:
**不可能！**
系统有94个特征组合，每次自动随机选择，确保每个角色都独一无二！

如果想手动控制：
```bash
# 指定特定特征
python master_tool.py "anime girl, red hair, twin tails, maid outfit"
```

### Q19: 分层效果不理想？

**A**:
1. 使用清晰、背景干净的原图
2. 确保图片分辨率足够（建议1024x1024+）
3. 使用 Live2D 友好的提示词：
   ```bash
   python master_tool.py "anime girl, clean lineart, white background, sharp edges"
   ```

### Q20: PSD文件无法打开？

**A**:
1. 确保使用 Live2D Cubism 4.0 或更高版本
2. 检查PSD文件是否完整
3. 尝试重新生成

---

## 🔧 技术问题

### Q21: 如何配置API？

**A**:
```bash
python config_api.py
```

或手动创建 `.env` 文件：
```bash
ARK_API_KEY=your-api-key
```

### Q22: API密钥在哪里获取？

**A**:
1. 访问 https://www.volcengine.com/
2. 注册账号
3. 获取API密钥

### Q23: 网络连接失败？

**A**:
1. 检查网络连接
2. 等待几分钟后重试
3. 使用 `--skip-generate` 模式（使用已有图片）

### Q24: 如何提高生成速度？

**A**:
1. 使用付费API（更快）
2. 选择较小图片尺寸
3. 避开高峰期使用

### Q25: 如何贡献代码/反馈问题？

**A**:
1. 在GitHub提交Issue
2. Fork仓库并提交Pull Request
3. 在社区留言反馈

---

## 💡 技巧与提示

### 获得最佳效果

1. ✅ 使用具体、清晰的描述
2. ✅ 添加质量提升词
3. ✅ 使用白色或简单背景
4. ✅ 指定关键特征（发型、服装等）
5. ✅ 尝试多次生成选择最佳

### 避免常见错误

1. ❌ 不要使用过长、过复杂的描述
2. ❌ 不要同时描述矛盾的特征
3. ❌ 不要使用过于模糊的词汇
4. ❌ 不要使用低分辨率图片进行分层

---

## 📞 更多帮助

- 📖 [QUICKSTART.md](QUICKSTART.md) - 快速入门
- 📖 [USER_GUIDE.md](USER_GUIDE.md) - 完整教程
- 💡 [BEST_PRACTICES.md](BEST_PRACTICES.md) - 最佳实践
- 🐛 [提交Issue](https://github.com/mw2wbyys6t-sudo/Live2D/issues) - 报告问题

---

**还有其他问题？** 欢迎提交Issue！

---

*最后更新：2026-05-22*
