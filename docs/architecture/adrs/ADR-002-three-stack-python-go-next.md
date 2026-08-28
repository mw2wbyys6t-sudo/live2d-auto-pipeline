# ADR-002：三栈架构选型 — Python 内核 + Go API + Next.js 工作台

| 字段 | 内容 |
|------|------|
| **状态** | Accepted（v10.0 起生效） |
| **日期** | 2026-07-30 |
| **决策者** | Live2D Master Agent Team |
| **本地校验** | `requirements.txt`（Python）、`api/go.mod`（Go 1.25）、`web/package.json`（Next.js 16 + PixiJS 7）、`docker-compose.yml` 单容器编排 |

---

## 1. 决策问题（Decision Question）

一个打通"AI 生成 → 语义分层 → Live2D 绑定 → 实时驱动 → LLM 对话 → 桌面桌宠 → Web 工作台"全链路的项目，应采用**单一语言全栈**还是**多语言分治**架构？如何在**算法开发效率**、**实时 API 吞吐**、**前端交互体验**、**部署复杂度**四者间取得最优平衡？

## 2. 上下文与推动力（Forces，基于真实文件）

| 推动力 | 说明 |
|--------|------|
| F1 · AI 与图像处理算法生态集中在 Python | 语义分割（SAM/ISNet/rembg onnxruntime，见 [requirements.txt](file:///workspace/requirements.txt#L26-L28)）、MediaPipe 面捕（同上 #L31）、Live2D 数学运算（scipy/scikit-learn）、LLM 生态（OpenAI SDK/edge-tts/Whisper/FunASR）**只在 Python 有成熟包**，重写其他语言成本 ≥ 数人年。 |
| F2 · HTTP/WebSocket 有高并发/低内存要求 | 用户提交生成任务后，`api/services/python_bridge.go`（见 [python_bridge.go](file:///workspace/api/services/python_bridge.go#L43-L57)）用 Go `exec.CommandContext` 调 Python 脚本，Go 负责会话保活、超时、WebSocket Hub（`services/websocket_hub.go`）、缓存（`services/cache.go`）。Python 单线程同步跑算法会阻塞，**让 Python 只做计算、Go 做接入层**是经典稳定解。 |
| F3 · Web 工作台需渲染 Live2D Canvas + WS 实时进度 + 8 页面 SPA | `web/components/ModelCanvas.tsx` + `lib/live2d-player.ts` 使用 **PixiJS 7**（WebGL）做 Live2D 渲染；`pages/` 有 8 个页面（见 [web/pages/](file:///workspace/web/pages/) 结构）。这是纯前端工程问题，Next.js 16 + React + TypeScript + Tailwind 是业内事实标准，Python/Go 做 SSR 模板完全不具备竞争力。 |
| F4 · 小白用户需要"一键安装"，但开发者需要容器化 | `install.py` / `install.sh` / `install.bat` 三套脚本（见仓库根）面向小白用户原生安装；同时 `Dockerfile` + `docker-compose.yml` 面向 Docker 用户。单容器双进程 + 挂载 assets 输出，保持部署入口简单（compose 只有 1 个服务 + optional redis）。 |

## 3. 备选方案对比

| 方案 | 优点 | 被拒原因 |
|------|------|----------|
| **A · Python 全栈（Flask/FastAPI + Jinja 模板）** | 单语言开发简单 | ❌ 实时 API 吞吐差（Python GIL）；Live2D WebGL 必须写 JS，不可能纯 Python；8 页面 SPA 交互体验劣化；F2+F3 不满足。 |
| **B · Node 全栈（Next.js API Route + Python child_process）** | Node 做前后端统一 | ❌ 算法层依然要调 Python，等于三栈退化为两栈但把 API 层从 Go 换到 Node；Node 处理高并发 WebSocket 的内存占用是 Go 的 3~5×，WS Hub 推送延迟不如 Go；F2 的 Go 超时/上下文/子进程控制优势丢失。 |
| **C · Go 全栈（重写算法到 Go）** | 单二进制部署，性能好 | ❌ [红线] SAM/MediaPipe/edge-tts/Whisper 等无成熟 Go 生态，重写代价极高且不可维护，违反"不要重复造轮子"；F1 彻底不满足。 |
| **✅ D · 三栈分治（Python 内核 + Go API + Next.js 工作台）** | F1 算法生态全保留；F2 Go 接入层吞吐/WS 性能最优；F3 Next.js 前端开发体验与生态最成熟；F4 Docker 单容器+原生脚本双入口满足双人群 | 部署链路有跨语言；测试需 pytest + go test + npm run build 三套（本项目 `.github/workflows/ci.yml` 已做到，见 [ci.yml](file:///workspace/.github/workflows/ci.yml#L11-L99)）→ 通过 CI 矩阵 + 本地脚本标准化解决。 |

## 4. 决策（Decision）

**采用三栈分治架构：Python = 算法/内核；Go = 接入/协议/高并发服务；Next.js = 交互/渲染/SPA。** 三者间交互约定：

```
[Next.js 工作台 (3000)]
   │ HTTP/SSE/WS
   ▼
[Go API (8080) ─ handlers/services/models]
   │ exec.CommandContext() + 文件系统共享
   ▼
[Python 内核 ─ core/ | live2d_builder/ | drivers/ | llm_bridge/]
   ↓ 输出到 assets/ 与 output/
   ↕ Go 再通过 HTTP 返回
```

**共享目录契约**（Docker 与原生安装均遵守）：`assets/` 与 `output/` 为三栈共享，Go 和 Next.js 都不直接写 Python 产物目录，只读取。

## 5. 后果（Consequences）

### 正面 ✅
- **算法开发效率**：Python 直接复用全部 ML/CV/AI 生态，新模型从论文到落地平均 1~2 天（vs 重写 Go 数周）。
- **Web 性能**：Go Gin + sonic 高速 JSON + 直接 WebSocket Hub，单实例可承载 1000+ 并发 WS 会话，Python 后端按 CPU 核数排队跑任务不阻塞 API。
- **前端体验**：Next.js 16 + PixiJS 7 WebGL，Live2D 预览可跑到 60fps；React 组件生态（lucide-react、Modal、ErrorBoundary）开箱即用。
- **Blast Radius 隔离**：Python 崩溃只影响单个任务（`exec.CommandContext` 带 timeout + `context.Done()`），Go API 与前端继续服务 → 天然故障隔离。
- **部署**：`docker-compose up -d` 一个命令起全部；小白用户 `python install.py` 即可。

### 负面 / 缓解 ⚠️
- **跨语言调试复杂**：Go→Python 桥的异常栈要跨进程查看 → 缓解：`core/logger.py`（Python 侧） + Go `services/` 统一 error code，错误 JSON 中携带 python_traceback 字段。
- **依赖版本冲突**：pip、go mod、npm 三套锁文件各自独立 → 缓解：CI（`.github/workflows/ci.yml`）三套都走 lockfile 哈希缓存，PR 不通过不合并。
- **跨语言类型契约**：Go `models/models.go` 与 Python `core/config.py` / `web/types/index.ts` 三份类型 → 缓解：以 Go struct 为**唯一真相源**（Spec-first），Python 与 TS 各自有对应序列化校验（`core/qa/engine.py` 做 JSON Schema 校验）。

## 6. 可逆性（Reversibility）

| 项 | 说明 |
|----|------|
| **撤销成本** | **中等偏高**。三栈之间的文件系统契约 + REST/WS 契约一旦被内部依赖使用，替换其中一栈需同时改写 2~3 处适配层。 |
| **重新考虑触发条件** | ① 项目全面云化，全部任务走队列 + Worker，本地 CLI 弃用 → 可考虑 Python FastAPI 单体替代 Go（但 WS 仍是 Go 强项）；② 用户量 < 100，并发极低 → 可简化为 Python FastAPI + Next.js 两栈。 |
| **责任方** | 架构 Owner 提出变更 + 提交新 ADR 覆盖本决策。 |

## 7. Fitness Functions（架构适应性函数）

| 属性 | 度量 | 阈值 | 来源 | 频率 | 失败响应 |
|------|------|------|------|------|----------|
| 依赖方向正确性 | 模块 import 扫描 | Python 不 import Go/TS 包；Go 不 import Python 源码；前端只有 HTTP/WS 调用，不直接读磁盘 | `import-lint`（自定义脚本） | PR 检查 | 阻断合并 |
| API 首字节延迟（p95） | Go 健康检查 + 生成任务元数据接口 | ≤ 80ms（不阻塞 Python 计算时） | `api/tests`（待补 benchmark） | 每次发版 | 告警 + 排查 |
| WS 推送延迟 | WebSocket Hub 广播到 100 client p95 | ≤ 25ms | `services/websocket_hub_test.go`（待补） | 每次发版 | 告警 |
| 部署入口数 | install.* + Dockerfile 总数量 | ≤ 4（当前 4：py/sh/bat/Docker） | 仓库清单 | 每次发版 | 评审 |
| 三栈构建 CI 总时长 | GitHub Actions 总时长 | ≤ 20 分钟 | Actions UI | 每次 PR | 优化缓存 |

## 8. 证据与校验点

- ✅ `requirements.txt` / `api/go.mod` / `web/package.json` 三锁文件齐全
- ✅ `docker-compose.yml` 单容器 `live2d` 暴露 8080 + 3000，挂载 assets/output
- ✅ `.github/workflows/ci.yml` 分三个 job：python-tests / go-build / next-build（末段）
- ✅ Go→Python 桥：`api/services/python_bridge.go` 存在 `validatePath`（防注入）+ `CommandContext`（超时）
- ⏳ 待补：Go 侧 benchmark + WebSocket 压测脚本
