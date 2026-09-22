# Kotlin 服务边界 与 C/C++ 热点测量方案（决策提案，2026-09-21）

范围：本文只回答「若要把部分框架用 Kotlin 复写、用 C/C++ 做优化，边界在哪里、什么条件下才允许动手」。
**这不是实施方案**：本轮不写 Kotlin、不写 C/C++、不改任何现有源文件。
引用约定：`路径:行`。生产物路径的实测数据来自 `Work/export-duration/export-duration.json`
（由 `tools/measure_export_duration.py` 生成，`Work/` 不入库）。
下文 `docs/reviews/...` 一律指 `docs/reviews/2026-09-20-moc3-export-risk-review.md`；
`pipeline.py` 指 `live2d_builder/pipeline.py`，`moc3_verify.py` 指 `drivers/live2d_runtime/moc3_verify.py`，
`ADR-002` 指 `docs/architecture/adrs/ADR-002-three-stack-python-go-next.md`。

---

## 1. 结论

不建议做 Kotlin 重写，也不建议手写 C/C++ 形变代码：这条链路上的形变只出现在两处 —— 构建期把键形**烘焙成
顶点数组**（纯 Python 逐顶点算术，`live2d_builder/exporter/moc3_pipeline.py:145-153,157-172`）与运行期
**由官方 Cubism Core 执行**（编译产物 `.venv/Lib/site-packages/live2d/v3/_v3cpp.pyd`，440832 字节；每帧
`model.Update()`，`drivers/live2d_runtime/native.py:139`、`drivers/desktop_pet/native_window.py:68-72`），
前者一次 numpy 广播即可覆盖，后者已经是唯一被验收过的那份 C 形变实现。最强实测数字：**50.33 百万像素的
完整导出（含官方内核验收）中位 6.50s、最长 6.58s，而预算是 150s —— 约 23 倍余量**
（`Work/export-duration/export-duration.json` 第 5 档；预算见 `api/config/config.go:204`）。所以性能话题必须先
过第 3 节的阶段级 Gate 才允许立项，而眼下唯一该做的是决定 `runtime_ready` 的 rotation 变形器（P0），
它与语言、算力无关。

---

## 2. Kotlin 服务边界（若仍要做）

### 2.1 前提：导出路径留在 Python，这一条不移

| 职责 | 现状位置 | 说明 |
|---|---|---|
| 10 步构建（网格→UV→骨骼→变形器→参数→表情→物理→烘焙→导出+moc3→验收） | `live2d_builder/pipeline.py:93-246` | 唯一产出 moc3 的路径 |
| moc3 编译 + 官方内核验收 | `live2d_builder/exporter/moc3_pipeline.py:402-492` | 第 9 步导出、第 9b 步编译（`pipeline.py:152,175`） |
| Go 侧调用方式 | `api/services/python_bridge.go:358-407`：内联 Python 里 `Live2DBuilder(...).build(layers)`（:392-393），预算 `GetExportTimeout()`（:406） | 导出=子进程跑 Python，不共享内存 |
| Python 侧 HTTP 面 | `api_server.py:579-584`（`_build_live2d_model`，经 `asyncio.to_thread` 调用 :629-631）、`api_server.py:614-666`（`/api/export/live2d`） | |
| 就绪判定 | `api_server.py:587-611`（`runtime_readiness`）、`api/handlers/handlers.go:740-753`（`moc3Readiness`） | **单一真相源**，见 §4(a) |

导出成本实际落在哪些步骤：第 1 步网格、第 2 步 UV、第 8 步烘焙、第 9/9b 步导出与编译、第 10 步校验
（`pipeline.py:112,116,148,152,175,192`）。**其中没有任何一步是「运行时形变计算」**：顶点与键形是被逐条
写进 moc3 的（`live2d_builder/exporter/moc3_model.py:384-503`），运行期执行形变的是消费端的 Core。
构建期唯一跑逐顶点算术的地方是键形烘焙（`moc3_pipeline.rotation_keyforms` :104-154 与
`warp_deformers` :216-255）—— 它的性质是「Python 算术没向量化」，不是「缺一段原生代码」，见 §3.1 第 8 行
与 §3.2 Gate C。

### 2.2 一个独立 Kotlin 服务可以拥有什么（不碰上面任何东西）

进程边界：独立 OS 进程、独立端口（建议 8090）、**只经 HTTP/WS + 共享目录通信**，沿用 ADR-002 已经写死的
交互约定（`docs/architecture/adrs/ADR-002-three-stack-python-go-next.md:36-50`）。不 import Python 模块、
不做 JNI/ctypes 到 Core、不写 Python 产物目录（`ADR-002:50`：`assets/` `output/` 三栈共享，非 Python 侧只读）。

| 可拥有 | 现有中立接缝 | Kotlin 侧要做什么 |
|---|---|---|
| A. 导出包**只读**巡检/看板：把 `build_meta.json` 的字段摊平成跨批次对照表 | 字段清单 `live2d_builder/pipeline.py:205-221`；就绪字段 `api_server.py:600-611` | 读文件 → 展示。**不得重新判定 `runtime_ready`**，只能转抄 |
| B. 预览/桌宠**进程编排**：起子进程、读带前缀的 JSON 行、超时即杀、回收 | Go 现在就是这么做的：`api/services/desktop_launch.go:30-79`（argv 来自验收报告，指纹校验 :52-56）；行协议 `drivers/live2d_runtime/moc3_verify.py:19-21,120-128` | 语言中立契约，可换实现；仍须调既有 `python -m drivers...` CLI，不得自己加载 Core |
| C. 进度/事件旁路消费者：分步日志与 WS 广播的汇聚与告警 | `api/services/websocket_hub.go:132`（`Broadcast`）、:146（`BroadcastProgress`）；步骤事件源 `live2d_builder/pipeline.py:112-192` + `core/logger.py:135` | 纯增量、只读订阅 |

### 2.3 绝不能拥有（任一都属红线）

1. `runtime_ready` / 验收结论的第二份实现（真相源：`moc3_pipeline.py:483`、`api_server.py:587-611`、`handlers.go:740-753`）。
2. moc3 字节编译器或 `moc3_lint` 规则（`live2d_builder/exporter/moc3_lint.py:57`、`moc3_container.py:84`）。
3. rotation 变形器的格式定论实验：需要的是**受控实验**（扫参数、比单位），不是换语言
   （`docs/reviews/2026-09-20-moc3-export-risk-review.md:162-164,356-358`）。
4. 任何直接链接 Cubism Core 的原生绑定（见 §4(b)）。
5. 像素级验收判据的替代实现（现判据：`moc3_verify.py:131-197` 离屏渲染 + `opaque_pixels`/`pixels_sha256`）。

### 2.4 为什么不建议现在做

1. **架构决策已定且被文档化**：三栈分治（Python 算法 / Go 接入 / Next.js 交互），
   `ADR-002:36`。第四个运行时不新增任何一条判据能力，只新增一跳协议。
2. **会直接击穿仓库自己的架构适应度函数**：`ADR-002:81`「部署入口数 ≤ 4（当前 4：py/sh/bat/Docker）」。
   Dockerfile 已是 3 阶段（`Dockerfile:5,11,19`），Kotlin 服务=第 4 阶段 + 运行镜像内 JRE；CI 现有 6 个 job
   （`.github/workflows/ci.yml:12,64,105,136,158,196`）要加第 7 个。
3. **契约份数**：类型定义已有 3 份、锁文件已有 3 套（`ADR-002:63-64`），全部变成 4。桥接层已经为此咬过
   一次嘴：`handlers.go:711-720` 专门写 `unwrapPythonResult`，因为「Python 结果包在 `result` 键下，直接读
   顶层键永远读不到」。多一跳 IPC 就多一类这种缺陷。
4. **崩溃面**：Core 历史上既会 `0xC0000005` 也会 `0xC0000409`
   （`docs/reviews/2026-09-20-moc3-export-risk-review.md:272-277`），今天全部关在 Python 子进程里
   （`moc3_verify.py:108-117`）。一个能渲染的 Kotlin 服务必须重新开这条路，等于把隔离层拆了。
5. **性能不是当前瓶颈**：五档实测最慢 6.62s，预算 150s（`api/config/config.go:204`），且项目自己写的重评
   阈值是「中位数接近预算 1/3 ≈ 50s」（`docs/reviews/2026-09-20-moc3-export-risk-review.md:195-197`）——
   现在离阈值差一个数量级。
6. **真正卡住产出的东西换语言不解决**：≥2.1 Mpx 的四档实测 `runtime_ready_all` 全为 `false`
   （`Work/export-duration/export-duration.json`），原因是 rotation 变形器未编译
   （`docs/reviews/...:162-164`）。这是格式未知，不是算力不足。
7. **若目的是「Web 预览动得更准」**：那处代码是前端自建的 PixiJS 近似形变
   （`web/lib/live2d-player.ts:58-65`，`PlaneGeometry` 顶点偏移），要改的是 TS 或改用官方 Web 运行时，
   与 Kotlin 无关。

---

## 3. C/C++ 热点测量方案（动手前置条件）

### 3.0 纪律与现状声明

现状：**各阶段耗时占比目前没有任何数据**。已标定的那份测量只测整次 `build()` 的总时长
（`tools/measure_export_duration.py:54-63`），从未做阶段归因 —— 所以下面所有阈值都是**待校准的门槛**，
不是对现状的描述。

新增一个**独立脚本** `tools/measure_export_stages.py`（只新增文件），通过 monkeypatch 包装下列函数计时，
**不修改任何现有源文件**；结论落盘 `Work/export-stages/`。本文档只设计，不执行。

### 3.1 计时点（阶段 → 函数 → 位置）

| # | 阶段 | 函数 | 位置 | 该阶段里必须单独归因的子项 |
|---|---|---|---|---|
| 1 | Step1 网格 | `Live2DBuilder._generate_meshes` | `pipeline.py:252` | `MeshGenerator.generate_combined_mesh`（`mesh/generator.py:153`）内的**纯 Python 内层网格双重循环**（`generator.py:188-191`）、`_sample_contour`（:360）、`_build_delaunay`（:409）拆成 `Delaunay(pts)`（:435，已在 Qhull C++ 里）与**逐三角形质心过滤循环**（`generator.py:442-448`） |
| 2 | Step2 UV | `_layout_uvs` → `UVUnwrapper.pack_meshes` | `pipeline.py:282` / `mesh/uv_unwrapper.py:50` | `_skyline_pack`（:261）、`_find_skyline_position`（:290）；**是否出现「放不下」的整段放弃**（:92-95） |
| 3 | Step3 骨骼 | `_compute_centroids` / `_assign_bone_weights` | `pipeline.py:447 / 305` | 全图 numpy（`pipeline.py:456-461`）与 `compute_bone_weights`（`generator.py:199`） |
| 4 | Step4 变形器 | `_setup_deformers` | `pipeline.py:331` | `bones/deformers.py` 的 `build` |
| 5 | Step5-7 | 参数/表情/物理 | `pipeline.py:343,355,378` | 物理里的 `_layer_alpha_height`（`pipeline.py:360`，每个 hair/skirt 层整图 numpy） |
| 6 | Step8 烘焙 | `_bake_textures` → `TextureAtlas.bake_atlas` | `pipeline.py:408` / `exporter/texture_atlas.py:136` | 逐层 `resize`+`paste` 循环（`texture_atlas.py:183-193`）、每页 `canvas.save`（:198）、**返回页数 = 0 的情况单独标记**（`pipeline.py:410-412`） |
| 7 | Step9 导出 | `Model3Exporter.export` | `exporter/model3_exporter.py:55` | motion3 生成 `motion/motion3.py:143`、`export_model3_json`（:605） |
| 8 | Step9b 编译 | `compile_export_moc3` | `exporter/moc3_pipeline.py:402` | **键形烘焙（构建期唯一的逐顶点算术）**：`rotation_keyforms`（`moc3_pipeline.py:104-154`，对每个键 × 每个顶点调 `_blend`/`_vertex_weight` :166-182；`_peak_influence` :157-163 还是**每个候选参数各扫一遍全顶点**）、`warp_deformers`+`_lattice`（:216-255 / :207）、`build_rig_spec`（:294）；再 `compile_static_rig`（`moc3_model.py:384`，逐网格/逐顶点/逐键形循环 `moc3_model.py:442-458,427-441`）、`lint_document`（`moc3_lint.py:57`）、`Moc3Container.to_bytes`（`moc3_container.py:84`）、**两次内核子进程**：`verify_moc3_consistency`（`moc3_pipeline.py:464` → `moc3_verify.py:25`）、`verify_motion_playback`（`moc3_pipeline.py:474` → `moc3_verify.py:50`） |
| 9 | Step10 校验 | `validate_all` / `check_cubism_compatibility` | `validator/model_validator.py:226 / 336` | |
| 10 | 收尾 | `build_meta.json` / `mesh_guide.json` 写盘 | `pipeline.py:222,229,464` | |

**底噪单测（必做）**：用与生产完全相同的调用形态直接跑
`python -m drivers.live2d_runtime.moc3_consistency <moc3>`、
`python -m drivers.live2d_runtime.moc3_motion_probe <manifest>`（即 `moc3_verify.py:71-72` 拼出的命令行），
量出「解释器启动 + `import live2d.v3` + GL 初始化」的固定开销，与真实验收时间分开报。

### 3.2 阈值：什么结果才允许谈原生

| Gate | 判据 | 未过怎么办 |
|---|---|---|
| **A：值不值得优化**（Amdahl） | 存在单一阶段在 ≥16 Mpx 的两档里都占**总时长 ≥35%**，且该阶段绝对耗时 ≥1.5s；同时总导出中位数已 >15s（预算 150s 的 1/10） | 不动。按现状 6.50s，即便把 35% 的阶段优化到 0 也只省 2.3s，离项目自己设定的重评阈值 50s（`docs/reviews/...:196`）仍差一个量级 |
| **B：是不是原生该上** | 把该阶段拆成「已在原生库」（scipy/Qhull `generator.py:435`、PIL `texture_atlas.py:192,198`、cv2 轮廓、Core `.pyd`）vs「纯 Python 循环」（`generator.py:188-191,442-448`、`moc3_pipeline.py:145-153`、`moc3_model.py:442-458`、`uv_unwrapper.py:261-328`）。纯 Python 部分须 ≥ 该阶段 60% **且** ≥ 总时长 25% | 不动原生 |
| **C：动作阶梯（强制顺序）** | 1) numpy 向量化该循环 → 复测（`generator.py:188-191` 的网格取点可用 `mask[grid_y, grid_x]` 一次布尔索引替代；`generator.py:442-448` 的逐三角形过滤可用向量化质心 + 一次索引；`moc3_pipeline.py:145-153` 的键形烘焙是 `(顶点 × 键)` 的广播）；2) 换成已有原生依赖；3) 合并两次 Core 子进程为一次（若 §3.1 底噪 ≥ 总时长 30%，这是唯一有效动作）；4) 只有 1-3 都做不到目标，才写 C/C++ 扩展，且必须是「纯数据进 / 数据出」、不 `import live2d`、不进验收判定路径的函数 | 记为「不动原生，改 Python」 |

任何新增原生扩展都不得改变产物字节：以 `moc3_verify.py:189-196` 的 `pixels_sha256` / `opaque_pixels`
和现有像素判据（`docs/reviews/...:150-158`）做前后逐像素相同比对，否则属于「未验证却宣称成功」。

### 3.3 如何控制「像素量」这个已经测过的缩放变量

1. 档位沿用已标定过的五档，保证与历史数据可比：`(3,256) (8,512) (16,1024) (26,1024) (12,2048)`
   （`tools/measure_export_duration.py:71`），图层名保持语义命名以走到真实变形器/键形路径（同文件 :29-35）。
2. **不能只用「百万像素」归一**：实测里 27.26 Mpx = 6.62s 而 50.33 Mpx = 6.50s
   （`Work/export-duration/export-duration.json`），说明该代理变量已经失真。每档必须同时记录
   顶点数、三角形数、图集页数与尺寸、键形数、编译出的字节数，并同时按 `ms/Mpx` 与 `ms/千顶点` 两种归一
   排名；只有在**两种归一下都排第一**才算热点。
3. **需要实测确认的一个静态推断**（本文不判定，只列出核查项）：`atlas_size` 默认 2048
   （`pipeline.py:61`，传入 `UVUnwrapper` 时 `padding=2`，`pipeline.py:71`），打包矩形按整幅图层尺寸
   + padding 计算（`uv_unwrapper.py:70-79`），而 `_find_skyline_position` 在 `x_pos + w > S` 时直接 break
   （`uv_unwrapper.py:308`）。若成立，则 2048 方形图层一页放不下 → `pack_meshes` 走到「放弃」分支
   （`uv_unwrapper.py:92-95`）→ UV 为空 → **Step8 烘焙整段跳过**（`pipeline.py:410-412`），那么
   「50 Mpx 6.50s」没跑烘焙，是个**下界**，20× / 23× 余量的说法就偏乐观。核查方法：每档断言
   `bake_atlas` 返回页数 > 0，并在日志里 grep `Packing failed for` / `texture baking skipped`。
   侧证（同一份真实产物，自证自洽）：`output/export_1789573872/layers/` 的 9 个图层全是 512×512，
   同目录烘出了 2 张 2048×2048 图集页（`web_preview_baked_00.png`、`web_preview_baked_01.png`），
   说明常规尺寸不会被跳过；这是**合成大图的档位问题**，不是生产尺寸下的行为。
4. 采样与顺序：每档 5 次，丢弃 1 次预热，报中位数与 IQR（不是极值）；档位轮转交错跑
   （A,B,C,D,E,A,B,…），避免机器升温把「最慢档」固定成「最后跑的档」；同机、固定电源计划，报告里写明
   机型与 CPU；与已有 2 次样本的历史表（`Work/export-duration/export-duration.json`）并列对比。
5. 函数级归因用 `python -m cProfile`，但只在**不改变阶段排名**的前提下使用：脚本要同时报「带/不带
   profiler 的总时长」，若 profiler 开销 > 20% 则只用它做排名、绝对值以 §3.1 的计时为准。
6. **并发维度必须单独测**：Python 侧导出是同进程线程池（`api_server.py:629-631`，受 GIL 串行），
   Go 侧是独立子进程（`python_bridge.go:436`，真并行）。单发与 2/4 并发两条曲线分开报。若并发一测就
   线性劣化，结论是「并发闸 / 任务队列」，不是 C++。

### 3.4 交付形式

测完必须产出：`Work/export-stages/export-stages.json`（逐档逐阶段）、一张「阶段 × 档位」占比表、
Gate A/B/C 的逐条判定，以及 §3.3 第 3 项的核查结论（烘焙是否被跳过）。**Gate 未过就在文档里写「不动原生」**，
不以「反正是 C/C++」为理由预留工作。

---

## 4. 验收红线（新运行时组件一律适用）

**(a) 未验证的能力绝不报成功。**
- 未实现路由返回 501：`api_server.py:143-145`（`not_implemented`），使用处 `api_server.py:669-676`。
- 就绪只认构建期内核验收：`api_server.py:587-611`；`motion_verified=None` 是「不适用」**不等于通过**
  （`moc3_pipeline.py:480-481`，字段语义注释 `api_server.py:607-609`）。
- Go 侧缺字段一律 false 且必须给 `blocker`：`handlers.go:740-753`；未产出模型回 422 不回 200
  （`handlers.go:698-707`）；超时与「构建坏了」分流，超时回 504 + `timeout_budget_seconds`
  （`handlers.go:624-640`，哨兵错误 `python_bridge.go:21,450-451`）。
- 保守默认有回归测试守着：`tests/unit/test_native_runtime.py:44-51`
  （断言 `report()` 的 `runtime_verified` 为 false）。
- ⇒ Kotlin/C++ 组件若拿不到上游判据，必须回 `false` + 可读 blocker（或 501），不得乐观默认、不得改扩展名
  或伪造文件头绕过验收。

**(b) 所有 Cubism Core 调用必须经隔离子进程。**
- 契约定义在 `drivers/live2d_runtime/moc3_verify.py:1-4`（本模块函数**永不抛出**，超时/崩溃一律归类为
  `ok=false` + `blocker`），实现为 `sys.executable -m <module>`：`moc3_verify.py:14-21,71-72,108-117,155-165`。
- `import live2d.v3` 只允许出现在这些被以 `python -m` 方式拉起的模块里：
  `drivers/live2d_runtime/__main__.py:23`、`moc3_consistency.py:20`、`moc3_load.py:24`、
  `moc3_motion_probe.py:119`、`moc3_render_probe.py:32`、`native.py:99`（桌宠窗口进程内使用，
  该进程本身由 `api/services/desktop_launch.go:56` 以 OS 子进程方式启动）。生产依赖侧也有同样约束：
  `requirements.txt:60-61` 写明 live2d-py「仅用于隔离验证，不进入生产数据路径」。
- ⇒ 新组件不得在自己进程内加载 Core（不得 JNI/JNA/dlopen 那个 `.pyd`/`.dll`）；需要渲染/验收就复用
  上述 CLI 与其 `LIVE2D_*=` 前缀 JSON 行协议（`moc3_verify.py:19-21,120-128`）。
  另需注意子进程输出必须按 UTF-8 解码，否则中文 Windows（cp936）会让 `stdout` 变 `None` 把验收链随机打断
  （`moc3_verify.py:108-117`，成因见 `docs/reviews/...:105-109`）。

**(c) 许可边界。**
- 本项目是 **Apache-2.0**：`LICENSE:1-2`。
- 「`psd2live` 是 GPL-3.0」**已核实为真**（2026-09-22 经 GitHub API 直接读取其仓库 `LICENSE`：
  `tsunehimatoi/psd2live` = **GNU GPL v3**，Kotlin/Gradle 工程，与本文描述的 Kotlin/C++ 边界一致）。
  结论不变且更硬 —— 一律按「可以借思想、**不得复制任何代码或字节**」执行：不粘贴片段、不按其数据结构
  逐字段抄写、不把它作为依赖引入（GPL-3.0 的传染性会与本项目 Apache-2.0 冲突）。
  同类需同样对待的还有 `CheshireMew/PuppetLoom`（**AGPL-3.0**，网络传染性更强）。
- 仓库内已有的合规先例可照抄：`py-moc3`（MIT）只作结构转录与交叉校验、编译器本体自研
  （`requirements.txt:57-59`；`docs/reviews/...:219-220`）；官方样例与生成包不入库
  （`docs/reviews/...:27,277`）。
- 新增任何 Kotlin/C++ 第三方依赖（含 JVM 生态库、CMake 依赖）必须单独登记许可，且不得与 Cubism SDK
  的使用条款冲突（Core 二进制只以官方分发形态经 `live2d-py` 使用，不重分发）。

---

## 5. 待用户决定

1. **Kotlin 服务的目的与授权范围**：它是产品能力还是技术栈偏好？若接受，是否同意范围严格限定在
   §2.2 的 A/B/C（只读巡检、进程编排、事件旁路），并明确写死「导出与验收判定永不移植」（§2.1/§2.3）？
   若你的目的其实是「让模型更能动」，那要先回答：rotation 变形器（P0，
   `docs/reviews/...:332,356-358`）换 Kotlin 会多出任何一条实测证据吗？
2. **运维代价是否接受**：部署入口从 4 变 5（会直接让 `ADR-002:81` 的适应度函数变红，需要一份新 ADR 正式
   覆盖 ADR-002）、Docker 多一个阶段并在运行镜像内装 JRE、CI 从 6 job 到 7 job、`install.py/sh/bat` 是否
   也要负责装 Kotlin 运行时？以及是否要求它像 Go 一样单二进制分发（否则 Windows 用户多一个运行时依赖）。
3. **性能目标的定义与顺序**：目标是「单次导出延迟」还是「并发吞吐」？（后者按 §3.3-6 指向并发闸/队列，
   与 C++ 无关。）是否同意固定顺序：先做 rotation 变形器 → 再跑 `tools/measure_export_stages.py`（只新增
   脚本）→ 按 §3.2 的 Gate 判定 → 只有 Gate 通过才立项原生；并同意「Gate 未过即在文档写不动原生」？
