# FOSSA `AGPL-3.0 Distribution` 策略与 64 条处理清单

> **用途**：FOSSA 升级到可自定义策略的计划后，按本文件一次性完成策略创建、项目绑定与 64 条 licensing issue 复核。
> **当前前提**：2026-11-12 公共 API 实测组织 `access_level = free`，`AGPL-3.0 Distribution` 尚不存在，项目绑定的是内置 `Standard Bundle Distribution`（policyId `271431`）。Free 计划不能创建自定义策略或逐条忽略，所以本文件是**升级后的执行单**，不是当前已生效的配置。
> **配套表格**：[fossa-agpl3-review.csv](fossa-agpl3-review.csv)（64 行，含 42 行待导出填充）。

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

## 3. 64 条处理清单（汇总）

> 64 = 7 条 Denied（CC-BY-SA）+ 57 条 Flagged。下表已把文档中**具名载体**的 22 条拆开；剩余 42 条需要登录后导出 issue ID 再逐条归类。
> 逐行可执行表见 [fossa-agpl3-review.csv](fossa-agpl3-review.csv)。

| # | 组 | 数量 | 依赖 / 载体 | FOSSA 动作 | Reason | Notes |
|---|---|---:|---|---|---|---|
| A | 项目自身 AGPL | 1 | `live2d-auto-pipeline` | 不操作 | — | 策略生效后应自动消失；若仍 Flag，说明策略没绑定成功 |
| B | `numpy: GPL-1.0` | 1 | numpy | Ignore | `incorrect_data_found` | wheel/sdist 内无 GPL-1.0 全文，扫描器编号错位 |
| C | `scipy: LGPL-3.0-only` | 1 | scipy | Ignore | `incorrect_data_found` | 无载体；GPL-3 尾段与 LGPL 提及被跨段拼接 |
| D | `scipy: LGPL-2.0-or-later` | 1 | scipy | Ignore | `incorrect_data_found` | 无载体；真实是 LGPL-2.1-or-later |
| E | `pillow: Bitstreamream` | 2 | pillow | Ignore | `incorrect_data_found` | Bitstream Vera 只在 sdist 的 `Tests/fonts/`，wheel 不含；另一条是 `BitStream` 普通单词 |
| F | `scipy: LGPL-2.1-later` | 1 | scipy（libquadmath） | Ignore | `other` | 弱 copyleft + 动态链接，未修改上游 |
| G | `scipy: GPL-3.0-with-GCC-exception` | 1 | scipy（libgfortran） | Ignore | `other` | `GPL-3.0-or-later WITH GCC-exception-3.1`；项目为 AGPL，可组合 |
| H | `pillow: GPL-3.0-only` | 1 | pillow | Ignore | `other` | 载体是聚合许可告知文件；GNU FreeFont 不进 wheel |
| I | pygame-ce LGPL/GPL 文档提及 | 4 | pygame-ce | Ignore | `other` | `LGPL-2.1-only` / `GPL-2.0-only` / `GPL-2.0-later` / `LGPL-3.0-only`；均在 `pygame/docs/`，不是被 import 的 GPL-2.0 代码 |
| J | `onnxruntime-directml: MPL-2.0` | 1 | onnxruntime-directml | Ignore | `other` | 文件级 copyleft，未修改上游文件 |
| K | `next: MPL-2.0` | 1 | next | Ignore | `other` | 构建期依赖，不随安装包分发 |
| L | CC-BY-SA（7 条 Denied） | 7 | x/text 测试 4 + numpy 2 + next 1 | Ignore | `Component_not_present` | 测试 URL 字符串 / 旧 pin / next 内 LICENSE 链接；均不构成分发义务 |
| M | 其余 Flagged | 42 | 待导出 | 逐条归类 | 见 §4 | 用载体核对脚本判定后再选 `incorrect_data_found` / `Component_not_present` / `other` |

**合计：22 条已具名 + 42 条待导出 = 64 条。**

---

## 4. 剩余 42 条怎么归类

1. 用 Full token 导出当前项目的 active licensing issues：

```bash
export FOSSA_API_TOKEN='<Full Token>'
curl -sG 'https://app.fossa.com/api/v2/issues' \
  -d 'category=licensing' \
  -d 'scope[type]=project' \
  -d 'scope[id]=git%2Bgithub.com%2Fmw2wbyys6t-sudo%2Flive2d-auto-pipeline' \
  -d 'status=active' \
  -d 'count=100' \
  -H "Authorization: Bearer $FOSSA_API_TOKEN" > /tmp/fossa-issues.json
```

2. 对每条 issue 用载体核对脚本确认它到底属于哪类：

```bash
python scripts/fossa_license_carrier_check.py \
  --expect '<dependency>:<license-id>'
```

3. 按下面规则二选一：

| 载体核对结果 | FOSSA 动作 | Reason |
|---|---|---|
| 依赖里**没有**该许可全文（扫描器误判） | Ignore | `incorrect_data_found` |
| 只在测试 / 文档 / 许可告知文件里出现 | Ignore | `other` 或 `Component_not_present` |
| 弱 copyleft（LGPL / MPL），动态链接或未修改 | Ignore | `other` |
| 强 copyleft（GPL-2.0-only 等）进入分发产物 | **不要 Ignore** | 替换/移除依赖，或改策略前单独评估 |

4. 把结果填进 [fossa-agpl3-review.csv](fossa-agpl3-review.csv) 的 `issue_id` / `dependency` / `license` / `action` / `fossa_reason` / `notes` 列。

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
