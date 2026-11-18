# FOSSA `AGPL-3.0 Distribution` 策略与 64 条处理清单

> **用途**：FOSSA 升级到可自定义策略的计划后，按本文件一次性完成策略创建、项目绑定与 64 条 licensing issue 复核。
> **当前前提**：2026-11-12 公共 API 实测组织 `access_level = free`，`AGPL-3.0 Distribution` 尚不存在，项目绑定的是内置 `Standard Bundle Distribution`（policyId `271431`）。Free 计划不能创建自定义策略或逐条忽略，所以本文件是**升级后的执行单**，不是当前已生效的配置。
> **配套表格**：[fossa-agpl3-review.csv](fossa-agpl3-review.csv)（64 行，已按 2026-11-14 扫描逐条判定：1 策略项 + 18 忽略 + 45 逐条放行；结构由 `tests/unit/test_fossa_review_table.py` 守护）。

---

## 1. 策略元数据

| 字段 | 值 |
|---|---|
| 策略名 | `AGPL-3.0 Distribution` |
| 类型 | Licensing |
| 描述 | `基于本仓库 AGPL-3.0 + 商业授权双轨的第三方依赖分发策略` |
| 未分类许可默认 | **Flag for Review**（不要默认 Approve） |
| 规则条件 | 无（先按许可族整体定档，特殊依赖再单条例外） |

绑定位置：**FOSSA → Projects → live2d-auto-pipeline → Settings → Issues Policies → `AGPL-3.0 Distribution`**。

> `.fossa.yml` 的 `policy:` 只对新建项目生效；已存在项目必须在后台切换。

---

## 2. 三档规则（按单个 SPDX 标识填写）

FOSSA 的规则匹配按**单个 SPDX 标识**，不是整个表达式。`MIT OR Apache-2.0` 需要把 `MIT`、`Apache-2.0` 分别列入。

### ✅ Approve（允许，不记 issue）

```text
0BSD
AGPL-3.0
AGPL-3.0-only
AGPL-3.0-or-later
Apache-2.0
Apache-2.0 WITH LLVM-exception
BSD
BSD-2-Clause
BSD-3-Clause
BSL-1.0
CC-BY-4.0
CC0-1.0
CDLA-Permissive-2.0
IJG
ISC
MIT
MIT-0
MIT-CMU
Unicode-3.0
Unlicense
Zlib
```

> `AGPL-3.0` 必须放进 Approve：项目自身 `LICENSE` 就是 AGPL-3.0，否则 FOSSA 会判“项目自身违规”。

### ⚠️ Flag for Review（弱 copyleft / 需组合确认）

```text
LGPL
LGPL-2.0
LGPL-2.0-only
LGPL-2.0-or-later
LGPL-2.1
LGPL-2.1-only
LGPL-2.1-or-later
LGPL-3.0
LGPL-3.0-only
LGPL-3.0-or-later
MPL-2.0
GPL-3.0
GPL-3.0-only
GPL-3.0-or-later
```

判定依据：

| 许可 | 典型依赖 | 结论 |
|---|---|---|
| LGPL-2.1 / LGPL-3.0 | `pygame` / `pygame-ce` / `edge-tts` / `libquadmath` | Python `import` 或动态链接，未修改上游 → 审阅后放行 |
| MPL-2.0 | `onnxruntime*` / `next` 的传递依赖 | 文件级 copyleft，未修改上游文件 → 审阅后放行 |
| GPL-3.0 系列 | `scipy` 随附 libgfortran（带 GCC exception） | 项目已是 AGPL-3.0，可经 GPL-3.0 §13 组合；逐条审阅，不批量放行 |

### ⛔ Deny（与 AGPL 不兼容 / 非商业条款）

```text
GPL-2.0
GPL-2.0-only
GPL-2.0-or-later
AGPL-1.0
SSPL-1.0
BUSL-1.1
Elastic-2.0
Commons Clause
CC-BY-NC-3.0
CC-BY-NC-4.0
CC-BY-NC-SA-4.0
CC-BY-SA-1.0
CC-BY-SA-2.0
CC-BY-SA-2.5
CC-BY-SA-3.0
CC-BY-SA-4.0
```

> `GPL-2.0-only` 没有 “or later”，无法升级到 GPL-3.0，走不到与 AGPL 的 §13 互认路径，保持 Deny。
> CC-BY-SA 放进 Deny 是为了保持与现状一致：本仓库出现的 CC-BY-SA 全部是测试文件、旧 pin 或构建期引用，不随产物分发；逐条按 `Component_not_present` 忽略。

---

## 3. 64 条处理清单（汇总，2026-11-14 已全部判定）

> 64 = **6 条 Denied（CC-BY-SA）+ 58 条 Flagged**。逐行可执行表见
> [fossa-agpl3-review.csv](fossa-agpl3-review.csv)：**64 行均有结论，无待判定占位**。
>
> CSV 的 `action` 是本仓库内部处置口径：`IGNORE` = 扫描器误判/无载体/不随产物分发，
> `APPROVE_PER_ITEM` = 载体是许可告知或文档、逐条审阅后放行（后台动作同样是 Ignore）。
> 共：`NOOP_POLICY_SHOULD_APPROVE` 1 + `IGNORE` 18 + `APPROVE_PER_ITEM` 45 = 64。

| # | 组 | 数量 | 依赖 / 载体 | 处置 | Reason |
|---|---|---:|---|---|---|
| A | 项目自身 AGPL | 1 | `live2d-auto-pipeline` | 不操作 | 策略生效后应自动消失；Free 计划下无法 Approve |
| B | CC-BY-SA（Denied） | 6 | numpy 1 + next 1 + `golang.org/x/text` 4 | Ignore | `incorrect_data_found`（SPDX 表）/ `Component_not_present`（构建期、测试文件） |
| C | scipy 1.15.3 | 8 | libquadmath、libgfortran、Kokkos mdspan + 2 条错号 | 6×逐条放行 + 2×Ignore | 弱 copyleft 动态链接；GPL-3 经 §13 与 AGPL 互认；错号无载体 |
| D | pillow 13.0.0 | 2 | 聚合告知文件里的 liblzma GPLv3 提及；BitStream 普通单词 | 1×逐条放行 + 1×Ignore | Pillow 本体 MIT-CMU |
| E | numpy OPL-1.0 | 1 | 无载体 | Ignore | `incorrect_data_found` |
| F | MPL-2.0 | 3 | onnxruntime / onnxruntime-directml / next | 逐条放行 | 文件级 copyleft，未修改上游 |
| G | urllib3 MPL-2.0 | 1 | 已装 2.8.0 无 MPL 全文 | Ignore | `incorrect_data_found` |
| H | edge-tts LGPL-3.0 | 1 | 运行时 import | 逐条放行 | 弱 copyleft、动态链接、未修改 |
| I | x/crypto openssl-ssleay | 1 | v0.58.0 全目录无 SSLeay 全文 | Ignore | `incorrect_data_found` |
| J | pygame 2.5.0 | 4 | 自身 LGPL + docs 内 mpg123 GPL 告知 + 2 条错号 | 2×逐条放行 + 2×Ignore | 不是被 import 的 GPL 实现 |
| K | pygame-ce 2.5.8 | 4 | 同上 | 2×逐条放行 + 2×Ignore | 同上 |
| L1 | opencv-python 4.9.0.80 | 9 | `cv2/LICENSE-3RD-PARTY.txt` 8 条 + instabug | 8×逐条放行 + 1×Ignore | OpenCV 本体 Apache-2.0；告知不是被 import 的代码 |
| L2 | opencv-python-headless 4.9.0.80 | 9 | 同上 | 同上 | 同上 |
| L3 | @img/sharp-* | 14 | 各平台可选依赖（Apache-2.0 AND LGPL-3.0-or-later） | 逐条放行 | 未修改、动态链接；本项目 `images.unoptimized=true` 不走 sharp |

**合计：1 + 6 + 8 + 2 + 1 + 3 + 1 + 1 + 1 + 4 + 4 + 9 + 9 + 14 = 64 条。**

---

## 4. 判定规则与后台执行

表格里的结论由下面三条规则推出，重扫后按同一规则复核即可：

| 载体核对结果 | CSV action | FOSSA 后台动作 | Reason |
|---|---|---|---|
| 依赖里**没有**该许可全文 | `IGNORE` | Ignore | `incorrect_data_found` |
| 只在测试 / 文档 / 许可告知文件里出现 | `APPROVE_PER_ITEM` | Ignore | `other` / `Component_not_present` |
| 弱 copyleft（LGPL / MPL），动态链接或未修改 | `APPROVE_PER_ITEM` | Ignore | `other` |
| 强 copyleft 进入分发产物 | **不忽略** | 替换/移除依赖，或单独评估 | — |

复核命令：

```bash
python scripts/fossa_license_carrier_check.py \
  --expect '<dependency>:<license-id>'
```

> 表格已无待判定行；剩余工作只有在 FOSSA 后台逐条执行 Ignore（Free 计划不可用，
> 见 `docs/license-policy.md` §1）。执行后重扫同一提交，Active 应归零。

---

## 5. 批量执行 API

确认 issue ID 后，按组批量忽略（`scope[id]` 里的 `+` 必须编码为 `%2B`）：

```bash
curl -X PUT \
  'https://app.fossa.com/api/v2/issues?category=licensing&scope[type]=project&scope[id]=git%2Bgithub.com%2Fmw2wbyys6t-sudo%2Flive2d-auto-pipeline&ids[0]=12345&ids[1]=67890' \
  -H "Authorization: Bearer $FOSSA_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "type": "ignore",
    "reason": "incorrect_data_found",
    "notes": "No license carrier in wheel/sdist; scanner ID mismatch"
  }'
```

> `reason` 必须使用 FOSSA 支持的枚举值：`incorrect_data_found`、`Component_not_present`、`other` 等；自由文本放在 `notes`，不要放在 `comment`。

---

## 6. 验收

1. 重新扫描同一提交，确认 `Licensing Active` 为 0，或只剩策略明确 Approve 的条目。
2. 项目自身 `AGPL-3.0-only` 不再出现；若仍出现，回查策略是否绑定成功。
3. 保留的每条例外都能在 FOSSA issue notes 中看到本文件第 3 节的依据。
4. 导出最终 CSV 存档，作为下一次扫描的对照基线。
