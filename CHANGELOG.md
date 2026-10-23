# 更新日志

本项目遵循「语义化版本 + 诚实降级」原则：每个版本都明确标注**解决了什么致命问题**、**已知限制**、**如何回退**。

---

## [Unreleased] — 2026-10-23 — 代号「Dependency Truth」

> **本版目标**：把 FOSSA 三项告警（Security Analysis / License Compliance / Dependency
> Quality）从「第三方后台报的数」变成**仓库侧可核实、可复现的状态**。所有版本下限的取舍都
> 逐条查过权威源（OSV.dev / PyPI / Go module proxy / crates.io），不做无依据的越级抬升。

### 🔴 修复（会直接坑到用户）

- **Next.js 未授权 RCE（CVE 级，CRITICAL）**：锁定的 `next@16.4.0` 命中 3 条 2026-10-22
  公布的公告 —— `GHSA-673q-mjm6-r5xr`（`use cache` 函数返回 typed array 时的未授权远程代码执行，
  CVSS v4 `VC:H/VI:H/VA:H/SC:H/SI:H`）、`GHSA-2464-fhr4-w9xw`（Image Optimization 未授权 RCE）、
  `GHSA-p3m5-59mp-jv5r`（App Router + Server Actions 拒绝服务）。**全部在 16.4.1 修复**
  （16.4.1 / 16.4.2 经 OSV.dev 复核均为 0 条）。已升到 `next@16.4.1`，锁文件同步更新
  （含 `libc` 字段，保留 glibc/musl 区分，避免 musl 系统误装 glibc 二进制）。
- **Python 依赖下限的 CVE 面**：Pillow 12.3.0 命中 **39 条**（堆溢出 / 越界读 / 解码炸弹 DoS）、
  cryptography 42.0.0 命中 **15 条**、python-multipart 0.0.18 命中 **14 条**、
  rembg 2.0.0 命中 **7 条**、requests 2.32.3 命中 **4 条**、torch 2.0.0 命中 **28 条**、
  transformers 4.30.0 命中 **50 条**。下限全部抬到修复版本（逐条见 `requirements.txt` 注释）。
- **`transformers` 下限声明的是一个代码跑不起来的版本**：清单写 `transformers>=4.30.0`，
  而 `core/segment_engine/sam2_gd.py` 明确按 **transformers 5.17.0** 验证
  （`Sam2Model` / `GroundingDino` 走 `getattr(transformers, cls_name)`，4.x 里根本没有这些类）。
  下限抬到 `>=5.17.0` 后既消掉 50 条 CVE，也让声明与代码实际要求一致。

### 🟡 支撑版本下限（已声明但从未被任何 CI 验证）

- `requirements.txt` 声明的下限此前**在 CI 里零验证** —— 三处 `pip install` 全是不带约束的
  `latest`。现在 CI 按**声明的下限**安装（`"Pillow>=13.0.0"` 这种形式），下限是否仍可满足/
  仍可安装，第一次被 CI 真正证明一次。
- `install.py` 的兜底安装列表仍写着 `Pillow>=10.0.0` / `numpy>=1.24.0` / `urllib3>=2.2.2` 等旧值，
  与清单直接冲突（单独装会拉到带 CVE 的版本）；已对齐。其 `PYTHON_MIN` 原本是 `(3, 9)`，
  与清单实际要求完全脱节（3.9 走到装依赖那步必然失败）。
- **Go 侧 48 条 advisory 清零到 1 条**：`golang.org/x/crypto` / `net` / `sys` / `text` 与
  `quic-go` 升到修复版本。x/net 的三条都是 **HTTP/2 服务端 DoS**（Trailer 内存放大 /
  畸形帧头转发 / 大量流耗 CPU），本服务支持 `LIVE2D_API_HOST=0.0.0.0` 暴露到局域网，值得修。
  为此把 Go 工具链下限从 1.25 抬到 **1.26**（x/crypto 0.56+ / x/net 0.60+ 要求 go>=1.26），
  CI 三个工作流的 `setup-go` 同步升级。
- **Rust 侧**：两个 `Cargo.lock` 全量 `cargo update`（`ort` 2.0.0-rc.14 → rc.15、
  `webpki-root-certs` 1.0.9 → 1.0.10 等）。

### 🟡 支持版本收敛（两条，都是「保不住就别假装」）

- **放弃 Python 3.10**：Python 3.10 已于 **2026-10-01** 结束官方安全支持；而 Pillow 13.0.0 /
  rembg 2.0.86 / scikit-learn 1.9 官方均要求 `>=3.11` —— 保留 3.10 就只能钉在带 CVE 的版本上。
  改动的连带面：README 徽章、`install.py` 的 `PYTHON_MIN`、CI 矩阵（3.10 / 3.11 / 3.12 → 3.11 / 3.12）。
  顺带修掉一个**存量硬伤**：`requirements.txt` 声明的 `numpy>=2.5.4` 要求 py>=3.12，
  `scikit-learn>=1.9.1` 要求 py>=3.11 —— 也就是说**这份清单在 py3.10/3.11 上本来就装不上**，
  README 宣称的「Python 3.10+」早已名不副实。
- **Go 最低版本 1.21 → 1.26**（同上，为修 x/net 的 HTTP/2 DoS）。

### 🟢 许可证与合规（FF-15 首次真正落地）

- **剩余的 65 条 License Compliance issue 不是合规风险，是策略未配置** —— 逐条实算
  721 个依赖后确认：**698 允许 / 22 需审阅（弱 copyleft）/ 0 禁止 / 1 未声明**。
  **没有任何强 copyleft 或非商业条款依赖**，那 65 条只是「不在 FOSSA 默认允许清单里」。
  新增 `scripts/fossa_license_inventory.py`：枚举 PyPI / npm / crates.io / Go 四个生态的
  依赖树，逐个取 SPDX 标识（数据源是 PyPI JSON、锁文件自带许可、crates.io API、
  deps.dev —— 都是权威源，不是扫描器的结论），按 Apache-2.0 分发场景分三档，
  直接产出「策略该允许什么」的清单。
- **`.fossa.yml` 的 `telemetry.scope` 写成了布尔值**：FOSSA CLI 解析配置时直接报
  `parsing ConfigTelemetryScope failed, expected String, but encountered Boolean` ——
  **整个扫描跑不起来**（用官方 CLI 3.20.7 实测复现）。已改为 `scope: "off"`。
- **新增 `.github/workflows/fossa.yml`**：FOSSA 扫描 CI job。**无 `FOSSA_API_KEY` 时自动跳过**
  而非失败 —— 本仓库是公开仓库，外部贡献者的 fork PR 拿不到组织 secret，
  直接跑会把所有外部 PR 标红（那是"没权限"不是"有问题"）。
  同时该 job 无条件跑离线许可清单并上传 artifact，让「策略该允许什么」在仓库侧可复现、可评审。
- **新增 `docs/license-policy.md`**：FOSSA API Key 的创建步骤（后台手动、只显示一次、
  必须选 Push Only 而非 Full API Token —— 后者会把组织读写权限暴露给所有能提 PR 的人）、
  后台三档规则的完整填写清单、以及「仓库侧能改什么 / 不能改什么」的对照表。
- **一个必须知道的 FOSSA 限制**：项目已存在后，改 `.fossa.yml` 的 `policy:` 字段
  **不会生效**（官方明确："changing the `policy:` field ... will *not* change the policy"）。
  切换策略必须在**项目后台** Settings → Issues Policies 里做 —— 这是纯后台操作，
  仓库侧无法替代。
- **FF-15 此前是纸面承诺**：`docs/architecture/index.md` 写着「Release 打包前钩子：
  `scripts/check-license-bundle.sh`（待补）」，而该脚本**从不存在**。现已实现：
  校验产出物内的许可全文是否与仓库根 `LICENSE` **逐字节一致**（根目录若有 `NOTICE` 一并校验），
  不合规即**退出码 1 阻断发布**。
- **产出物真的带上许可了**：此前导出的模型包、安装包、Web 构建包**都不含任何许可声明** ——
  用户把模型包再分发出去时没有许可文本。现在：
  - 新增 `core/license_bundle.py` 作为**唯一真源**，Python 降级服务与 Go 导出桥共用；
  - 导出模型包写入 `LICENSE`（`tests/unit/test_license_bundle.py` 锁住）；
  - `web/package.json` 加 `prebuild` 钩子，从仓库根复制许可进 `web/public/`（不提交副本，避免漂移）；
  - `scripts/build_desktop_bundle.py` 把许可写进 `desktop/bundle/` 根（安装后的 `$RESOURCE` 根）；
  - `deploy/installer/desktop.iss` 的 `[Files]` 安装许可全文；
  - CI 加 FF-15 校验（PR 级查 `web/public`，发布级查 `web/out` + `desktop/bundle` + `api/webui/dist`）。
- **两个 crate 补 SPDX 标识**：`desktop/Cargo.toml` 与 `native/face_tracker/Cargo.toml` 此前
  **没有 license 字段**（`cargo metadata` 里是 `null`），crates.io 与许可扫描都拿不到本 crate
  的许可信息 —— 已补 `license = "Apache-2.0"`。
- **文档残留修正**：`docs/index.html` 的徽章仍写着「Open Source MIT」（ADR-001 自 2026-09-22
  起已是 Rev.4 / Apache-2.0）→ 改为 Apache-2.0。

### 📦 安装包体积：把误打进去的 torch 组移出（约 −500MB）

- **`requirements.desktop.txt` 把 torch 组全量打进了安装包，与项目自己的设计契约矛盾。**
  `core/capabilities.py` 的模块文档明确写着「基础包只带零前提主链路所需的轻依赖；
  SAM2/torch、rembg、mediapipe…等重能力**不进包**，由应用内按需下载」，
  `api/services/capability_manager.go` 的 `capabilityPlans` 也已实现 `sam2_gd`
  组的一键 pip 安装 —— 但清单里同时写着 `torch>=2.14.1`、`torchvision`、
  `transformers`、`segment-anything`、`huggingface-hub`，等于**两条路都走了一遍**。
- **而且装了也用不上**：`core/segment_engine/semantic.py::_resolve_backend` 的 `auto`
  要满足「`HAS_TORCH` **且**找到 SAM 权重」才选 `sam`，而装配脚本只内置 ISNet 权重
  （`models/rembg/isnet-anime.onnx`），桌面端的 `auto` **恒走 `isnet`**。
  实测在无 torch 环境下 `_resolve_backend("auto") == "isnet"`，与有 torch 时行为一致。
- **代价**：torch 的 win_amd64 wheel 约 119MB（PyPI 实测），解压进 site-packages
  约 400–500MB —— 占 1.4GB 安装包的三分之一左右，换不到任何默认功能。
- **处置**：把这 5 个包移出 `requirements.desktop.txt`（清单 29 → 24 条），
  改为应用内「能力」页按需下载。**桌面端默认链路零变化**（本来就是 isnet）。
  同步修正三处互相打架的文档，并把 e2e 验收从「要求 torch 存在」改成
  「断言 torch **不**在包内」（`TORCH_ABSENT_OK`）—— 有它反而说明装配回退了。

### 🔍 Rust 依赖多版本：查清了，结论是不收敛

- `desktop/Cargo.lock` 有 **20 个包存在多版本并存**（`syn` 三个大版本
  1.0.109 / 2.0.119 / 3.0.9、`base64` 三个、`schemars` 三个、`toml_edit` 三个等）。
  用 `cargo tree -d` 反查后确认：**这些几乎全部来自 Tauri 的传递依赖**，
  版本分裂由上游 `Cargo.toml` 的 `^x` 约束决定，仓库侧没有杠杆 ——
  例如 `toml_edit` 的三个版本分别被 `proc-macro-crate` 的 1.3.1 / 2.0.2 / 3.5.0
  锁定，而这三个又分别被 `glib-macros` / `gtk3-macros` / `num_enum_derive` 要求。
- **其中一大半在 Windows 构建里根本不参与编译**：全量锁文件 407 个包，
  指定 `--target x86_64-pc-windows-msvc` 后只剩 **280 个**，
  且 GTK 系（`glib` / `gtk` / `cairo` / `webkit2gtk` / `soup3`）为 **0 处**。
  Linux / macOS / Android 专属依赖共 127 个，占全量的 31%。
- **结论**：不做 `cargo update -p` 强行统一。proc-macro 生态按大版本分裂是常态，
  强行拉平会直接破坏上游编译；真要减少体积，正确方向是升级 Tauri 大版本，
  成本与风险都远高于收益。

### ⚙️ Go 构建精简：裁掉 gin 内置的 msgpack 编解码器

- **所有产出 Go 二进制的构建入口统一加 `-tags nomsgpack`**（CI 两个 workflow +
  `build_desktop.{bat,sh}` + `build_tauri_release.{bat,sh}` + `run_tauri_dev.{bat,sh}`
  + `api/start.sh` + README 的手抄命令）。
- **为什么**：gin 的 `binding` 默认内置 msgpack，会连带拉进 `ugorji/go/codec`、
  `net/rpc`、`encoding/gob`、`encoding/base32`（依赖树 327 → 323 个包）。
  本项目**零 msgpack 使用**，Go 代码与前端都查过。该包编译期内存占用尤其大 ——
  在本仓库的构建环境里编译它时，`go build` 直接被 **OOM kill**（`signal: killed`），
  加上该标签后构建成功（产物 15.4MB，`go tool nm` 里 `ugorji` 符号数为 **0**）。
- **契约测试 `tests/unit/test_go_build_tags.py`（11 条）**锁住两件事：
  ① Go 源码与前端不得出现 msgpack —— 否则源码看起来"支持 msgpack"，而带标签的
  构建会在运行时拒绝，属于最难查的一类不一致；② 每个产出二进制的 `go build`
  必须带该标签 —— 漏一处就会让开发构建与发布构建的编解码能力分叉。
  已做反向验证：临时去掉 `api/start.sh` 的标签，测试立即失败并精确报出行号。

### 🔍 前端依赖审计：没有可删的

- `web/package.json` 的 5 个 `dependencies` + 7 个 `devDependencies` 全部必要。
  静态扫描会把 `react-dom` / `@types/*` / `autoprefixer` / `postcss` /
  `typescript` 误判成"未使用"（它们不经 `import` 使用：类型走 `tsconfig`、
  PostCSS 插件走 `postcss.config.js` 的 `plugins` 字段、`tsc --noEmit` 走 CI 步骤），
  逐个核实后保留。`npm` 侧 219 个包仅 2 个存在多版本（`glob-parent` / `picomatch`，
  均为 dev 传递依赖），无优化空间。

### ⚡ 运行时性能：健康检查从「每次 fork 3 个 Python」改成缓存 + 单次探测

实测报告见 [`docs/performance-report-2026-10-23.md`](docs/performance-report-2026-10-23.md)。三处改动全部有前后对比：

- **`/api/health` 原来每次请求拉起 3 个 Python 子进程**（`python --version` +
  `import PIL` + `import numpy`），单次 1.53 s、**零缓存**。而前端连接指示器与
  桌面壳会按秒级轮询它 —— 32 并发下实测 **200 次请求只成功 9 次，耗时 115 s，RPS=2**。
  改为：**合并成 1 次 exec**（一个内联脚本同时拿版本与缺失依赖）+ **30 s TTL 缓存**。
  结果：`/api/health` 命中缓存 **0.0004 s**，并发 200 次 **200/200 成功、1.53 s、RPS=130**
  （吞吐 **65×**，耗时 **75×**）。缓存可用 `?force=1` 绕过；诊断页永远强制实时。
  合并后仍能逐项报出缺失依赖，且**输出无法解析时退回「退出码 0 即可用」**，
  与合并前判据一致（否则会把健康状态平白判成 503）。
- **分层质量评分从 2.27 s 降到约 1.0 s**（`core/segment_engine/layer_quality.py`）：
  两两配对检查里 `ma.sum()/mb.sum()` 原本**每对都算一次**，35 层就是约 1190 次
  60 万像素的全数组归约 —— 改为每层预计算一次（35 次）。新增
  `_iou_from_counts(inter, sa, sb)`，用恒等式 `|A∪B| = |A|+|B|−|A∩B|` 替代
  `(a|b).sum()`，把「调用方已经算过 inter、却还要再扫两遍全图」的浪费去掉。
  实测 `check_duplicates` 0.834 s → 0.189 s、`check_overlap` 0.705 s → 0.159 s（各 4.4×）。
  **正确性**：旧版 vs 新版对同一输入端到端对比，`steps.layering.quality`
  **逐字段完全一致**（`defect_count 0→0`、`explained_fraction 0.9898→0.9898`）。
- **`generate_occlusion_map` 去掉 O(n²) 次全图扫描**（`core/segment_engine/composer.py`）：
  它是 35 层两两遮拦检测（595 对），原实现对**每一对**都调 `_bbox_overlap`，
  而它内部是 `np.where(mask)` —— 一次 66 万像素的全画布扫描，合计约 **1190 次**。
  包围盒只与掩码本身有关，改为每层预计算一次（35 次），循环里只比 4 个整数。
  实测 `compose` **2.822 s → 1.563 s**（1.8×），`generate_occlusion_map` 跌出热点列表。
  **正确性用最强口径验证**：旧版 vs 新版各跑一次完整管线，对比 **130 个产物的 SHA256** ——
  所有 PNG / moc3 / texture / `exp3.json` / `motion3.json` **逐字节一致**，
  仅 4 个元数据文件不同且差异全是时间戳与输出目录路径。
- **PNG 压缩级别（6→1）经评估后否决**：初版报告估「可省 1.4 s」，但拆开 88 次
  `Image.save` 后发现 **98% 是交付物**（层图 / 纹理 / 优化图），可安全降级的中间预览
  只占 2.3%（0.069 s，降级仅省 0.032 s）。函数名 `_export_preview_layers` 有误导性 ——
  它写的就是用户拿去 PS 编辑的 `layers/*.png`。全局降级会让每张层图体积 +20%，
  代价大于收益，故**不做**，转而优化了上面的 `generate_occlusion_map`。
- **生成管线端到端 11.92 s → 9.61 s**（中位，3 次，累计 −19%）。
- **Go 侧补上 pprof（默认关闭，双重闸门）**：必须 `LIVE2D_PPROF=1` **且**监听回环
  才挂载；设了变量却监听 `0.0.0.0` 只打警告不挂载。此前 `grep pprof api/` 无结果，
  只能测 RSS。现在可拿到堆分解：`HeapAlloc` 仅 2.29 MB，`Sys` 12.7 MB 里
  GC 元数据 1.97 MB —— **坐实 Go 内存基本全是 runtime 固定开销，无优化空间**。

### 🐛 顺手修掉的存量 bug

- **`drivers/audio/capture.py` 的可选依赖保护写窄了**：`except ImportError` 没能兜住
  `sounddevice` 在「装了但系统缺 PortAudio」时抛的 **OSError**（它载入即 `dlopen libportaudio`）。
  这个 OSError 会逃出导入链，把**整条导入链打挂** —— 表现为「只是没装麦克风依赖，
  结果所有测试都收集失败」。已改为 `except (ImportError, OSError)`。
  （`sounddevice` 0.4.6 与 0.5.6 行为相同，是存量问题，与本次版本抬升无关。）

### 📊 治理结果（OSV.dev 全量复扫，口径对齐 FOSSA）

| 生态 | 治理前 | 治理后 |
|---|---|---|
| PyPI | 157 条 | **0 条** |
| Go | 48 条 | 1 条 |
| npm | 5 条 | 1 条 |
| crates.io | 6 条 | 4 条 |
| **合计** | **129 条** | **6 条** |

剩余 6 条**全部没有修复版本**，逐条原因见 `docs/LIMITATIONS.md` §10。

---

## [v0.10.8] — 2026-10-17 — 代号「Honest Desktop」

> **本版目标**：让安装包"装完真的能用"，并把"看起来能用、实际没有"的东西清掉。

### 🔴 修复（会直接坑到用户）

- **安装包不再缺运行时**：Tauri NSIS 改为自带嵌入式 CPython 3.12 + 全量离线依赖（**92 个包**，site-packages 约 **1.4 GB**）+ `app/` 源码树 + `live2d-api.exe` / `face_tracker.exe` / `onnxruntime.dll` / YuNet / `face_landmarker.task`。装配由 `scripts/build_desktop_bundle.py` 完成，下载物 SHA256 锁定，构建期冒烟 9/9 必装模块。
- **「后端未找到」根因修复**：`resources` 从数组 `["bin/*"]`（会落进 `$RESOURCE\bin\`）改为 map `{"bundle/": ""}`（扁平），并扩展 `find_go_binary` 的候选路径。
- **导出页 PSD 100% 失败**：前端改为合并外层 envelope 的 `success`（内层 `data` 本就没有该字段）。
- **角色保存/删除假成功**：去掉 `.catch()` 伪造对象与静默吞错，失败保留原状态并报错。
- **See-through 假成功**：接口从不抠图却返回 `200 + success:true`，改为 **501** + 可读原因。
- **LLM 兜底假回复**：新增 `fallback:true` 标记，并在文案前显示「离线兜底回复」。
- **Python 面捕假运行**：启动后校验 `is_running()`，失败返回 **503**。
- **PSD 降级下载 404**：`psd_path` 不再返回目录，改给 `package_dir` + `fallback_reason`。
- **连接状态「Offline」误报**：连接指示器原先按 `/api/health` 的 `res.ok` 判定，
  而该端点在后端可达、仅缺少**可选** Python 依赖（PIL/numpy）时返回 **503**，
  导致「弱依赖缺失」被误报成整体离线。现改为**收到任何 HTTP 响应即视为后端可达**
  （仅网络不可达才判 `Offline`）；缺依赖详情仍由 `/health` 环境自检页与 `/api/status` 呈现。
  （`web/lib/api-client.ts` 的 `healthCheck()`）

### 🟡 移除（无效控件，改了也不影响结果）

- 生成页：Provider / 风格 / Steps / CFG。
- 导出页：Include physics / expressions / motions、Compression。
- Live2D 页：静态模型结构树、Physics 面板、Wireframe 开关。

### 🟢 如实标注

- `/api/info` 的 `features` 改为按实际探测生成，并新增 `notes`。
- `/api/models`、`/api/expressions` 明确标注「内置示例列表」。
- 仪表盘延迟改为真实测量；GPU 显示「未探测」。

### 📦 安装包变化

- 体积从 ~8 MB 变为 **461.62 MiB（484,048,467 字节）**（自包含运行时，正常）。
- SHA256：`01862574322e45410ecad57587bcdce7413e968387b267fe1fdf8a4d605b71b5`
- **旧 v0.10.7 的 8 MB 包不可用**（缺 Python 运行时 / 源码树 / 模型），该资产已从 Release 撤下并在发布说明中加了警示；请先卸载旧版再装本版。

### 🧪 安装后自检（本版新增）

- `scripts/windows/verify_desktop_install.ps1`：检查 17 项关键文件、随包解释器与核心依赖、`/api/health` 与 `/api/diagnostics`、残留进程，输出 `PASS / PASS with warnings / FAIL`。
- 新增 `/api/diagnostics` 端点，逐项返回运行时状态（解释器路径、依赖可用性、模型、输出目录）。

### ✅ 实机安装验收（2026-10-17）

在 GitHub `windows-latest` 上对**发布资产本身**做了真实安装验收（[run #39046077810](https://github.com/mw2wbyys6t-sudo/live2d-auto-pipeline/actions/runs/39046077810)），结果 **`RESULT: PASS`**：

| 检查 | 实测结果 |
|---|---|
| 产物哈希 | `321,047,178` 字节，sha256 与发布值一致 |
| 静默安装 | 安装器退出码 `0`，落在 `%LOCALAPPDATA%\Live2D Master Agent` |
| 文件完整性 | **17 项全部存在**（含 `live2d-api.exe` / `live2d-master-agent.exe` / `python\` / `app\` / `models\`） |
| 随包解释器 | Python `3.12.10`；7 个核心模块可导入；`mediapipe 0.10.31`；`torch 2.14.1+cpu`；`sitecustomize` 生效 |
| 后端 | `live2d-api.exe` 启动后 `/api/health` 返回 `python_ok=true` |
| 诊断 | 8 项 `check:*` 全 ok（后端 / 解释器 / 脚本目录 / 产物目录可写 / 面捕程序 / YuNet 模型 / ONNX Runtime / 仅回环监听） |
| 退出 | 停止后无 `live2d-api` / `face_tracker` 残留 |

> 期间发现并修复了一个**验收脚本自身的 bug**：脚本按 productName 找 `Live2D Master Agent.exe`，而 Tauri 产物实际是 Cargo 包名 `live2d-master-agent.exe`，导致首轮误报 FAIL。现已兼容两种命名。

### ⚠️ 仍需人工确认（自动化覆盖不到）

- GUI 逐项操作：生成 → 分层 → 导出 → 预览 → 桌宠。
- 摄像头权限与面捕实际驱动效果（已确认程序 / 模型 / 运行库齐备，但未接摄像头跑过）。
- 桌宠窗口的多窗口 / 置顶行为。
- **安装包未签名**，首次运行会触发 SmartScreen 告警。签名管线已就绪（`scripts/windows/sign_artifacts.ps1`），默认 `L2D_SIGN_MODE=off` 不生效。

### ↩️ 回退

- 卸载后重装本版即可；用户产物在 `%APPDATA%\Live2DMasterAgent` 与「文档\Live2DMasterAgent」，卸载不清空。

### 🧾 发布证据（release receipt）

| 项 | 值 |
|---|---|
| 版本 / 标签 | `v0.10.8`（annotated tag `02592f3` → 提交 `69b27d1`） |
| 源码修订 | `69b27d1` — `chore(release): v0.10.8 - Honest Desktop`，发布线 `main`（trunk） |
| 构建运行 | [CI/CD Pipeline #39033114624](https://github.com/mw2wbyys6t-sudo/live2d-auto-pipeline/actions/runs/39033114624)（tag 触发；`windows-installer` 仅 tag 构建） |
| 构建环境 | `windows-latest`；Node 20 / Go 1.25 / Rust stable / Python 3.12.10 |
| 必须通过的检查 | Python Tests 3.10 / 3.11 / 3.12（3.12 实测 **504 passed / 96 skipped**）、Go build + test、Next.js build + TS check、装配冒烟（**必装 9 项全部可导入**） |
| 依赖解析 | `requirements.desktop.txt`（29 个直接依赖）→ win_amd64 / cp312，实装 **92 个包**，site-packages **1.4 GB** |
| 装配产物体积 | python 1.4 GB + app 1.9 MB + models 171.7 MB = **1.76 GB**（未压缩） |
| 钉住且强制校验 | `python-embed` `4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3`<br>`yunet-onnx` `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`<br>`face-landmarker-task` `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff`<br>（来源 `scripts/windows/hashes.json`，不匹配即构建失败） |
| 已实测但未强制 | `onnxruntime-dll` `0c42dc4e278f0968fdf9ebf1b1e54a74c657a9942e70803e453c91993451cf4c`（17,253,176 字节）——`hashes.json` 该项为空，且脚本当前未对该文件调用 `verify_hash`；**回填哈希并补上校验调用**后才转为强制 |
| 产物 | `Live2D.Master.Agent_0.10.8_x64-setup.exe` — **461.62 MiB / 484,048,467 字节** |
| 产物校验 | `sha256:01862574322e45410ecad57587bcdce7413e968387b267fe1fdf8a4d605b71b5` |
| 下载可达性 | Release 直链匿名请求 `HTTP 200`，`content-length: 484048467`（已实测） |
| 实机验收 | 新资产 [run #39605949729](https://github.com/mw2wbyys6t-sudo/live2d-auto-pipeline/actions/runs/39605949729)（提交 `ce20820`）：`RESULT: PASS`——`484,048,467` 字节、sha256 与发布值一致、安装器退出码 `0`、`/api/health` `python_ok=true`、17 项自检通过。上一版资产为 [run #39046077810](https://github.com/mw2wbyys6t-sudo/live2d-auto-pipeline/actions/runs/39046077810)，同样 PASS |
| 资产替换 | 2026-10-21 用 CI run [#39580133583](https://github.com/mw2wbyys6t-sudo/live2d-auto-pipeline/actions/runs/39580133583)（提交 `d7c46e0`）的产物替换了 Release 资产，加入腮红/眼泪网格键形不透明度驱动修复；文件名与下载 URL 不变 |
| 替代关系 | 取代 `v0.10.7`；其 8 MB 坏资产已删除，发布说明顶部加了警示 |
| 回滚目标 | 卸载后重装**同一产物**（v0.10.8）；用户数据在 `%APPDATA%` 与「文档」保留，卸载不清空 |
| 例外 | 未签名（SmartScreen 告警）；无自动更新；无 MSIX / Store 分发 |
| 复核方式 | `(Get-FileHash "<安装包>" -Algorithm SHA256).Hash` 与上方 `sha256` 比对 |

---

## [v0.10.7] — 2026-10-16 — 代号「Dependency Governance」

> **本版目标**：仓库侧依赖治理 + 发布流程修复。抬高 Python/npm 依赖下限以消除已知 CVE 面、降低 FOSSA 安全/质量告警；修复「Release 有 tag 无资产」的发布回归；并清零全栈版本号残留漂移。

### 🔵 修复与维护

#### 依赖治理（安全 / 质量）
- `requirements.txt` 依赖下限抬高（对齐 dependabot 已通过 CI 的目标）：Pillow 10.0.0→12.3.0、numpy 1.24.0→2.5.4、urllib3 2.2.2→2.8.0、aiohttp 3.10.11→3.14.5、edge-tts 6.1.0→7.2.8。
- 全仓确认无 numpy 2.x 已移除 API 依赖（`np.float_`/`np.NaN`/`np.object_` 等零命中）。
- 修正 `requirements.txt` 中关于 live2d-py 的过时说明：PyPI 已提供 cp311-abi3 预编译 wheel，基础包钉 `==0.8.1`（1.0.0 移除 `live2d.v3`，与验收链不兼容）。
- `web/package.json`：lucide-react 1.53.0→1.55.0、@types/node 26.6.4→26.6.5。

#### 发布流程修复
- **问题**：v0.10.6 的 GitHub Release 资产为空（`assets: []`）——`release` job 只发布 `dist/*`，而该 workflow 从不生成 `dist/*`。
- **修复**：新增 tag 触发的 Windows NSIS 构建 job，并把安装包上传到对应 Release。

#### 版本号统一
- 残留漂移清零：`web/components/Sidebar.tsx`（v0.10.5）、`web/pages/index.tsx`（v0.10.5）、`web/lib/api-client.ts`（fallback v0.10.5）、`install.py`（v0.10.0）、`deploy/installer/desktop.iss`（0.10.2）、`requirements.txt` 头部横幅（v0.10.2）全部对齐到 **0.10.7**。

### 已知限制
- FOSSA 的三项检查（Dependency Quality / License Compliance / Security Analysis）为第三方 SaaS 状态，需在 FOSSA 后台按策略/忽略规则收敛；本版仅做仓库侧治理以降低报道数。
- 桌面安装包当前仅含 Go 后端 + 内嵌 Web UI + Rust 面捕二进制；Python 运行时随包分发属 P0 打包计划（见 `docs/project_understanding.md`）。
- README 曾宣称的单文件 `Live2DMasterAgent.exe` / `-portable.zip` 实际并无脚本产出，本版已按真实产物修正说明。

---

## [v0.10.6] — 2026-10-15 — 代号「Desktop First」

> **本版目标**：解决 CI 构建失败并统一全栈版本号。dependabot 提出的 pixi.js v7→v8 大版本升级因 API 不兼容（v8 移除 PlaneGeometry、重构 Geometry Buffer/Texture API）导致 Next.js TypeScript 检查失败，本版关闭该 PR 并在 dependabot 配置中忽略 pixi.js major 升级，确保发布管线畅通。

### 🔵 修复与维护

#### CI 构建失败修复
- **问题**：dependabot PR #18 将 `pixi.js` 从 7.4.3 升级到 8.22.0，`web/lib/live2d-player.ts` 使用的 v7 API（`PlaneGeometry`、`geometry.getBuffer('aVertexPosition')`、`tex.baseTexture.resource` 等）在 v8 中全部变更，TypeScript 检查失败。
- **修复**：关闭 PR #18，在 `.github/dependabot.yml` 中为 `pixi.js` 增加 `version-update:semver-major` 忽略规则，避免反复提出破坏性大版本 PR。pixi.js v8 迁移留待后续专门迭代。

#### 版本号统一
- 此前各端版本号不一致（Python 0.10.3 / Go 0.10.5 / Web 0.10.5 / Desktop 0.10.3），本版全部对齐到 **0.10.6**：
  - `core/version.py`：`0.10.3` → `0.10.6`
  - `api/handlers/handlers.go`：`v0.10.5-go` → `v0.10.6-go`
  - `api_server.py`：`v0.10.3-py` → `v0.10.6-py`
  - `web/package.json` / `web/package-lock.json`：`0.10.5` → `0.10.6`
  - `desktop/Cargo.toml` / `desktop/tauri.conf.json`：`0.10.3` → `0.10.6`

### 已知限制

- pixi.js 仍为 v7，v8 迁移（含 WebGPU 支持）需单独排期
- macOS / Linux 桌面打包脚本仍以 Windows 为主

---

## [v0.10.5] — 2026-10-11 — 代号「Desktop First」

> **本版目标**：全面转向桌面应用为唯一分发形态。移除 Docker 和独立 Web 部署模式，保留 Next.js 前端用于桌面内嵌和开发调试。核心动机：AI 推理需要直接利用本地 GPU/CPU，容器化部署无法高效访问硬件加速。

### 🔴 架构变更（BREAKING）

#### 移除 Docker 部署
- **删除** `Dockerfile`（三阶段构建：Go API + Next.js Web + Python 运行时）
- **删除** `docker-compose.yml`（All-in-one 服务编排，含独立前端服务 :3000）
- **删除** `.dockerignore`
- **删除** `deploy/docker/entrypoint.sh`（含独立启动 Next.js `next start -p 3000` 逻辑）
- **CI 移除** `docker-build` job 及 `release` 的 `needs` 依赖

#### 移除 Web 托管配置
- **删除** `web/public/_headers`（Cloudflare Pages / Netlify 专用安全头；桌面版由 Go 同源服务统一注入安全头）

### 🔵 保留与调整

#### 保留 Next.js 前端源码
- `web/` 目录完整保留——桌面版仍需 Next.js 静态导出（`output: 'export'`）嵌入 Go 二进制
- `next.config.js` 保留双模式：`NEXT_STATIC_EXPORT=1`（桌面构建）vs 默认 standalone + rewrites（开发调试）

#### 文档更新
- `README.md`：桌面应用提为推荐方式，Docker 章节移除，独立 Web 启动降级为「开发模式」
- `docs/DEPLOY.md`：重写为桌面安装指南为主，补充源码构建桌面应用的完整流程
- `docs/QUICKSTART.md`：安装章节改为桌面应用优先
- `docs/ARCHITECTURE.md`：技术栈表中部署方案更新为「桌面应用 (Go embed)」

### 已知限制

- 开发模式仍需手动启动两个终端（Python 后端 + Next.js 开发服务器）
- macOS / Linux 桌面打包脚本尚未完善（当前主要支持 Windows Inno Setup 安装包）

---

## [v0.10.4] — 2026-10-11 — 代号「Hardened Shell」

> **本版目标**：系统性安全加固——堵住 5 类审计发现的安全缺陷，覆盖前端面捕、文件导出、静态产物服务、SSRF 防护和批量导入内存安全。

### 🔴 安全修复（SECURITY）

#### S1. 桌面端面捕恢复 — CSP 放行 MediaPipe + Permissions-Policy 允许 camera（审计 A）
- **问题**：CSP `connect-src` 缺少 MediaPipe CDN 回退源（`cdn.jsdelivr.net` / `storage.googleapis.com`），导致桌面端摄像头面捕在 CDN 回退路径下被浏览器阻断；Permissions-Policy 设为 `camera=()` 直接全局禁用摄像头 API。
- **修复**：
  - [api/main.go](api/main.go) 提取命名安全头中间件 `securityHeadersMiddleware()`，CSP `connect-src` 放行 `cdn.jsdelivr.net` + `storage.googleapis.com`，`media-src` 增加 `blob:`
  - Permissions-Policy 从 `camera=()` 修正为 `camera=(self)`
  - [web/public/_headers](web/public/_headers) 同步更新，覆盖非嵌入式部署场景
- **测试**：[api/p4_csp_permissions_test.go](api/p4_csp_permissions_test.go) 验证 CSP 包含所需源、Permissions-Policy 允许 `self` 摄像头

#### S2. zip 导出不再跟随符号链接 — 修复 symlink 泄露 + fd 累积（审计 C）
- **问题**：`export_zip.go` 使用 `os.Open` 跟随符号链接，攻击者可在打包目录中放置指向敏感文件的 symlink 导致越权读取；`defer f.Close()` 在 `filepath.Walk` 回调中累积文件描述符，大批量打包时可能耗尽 fd。
- **修复**：
  - 增加 `!info.Mode().IsRegular()` 检查，跳过 symlink / 设备 / 管道等非普通文件
  - 将 `defer f.Close()` 替换为 `io.Copy` 后显式关闭，消除 fd 累积
  - 增加 `io.Copy` 字节数与 `info.Size()` 一致性校验
- **测试**：[api/handlers/p4_zip_symlink_test.go](api/handlers/p4_zip_symlink_test.go) 构造指向外部秘密文件的 symlink，验证产物不含 symlink 及泄露内容

#### S3. /output/ 目录列举禁用（审计 D）
- **问题**：`ServeOutput` 直接调用 `http.ServeFile`，当请求路径指向目录时返回 HTML 目录列举，泄露所有生成产物文件名。
- **修复**：[api/handlers/handlers.go](api/handlers/handlers.go) ServeOutput 增加 `info.IsDir()` 检查，目录请求统一返回 404 JSON。
- **测试**：[api/handlers/p4_output_dir_test.go](api/handlers/p4_output_dir_test.go) 验证目录请求返回非 200、普通文件仍正常下载

#### S4. SSRF DNS Rebinding TOCTOU 收口（审计 B）
- **问题**：图片下载链路虽然做了 URL 校验（协议、IP 黑白名单），但校验与实际 HTTP 请求之间存在 TOCTOU（Time-of-Check-Time-of-Use）窗口：攻击者可在校验通过后、请求发起前通过 DNS rebinding 将域名解析切换到内网 IP。
- **修复**：
  - [core/image_gen/base.py](core/image_gen/base.py) 引入 threading-local DNS 钉扎机制
  - `CheckedURL` 校验时缓存 `addr_infos`，`checked_image_download` 下载时通过 `DNSPin` 上下文管理器锁定解析结果，确保下载阶段使用的 IP 与校验阶段完全一致
- **测试**：[tests/unit/test_checked_download.py](tests/unit/test_checked_download.py) 新增 5 个 DNS 钉扎回归用例（21/21 通过）

#### S5. PNG 批量导入内存放大修复 — 两遍扫描（审计 E）
- **问题**：批量导入时一次性对所有上传文件执行 `image.Decode`（完整像素解码），200 张 4K PNG 会同时占用约 10GB 内存。
- **修复**：
  - [api/services/imaging.go](api/services/imaging.go) 提取 `checkImageConfig` 函数，封装 header-only 尺寸校验逻辑
  - [api/services/upload_service.go](api/services/upload_service.go) 改为两遍策略：
    - **Pass 1**：逐文件 `checkImageConfig`（仅读 header）→ 收集最大画布尺寸 + 元数据
    - **Pass 2**：逐文件 `Decode` → `NormalizeToCanvas` → 写入 → 立即释放
  - 峰值内存从「N 张同时解码」降至「1 张解码 + N 个 header」
- **测试**：[api/services/p4_png_twopass_test.go](api/services/p4_png_twopass_test.go) 导入 3 张不同尺寸 PNG，验证全部归一化到最大画布

### 🔵 工程改进

- **安全头集中管理**：Go 后端安全头从匿名中间件提取为命名函数 `securityHeadersMiddleware()`，便于测试和维护
- **TDD 覆盖**：全部 5 项修复均通过红绿流程交付，回归测试覆盖 symlink 泄露、DNS rebinding、目录列举、CSP 头、内存安全等场景

### 验证状态

| 检查项 | 结果 |
|--------|------|
| `go build ./...` | ✅ 通过 |
| `go test -race -count=1 ./...` | ✅ 全包通过 |
| `go vet ./...` | ✅ 无告警 |
| `pytest test_checked_download.py` | ✅ 21/21 |
| `npx tsc --noEmit` | ✅ 仅预存 preview.tsx 噪音 |

### 已知限制

- CSP 仍保留 `'unsafe-inline'` 和 `'unsafe-eval'`——前者用于内联样式，后者用于前端动态加载。后续版本可通过 nonce 收紧。
- DNS 钉扎仅覆盖 `checked_image_download` 路径；若新增其他出站 HTTP 调用，需同样接入 `DNSPin`。

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
