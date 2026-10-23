# 运行时性能报告（2026-10-23）

> **读者（Audience）**：维护者 —— 用于定位性能瓶颈、决定优化优先级。
> **文档类型（Diátaxis）**：reference（测量数据）+ explanation（归因）。
> **测量脚本**：`/tmp/perf_pipeline.py`（一次性诊断脚本，未入仓库）；本文所有数字均为**实测**，不是估算。
> **最后校验（Last verified）**：2026-10-23，commit `cc4103e` 之后。

---

## 0. 摘要

| 项目 | 实测值 |
|---|---|
| 生成管线总耗时（本地素材，median / 3 次） | **11.92 s** |
| 生成管线峰值内存 | **589.6 MB** |
| Go 服务空载 RSS | **11.9 MB** |
| Go 服务 6 个只读端点后 RSS | **14.3 MB** |
| Go 服务 200 次并发健康检查后 RSS | **22.6 MB**（不回落） |
| **`/api/health` 单次耗时** | **1.53 s** ⚠️ |
| **32 并发下的健康检查吞吐** | **RPS = 2**，200 次仅 9 次成功 ⚠️ |

**最严重的问题不是内存，是 `/api/health`**：它每次请求都真的拉起 **3 个 Python 子进程**，且走到 pyenv shim 上单次 ~0.5 s，合计 1.5 s。前端与桌宠会周期性轮询该端点，等于持续烧 CPU。

---

## 0.1 优化实施结果（同日修复后重测）

本文第 4 节的三个问题已在同一天修复并重新测量。**下表全部为同一台机器的前后实测**：

| 指标 | 优化前 | 优化后 | 提升 |
|---|---|---|---|
| `/api/health` 首次（需探测） | 1.53 s | **0.539 s** | 2.8× |
| `/api/health` 缓存命中 | 1.53 s | **0.0004 s** | ~3800× |
| 200 次并发(32) 成功率 | **9 / 200** | **200 / 200** | 22× |
| 200 次并发(32) 总耗时 | 115.33 s | **1.53 s** | **75×** |
| 健康检查吞吐 RPS | **2** | **130** | **65×** |
| 压测后 Go RSS | 22.6 MB | 17.7 MB | −22% |
| `check_duplicates` | 0.834 s | **0.189 s** | 4.4× |
| `check_overlap` | 0.705 s | **0.159 s** | 4.4× |
| `numpy.ufunc.reduce` 调用 | 19,750 次 / 1.875 s | 15,456 次 / **0.787 s** | 2.4× |
| `compose`（图层合成） | 2.822 s | **1.563 s** | 1.8× |
| **生成管线端到端** | **11.92 s** | **9.61 s** | **−2.31 s（−19%）** |

### PNG 压缩级别：评估后**否决**（原估算有误）

初版报告建议「`compress_level` 6→1 可省约 1.4 s」。按调用方拆开 88 次 `Image.save` 后，这个估算**被证伪**：

| 调用方 | 次数 | 耗时 | 性质 |
|---|---|---|---|
| `texture_atlas.bake_atlas` | 9 | 0.713 s | 交付物（运行时纹理） |
| `model3_exporter._export_textures` | 9 | 0.693 s | 交付物（纹理） |
| `model3_exporter._export_preview_layers` | 32 | 0.451 s | **交付物**（就是 `layers/*.png`，用户拿去 PS 编辑的层） |
| `composer.compose` | 29 | 0.406 s | 交付物（层图） |
| `semantic.layer` / `workflow.run` | 2 | 0.606 s | 交付物（优化图 / 层输出） |
| `composer._store_layer` | 6 | 0.077 s | 交付物 |
| **`semantic._write_composite_preview`** | **1** | **0.069 s** | **唯一真正的中间预览** |

**98% 的保存都是交付物**，可安全降级的只有 2.3%（0.069 s，降级后仅省 0.032 s）。
函数名 `_export_preview_layers` 有误导性 —— 它写的是 `layers/*.png`，是交付层。

**结论：不做。** 全局降级会让用户每一张层图体积 +20%（35 层累计可观），只换来 1.4 s；只降那 1 张预览则毫无意义。

### 转而在真正的瓶颈上优化：`generate_occlusion_map`

同一个 profile 显示 `compose` 的 2.82 s 里只有 0.41 s 是保存，**2.16 s 是合成计算**，其中：

- `generate_occlusion_map` **1.103 s**
- `synthesize_face_geometry` 0.932 s

`generate_occlusion_map` 是 O(n²) 两两遮拦检测（35 层 → 595 对），原实现对每一对调用
`_bbox_overlap`，而它内部是 `np.where(mask)` —— **一次全画布（66 万像素）扫描**。
595 对 × 每对 2 次 = **约 1190 次全图扫描**；而包围盒只跟掩码本身有关，算 **35 次**就够。

改为预计算「非空 + 包围盒」后：`compose` **2.822 s → 1.563 s**（1.8×），
`generate_occlusion_map` 已跌出热点列表（< 0.1 s）。

**正确性验证（最强口径）**：旧版 vs 新版各跑一次完整管线，对比 **130 个产物的 SHA256**——
所有 PNG / moc3 / texture / `exp3.json` / `motion3.json` **逐字节一致**；
仅 4 个元数据文件（`build_meta.json` / `layer_guide.json` / `layer_mapping.json` /
`52_LAYER_GUIDE.txt`）不同，而差异**全部是时间戳与输出目录路径**。

### 具体改了什么

1. **`CheckPythonEnvironment` 合并 3 次 exec 为 1 次 + 30 s TTL 缓存**
   （`api/services/python_bridge.go`）。缓存命中时不再 fork 任何进程；
   `?force=1` 可绕过（`/api/health`），诊断页则总是强制实时。
2. **`layer_quality` 的面积预计算 + IoU 用计数直接算**
   （`core/segment_engine/layer_quality.py`）。
   - 两两配对里 `ma.sum()/mb.sum()` 原本每对都算 → 35 层共约 1190 次全数组归约，
     现在每层只算一次（35 次）。
   - 新增 `_iou_from_counts(inter, sa, sb)`，用恒等式
     `|A∪B| = |A| + |B| − |A∩B|` 替代 `(a|b).sum()`；调用方本来就算过 `inter`，
     等于白扫的 2 遍全数组被彻底去掉。
   - **正确性验证**：对同一输入跑「旧版 vs 新版」端到端对比，
     `steps.layering.quality` **逐字段完全一致**
     （`defect_count 0→0`、`explained_fraction 0.9898→0.9898`、`ok True→True`）。
3. **Go 侧挂载 pprof（默认关闭，双重闸门）**（`api/main.go`）。
   必须同时满足 `LIVE2D_PPROF=1` **且**监听回环才挂载；
   设了变量但监听 `0.0.0.0` 时只打警告、不挂载。
   实测三条路径：默认 404 / `0.0.0.0` 404+警告 / 回环 200。
4. **`generate_occlusion_map` 预计算包围盒**（`core/segment_engine/composer.py`）。
   把「两两循环里对每对重算 `np.where`」改成「每层算一次、循环里只比 4 个整数」，
   `compose` 2.822 s → 1.563 s。产物逐字节一致（见上节验证口径）。

### pprof 给出的堆内部分解（此前只能测 RSS）

```
Sys       = 12.7 MB     ← Go 进程从 OS 拿到的总量
HeapAlloc =  2.29 MB    ← 真正活跃的对象
HeapSys   =  7.22 MB
HeapIdle  =  3.87 MB    ← GC 后未归还的页
GCSys     =  1.97 MB    ← GC 元数据
OtherSys  =  1.26 MB
goroutine = 8
```

**结论坐实：Go 侧内存基本全是 runtime 固定开销**（GC 元数据 + 栈 + 未归还页），
业务逻辑只占 2.29 MB。RSS 从 11.9 MB 涨到 22.6 MB 是 GC 保留而非泄漏 ——
优化缓存后压测峰值还降到了 17.7 MB。

---

## 1. 测量环境与局限（必读）

| 项 | 值 |
|---|---|
| CPU | 3 核（容器） |
| Python | 3.14.8 |
| PIL / numpy / scipy / sklearn / cv2 / rembg / psd_tools / onnxruntime | 均可用 |
| **torch** | **缺失** |
| **ISNet 前景模型（`isnet-anime.onnx`）** | **缺失** |
| 输入素材 | `docs/assets/demo_input.png`（600×900，自动放大到 667×1000） |

### 三条必须知道的局限

1. **分层走的是降级路径**。日志明确打印：
   `No foreground segmentation model available. Layering will fall back to HSV color heuristics`
   —— 所以本文的分层耗时是 `semantic_hsv_fallback` 的，**不是** ISNet / SAM2 学习式分割的耗时。
   有 ISNet 模型时分层会变慢（要多跑一次 ONNX 推理），但会产出更细的部位掩码。
2. **没有 torch**，因此 `sam2_gd` 后端无法测量（它会直接抛 `ModelUnavailable`，这是有意的诚实设计）。
3. **3 核容器**，绝对耗时不能直接外推到 8 核台式机；**相对占比**比绝对值更有参考价值。

---

## 2. 生成管线耗时

### 2.1 总量

```
run 1: 12.36 s
run 2: 11.70 s
run 3: 11.92 s
median 11.92 s   min 11.70 s   max 12.36 s
```

跑的是完整链路（含 rigging + moc3 导出 + PSD），产物含 **35 个语义层**、`generated_character.moc3`（202 KB）、8 张 texture、6 张 baked、22 个 `exp3.json` 表情、1 个 `motion3.json`。

### 2.2 阶段分解

数字取自 cProfile 的 **cumtime**，只取每个阶段的**顶层入口函数**（不重复计数）：

| 阶段 | 入口函数 | 耗时 | 占比 |
|---|---|---|---|
| 图层合成与写出 | `workflow.py:346(_step_layering)` | **3.30 s** | 28% |
| 分层质量评分 | `workflow.py:392(_record_layering_step)` | **2.27 s** | 19% |
| 绑定 + moc3 导出 | `live2d_builder/pipeline.py:563(run)` | **3.34 s** | 28% |
| PSD 创建 | `core/psd/creator.py:34(create_psd)` | **1.80 s** | 15% |
| 加载 / 规范化 / 质检 / 去背 | （分散在 `run()` 内联） | ≈ **1.2 s** | 10% |
| 合计 | | ≈ 11.9 s | 100% |

> 说明：`_step_layering` 与 `_record_layering_step` 是顺序调用、不重叠，所以可以直接相加。
> 前四项合计 10.70 s，余下约 1.2 s 是加载、`normalize_image`、`_ensure_min_height`、`assess_image`、`_optimize_image`。

### 2.3 函数级热点（cProfile cumtime 前几名）

| 函数 | 累计耗时 | 调用次数 | 归因 |
|---|---|---|---|
| `{method 'encode' of 'ImagingEncoder'}` | **2.99 s** | 9,206 | PNG 编码（88 次 `Image.save`） |
| `core/segment_engine/composer.py:225(compose)` | **2.57 s** | 1 | 把 35 层按序 alpha 合成为预览图 |
| `core/segment_engine/layer_quality.py:428(score_masks)` | **2.10 s** | 1 | 逐层打分（内部 6,022 次 `ndarray.sum`） |
| `{method 'reduce' of 'numpy.ufunc'}` | 2.14 s | 19,750 | numpy 归约总计 |
| `live2d_builder/exporter/model3_exporter.py:56(export)` | 1.36 s | 1 | model3.json 与纹理导出 |

**结论**：真正吃时间的是 **图像 I/O 与像素级统计**，不是 Python 逻辑。这也意味着优化方向应该是「少做像素运算 / 少写文件」，而不是「重写代码结构」。

---

## 3. Go 服务内存

### 3.1 生命周期

| 时点 | VmRSS | 说明 |
|---|---|---|
| 启动完成、空载 | **11.9 MB** | 8 线程、7 个 fd |
| 6 个只读端点各打一次后 | **14.3 MB** | 含路由/JSON 编解码的首次分配 |
| 200 次并发 `/api/health` 后 | **22.6 MB** | 峰值增加 8.3 MB |
| 之后等待 6 s | **22.6 MB** | **不回落**（Go runtime 保留堆给后续复用，属正常行为，非泄漏） |

`VmSize` 显示 1.86 GB 是 Go 的**虚拟地址空间保留**（runtime 按 arena 预留），**不代表实际占用**；看 RSS 才有意义。

### 3.2 缓存配置的实际影响

启动横幅写着「缓存大小: 100MB」，但实现是：

```go
cache: make(map[string]*CacheEntry),   // services/cache.go:39
```

**懒分配** —— 100 MB 是**上限**，不是预占。空载时缓存不占内存，这点是好的。

### 3.3 内存结论

Go 侧内存**没有可优化空间**：空载 11.9 MB、压测后 22.6 MB，对桌面常驻进程完全可接受。真正的内存大头在 Python 侧（管线峰值 589.6 MB，主要来自 numpy/PIL 的整幅位图与 35 层掩码）。

---

## 4. 发现的问题（按严重度）

### 🔴 P0：`/api/health` 每次请求拉起 3 个 Python 子进程（1.53 s，零缓存）

**实测**：

```
单次 /api/health: 1.600s / 1.616s / 1.531s / 1.525s / 1.530s
/api/status:      1.540s     ← 同样调用了环境探测
/api/info:        0.0007s    ← 不探测，正常
/api/models:      0.0004s    ← 不探测，正常
```

**并发退化**：

```
200 次 /api/health，并发 32  →  仅 9 次成功，耗时 115.33 s，RPS = 2
```

**根因**（`api/services/python_bridge.go:942 CheckPythonEnvironment`）：

```go
exec.CommandContext(versionCtx, pb.cfg.Python.PythonPath, "--version")        // 子进程 1
exec.CommandContext(importCtx,  pb.cfg.Python.PythonPath, "-c", "import PIL")  // 子进程 2
exec.CommandContext(importCtx,  pb.cfg.Python.PythonPath, "-c", "import numpy")// 子进程 3
```

**没有任何缓存**，每次请求跑 3 遍。

**为什么单次要 1.5 s 而不是 0.2 s** —— 我写了个最小 Go 复现程序（`/tmp/execprobe.go`）对比同一个解释器的两种调用方式：

| 解释器写法 | `--version` | `import PIL` | `import numpy` | 合计 |
|---|---|---|---|---|
| `python3`（走 pyenv shim） | 0.478 s | 0.505 s | 0.612 s | **1.594 s** |
| 绝对路径 `/root/.pyenv/versions/3.14.8/bin/python3` | 0.007 s | 0.029 s | 0.142 s | **0.178 s** |

同一个解释器、同样的命令，**差 9 倍**。shim 每被 exec 一次都要重新解析版本并二次 exec 真解释器，在 Go 的 `exec.CommandContext` 路径下这个开销被显著放大（而 Python 的 `subprocess` 下只有 0.14 s —— 说明这不是 shim 本身慢，是它与 Go exec 的组合慢）。

> 补充：桌面包用的是**嵌入式 CPython 的绝对路径**，所以最终用户不会吃到这个 9 倍惩罚。
> 但「每次 health 起 3 个进程」这件事在**任何环境**都存在，只是代价从 1.5 s 降到约 0.18 s。

**影响面**：`web/lib/api-client.ts` 的连接状态指示器、桌面壳的健康轮询都会打这个端点。轮询频率按秒级算，等于持续 fork Python。

### 🟡 P1：分层质量评分自身吃掉 2.27 s（占 19%）

`layer_quality.py:428(score_masks)` 累计 2.10 s，内部 **6,022 次 `ndarray.sum`**。

这是纯计算、无产物副作用，属于「同样的结论可以更便宜地算出来」的类型 —— 典型做法是把逐层多次归约合并成一次（或先用积分图/`cv2.connectedComponentsWithStats` 一次拿到面积与 bbox）。

### 🟡 P1：PNG 编码 2.99 s（88 次 save）

`ImagingEncoder.encode` 累计 2.99 s，25% 的总耗时。实测压缩级别的代价：

| compress_level | 单层耗时 | 文件大小 |
|---|---|---|
| 0 | 14.6 ms | 2607 KB |
| **1** | **14.8 ms** | **120 KB** |
| 6（PIL 默认） | 27.4 ms | 100 KB |
| 9 | 92.4 ms | 98 KB |

`level=1` 比默认 `level=6` **快 46%，体积只大 20%**；`level=9` 完全不该用（慢 3.4 倍，只省 1.6%）。

> ⚠️ 这是**权衡**不是纯收益：层 PNG 本身是交付物（用户要拿去 PS 编辑），体积变大 20% 有代价。
> 若要优化，更合适的是**只对中间预览图用 level=1**，交付层保持 6。

### 🟢 P2：Go 侧完全没有挂载 pprof

`grep -rn pprof api/` 无结果。当前只能测 RSS，**拿不到堆分配来源、goroutine 栈、GC 停顿**。

目前 Go 内存很健康（22.6 MB），所以不是紧急问题；但一旦线上出现内存增长，没有 pprof 就只能靠猜。

---

## 5. 优化建议（按投入产出排序）

| 优先级 | 动作 | 预估收益 | 风险 |
|---|---|---|---|
| **P0** | `CheckPythonEnvironment` 加 **TTL 缓存**（30–60 s），并提供 `?force=1` 绕过 | 轮询时 1.53 s → **≈0** | 低。健康状态延迟最多 60 s，可接受 |
| **P0** | 把 3 次 exec **合并成 1 次**：`python -c "import sys, PIL, numpy; print(sys.version)"` | 首次探测 1.59 s → **≈0.15 s**（实测 `import PIL, numpy` 一起只要 0.126 s） | 低。需调整解析逻辑与错误归因（当前逐个依赖报告缺失项） |
| **P1** | 配置里**优先绝对路径**，避免 `python3` 走 shim | 在 shim 环境下再快 9 倍 | 低。需处理「路径不存在时回退到 PATH 查找」 |
| **P1** | `score_masks` 合并归约 / 改用连通域统计 | 2.10 s → 估计 0.5–1.0 s | 中。改的是质量评分逻辑，**必须用现有阈值回归验证** |
| **P2** | 中间预览图用 `compress_level=1`，交付层保持 6 | 编码 2.99 s → 估计 2.2 s | 中。产物体积是权衡 |
| **P2** | 挂 `net/http/pprof`（建议仅限 `LIVE2D_DEBUG=1` 或本地绑定） | 获得可观测性，无直接性能收益 | 低。注意不要默认暴露在 `0.0.0.0` |

**不建议动的**：Go 服务内存（22.6 MB 已经很健康）、缓存实现（懒分配正确）、管线整体结构（耗时来自像素运算，不是架构）。

---

## 6. 复现方法

```bash
# 生成管线（3 次，报告 median）
python3 /tmp/perf_pipeline.py 3

# Go 服务内存
go build -trimpath -tags nomsgpack -o /tmp/api . && cd api
/tmp/api -port 18099 -host 127.0.0.1 &
PID=$(pgrep -f "api-nomsgpack -port 18099")
grep -E "VmRSS|VmHWM" /proc/$PID/status

# 健康检查耗时
for i in 1 2 3; do curl -sS -o /dev/null -w "%{time_total}s\n" http://127.0.0.1:18099/api/health; done

# shim vs 绝对路径的 exec 开销
go run /tmp/execprobe.go
```

---

## 7. 附：本文未覆盖的部分

- **前端运行时性能**（`web/` 的 LCP/CLS/INP）：需要浏览器与构建产物，本轮未做。
- **Python 侧内存归因**：只测到进程级峰值 589.6 MB，未做逐对象剖析（可用 `tracemalloc` 补）。
- **ISNet / SAM2 真实分层耗时**：缺模型与 torch，未测。这是本文最大的空白 —— 有模型后应重跑 2.1 节。
