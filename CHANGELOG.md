# 更新日志

本项目遵循「语义化版本 + 诚实降级」原则：每个版本都明确标注**解决了什么致命问题**、**已知限制**、**如何回退**。

---

## [v0.10.3] — 2026-10-08 — 代号「Sandbox-Ready」

> **本版目标**：解决阻碍「下载双击就能在本地沙箱跑通完整生成流程」的最致命问题，为本地开发与首次运行做好万全准备。

### 🔴 致命问题修复（FATAL）

#### F1. 离线兜底生图正式接入主流程
- **问题**：此前「完全离线」根本跑不通——主工作流走的是 ProviderRouter，最终兜底的 Pollinations 需要联网。项目里有一个真正的离线生成器 `local_image_generator.py`，但它只被 Go 旧路径调用，主流程根本不走它。
- **修复**：新增 `LocalPlaceholderProvider`（[core/image_gen/local_placeholder.py](core/image_gen/local_placeholder.py)），注册为路由优先级最末位。所有云端 provider 不可用时（无 Key / 断网），自动产出确定性占位立绘（渐变背景 + 角色剪影 + 提示词水印），并如实标注 `local_placeholder=True`。
- **影响**：「输入文本 → 生成 → 分层 → 导出 → 预览」主链路在**完全离线**条件下也能走通。

#### F2. PSD 缺少 psd-tools 时不再让整个流程前功尽弃
- **问题**：图片已生成、分层已完成，但因为可选依赖 `psd-tools` 没装上，整个 workflow 抛 `RuntimeError`，所有产物白做。
- **修复**：PSD fallback 从「致命错误」降级为「带警告的降级」（[core/workflow.py:533](core/workflow.py#L533)）。缺少 psd-tools 时产出 PNG 包 + 警告，后续 Live2D 导出继续走通。

#### F3. 双击 exe 时项目根定位更健壮
- **问题**：候选目录列表只有容器约定路径（`/workspace /app /repo /project`），在普通 Windows 用户电脑上不存在。
- **修复**：追加 `exe 同级 runtime/ 子目录`作为候选（便携版常见布局）；全部找不到时如实记录警告，不静默继续（[api/config/config.go:178](api/config/config.go#L178)）。

### 🟠 严重问题修复（CRITICAL）

#### C4. /api/health 不再永远返回 200
- **问题**：健康检查永远 OK，前端误判后端可用，点「生成」立刻崩——「先成功后失败」比直接报错更令人困惑。
- **修复**：HealthCheck 启动后跑一次 Python 解释器探针，不可用时返回 503 + 中文说明（[api/handlers/handlers.go:60](api/handlers/handlers.go#L60)）。

#### C5. 端口冲突不再直接 log.Fatalf 崩溃
- **问题**：小白双击两次 exe，第二次撞端口，原实现直接 `log.Fatalf` 退出且无任何可读提示。
- **修复**：绑定前用 `net.Listen` 探测端口，被占时给出中文友好提示（关闭已运行实例 / 换端口）（[api/main.go:143](api/main.go#L143)）。

#### C6. Python 后端默认端口对齐到 8080
- **问题**：Python 后端默认监听 8000，但前端 Next.js 代理目标是 8080。降级模式启动后所有请求 404。
- **修复**：`api_server.py` 默认端口改为 8080，与 Go 后端及前端代理目标一致。

### 🔵 工程改进

- **版本号集中化**：Go 后端新增 `Version` 常量（`v0.10.3-go`），消除散落多处的硬编码版本字符串。
- **未接入配置诚实标注**：SDWebUI / TTS / Redis / MediaPipe 四个仅预留的配置项添加注释，避免用户误以为修改它们会生效。

### 已知限制

- 离线占位图是**确定性剪影**，不是真实 AI 生成结果——它会如实标注 `LOCAL-PLACEHOLDER`。配置 API Key 或联网后可获得真实立绘。
- HSV 兜底分层质量近似 K-means，产出部位可能嘴眼歪斜。建议安装 SAM 模型以获得最佳效果。

---

## [v0.10.2] — 2026-09-29 — 代号「Offline & Desktop Pack」

- 离线兜底生成器 + PSD 图层名识别 + 桌面打包工具链 + 相关项目调研

## [v0.10.1] — 自研 moc3 导出管线

- 官方 Cubism 参数驱动 + 官方内核逐像素验收

## [v0.10.0] — 角色一致性 + 语义分割 + Live2D 导出

- Character consistency system, semantic segmentation, Live2D export.
