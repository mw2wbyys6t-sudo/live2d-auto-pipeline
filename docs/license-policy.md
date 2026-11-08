# FOSSA 许可策略与 API Key 配置

> **读者（Audience）**：维护者与合规负责人。
> **文档类型（Diátaxis）**：explanation + how-to。
> **真相源（Source of Truth）**：包元数据清单由 [`scripts/fossa_license_inventory.py`](../scripts/fossa_license_inventory.py) 实算；FOSSA 另做依赖内嵌许可证深扫，两者口径不同。本文件记录判定依据与后台操作步骤。
> **最后校验（Last verified）**：2026-11-18（FOSSA 摘要 72 issues = 58 Flagged + 7 Denied + 5 Outdated + 2 Vulnerabilities；逐条载体复核见 §3.2.2，载体核对脚本见 `scripts/fossa_license_carrier_check.py`；本项目自身协议自 v0.10.10 起为 AGPL-3.0 + 商业授权双轨，见 ADR-001 Rev.5）。上一轮：2026-10-29（revision scan `126535209`，commit `42a99c7`）。

---

## 0. 本策略的适用范围（先读这段）

- **本策略只管第三方依赖**：某个依赖的许可证是否适合进入本项目的对外分发物。
- **本项目自身不在三档里**：`pygmalion-studio`、`face_tracker` 这两个自有 crate 的许可由
  ADR-001 决定（Rev.5 起 = **AGPL-3.0 + 商业授权双轨**），不按「依赖是否兼容本项目」
  判定。它们在本地清单里单列在「第一方组件」一节。
- **项目协议变更不放宽依赖策略**：强 copyleft 依赖（GPL / AGPL / SSPL / BUSL / Commons Clause）
  仍然进「禁止」档，需要人工确认后再决定是否引入。

---

## 1. 结论先说

当前 GitHub legacy status 为 **65 条 License Compliance issue**（2026-10-29）；同一 FOSSA 扫描的公共摘要分为 **57 条 Flagged + 7 条 Denied**。逐项 issue 详情需要 Maintainer Access，不能把 65 条一概归因成“策略未配置”，更不能全部放行。

2026-10-29 的本地清单：**714 项依赖**中 ALLOW 692 / REVIEW 22 / DENY 0 / 未知 0，另有 **2 项第一方组件**（自有 crate，不适用本策略）。这个结论**只覆盖 PyPI、npm、crates.io、Go 的包元数据**，没有覆盖 FOSSA 在 OpenCV、Pillow 等依赖内部发现的 GPL 等许可证文本，因此不能用来否定当前 7 条 Denied。

仓库侧与后台必须分开处理：

- **仓库侧**：保留强 copyleft / 非商业条款的 Deny 规则；发现实际进入发行产物且不兼容的依赖时，替换或移除，而不是放宽策略。
- **FOSSA 后台**：维护者逐条打开 57+7 项，核对依赖、许可证和发行产物。弱 copyleft 按 §3.2 审阅；Denied 只有在证明对应组件未进入产物或许可证识别有误时，才能按单条 issue 处理。
- **验收**：重新扫描同一提交，确认 License Compliance 为 0；若保留经审阅的例外，必须能看到逐条依据，不能只凭状态变绿。

本地包元数据清单仍可复现，但它不是 FOSSA 深扫的替代品：

```bash
python scripts/fossa_license_inventory.py            # 人类可读报告
python scripts/fossa_license_inventory.py --policy   # 策略建议（三档清单）
python scripts/fossa_license_inventory.py --json     # 机器可读
```

---

## 2. API Key：为什么必须由你手动创建

FOSSA 的 API Key **无法程序化生成**：

- 入口在 FOSSA 后台：**Organization Settings → Integrations → API**；
- Token **只显示一次**（创建后无法再查看），所以也不可能由脚本代取；
- 因此这一步只能由账号持有人手动完成。

### 2.1 创建步骤

1. 登录 <https://app.fossa.com>（组织：`mw2wbyys6t-sudo`）。
2. 右上角头像 → **Settings** → **Organization** → **Integrations** → **API**。
3. 点 **Create API Key**。
4. **勾选 `Push Only`**（见下节的选型说明）。
5. 复制 —— **只显示这一次**，立刻存进 GitHub Secret（步骤 2.3）。

### 2.2 Token 权限怎么选

| 类型 | 能做什么 | 结论 |
|---|---|---|
| **Push Only**（推荐） | 只能上传构建产物、读取自己的项目状态 | ✅ 够用。CI 里只需要它把分析结果推回 FOSSA |
| **Full API Token** | 可读写整个组织的数据（成员、项目、策略） | ❌ 不要放进 CI。PR 作者能触发 workflow，等于把组织权限暴露给所有能提 PR 的人 |

> 本仓库是公开仓库且接受外部 PR，**必须用 Push Only**。

### 2.3 存进 GitHub Secret

```bash
gh secret set FOSSA_API_KEY
# 粘贴 key，回车确认（不回显）
```

仓库 Settings → Secrets and variables → Actions → **Actions secrets** → **New repository secret**：
Name = `FOSSA_API_KEY`，Secret = 刚复制的 key。

> **注意**：本次会话环境里的 `GH_TOKEN` 是 GitHub App 的 user token，**没有 secrets 写权限**（`gh secret list` 返回 `HTTP 403: Resource not accessible by integration`），所以这一步需要你在有仓库管理权限的终端执行。

---

## 3. 后台配置策略与逐项复核

⚠️ **关键**：改 `.fossa.yml` 的 `policy:` 字段**对已存在的项目不生效**。FOSSA 官方明确写着：

> "once a project has been created, changing the `policy:` field in a `.fossa.yml` file for the project will *not* change the policy"

正确做法是在**项目后台**切换：

**FOSSA 后台 → Projects → live2d-auto-pipeline → Settings → Issues Policies → 选择策略**

### 3.1 创建策略

**Policies → Licensing → Create Policy → Type: Licensing**

- **Title**：`AGPL-3.0 Distribution`（与 `.fossa.yml` 里的 `project.policy` 保持一致）
- **Description**：`基于本仓库实算依赖树的 AGPL-3.0 双轨分发许可策略`

> ⚠️ **v0.10.10 起项目自身协议已由 Apache-2.0 改为 AGPL-3.0 + 商业授权双轨**
> （ADR-001 Rev.5）。旧策略名 `Apache-2.0 Distribution` 必须停用：它把
> `AGPL-3.0` 放在 **Deny** 档，而项目自己的 `LICENSE` 现在就是 AGPL-3.0，
> 于是 FOSSA 会判「项目自身违规」—— 这是 License Compliance issue 里
> 最先要消掉的一类误报。

### 3.2 填三档规则

FOSSA 的规则分三档：**Approve**（永不记 issue）、**Flag for Review**（记 issue 待人工批准）、**Deny**（记 issue 要求移除）。

#### ✅ Approve（允许）

```
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

> **为什么 AGPL-3.0 从 Deny 移到了 Approve**：项目自身 `LICENSE` 就是 AGPL-3.0
> （ADR-001 Rev.5）。AGPL 项目吸收 AGPL 代码没有任何兼容问题；继续 Deny 只会
> 让「项目自己」被判违规。这一条不影响对其他强 copyleft 的判断 —— GPL 系列
> 仍然需要人工确认（见下）。

#### ⚠️ Flag for Review（弱 copyleft + 需组合确认的强 copyleft）

```
LGPL
LGPL-2.1
LGPL-2.1-or-later
LGPL-3.0
LGPL-3.0-or-later
MPL-2.0
GPL-3.0
GPL-3.0-only
GPL-3.0-or-later
```

**弱 copyleft（LGPL / MPL）—— 已确认兼容，确认一次即可放行**：

| 许可 | 依赖 | 依据 |
|---|---|---|
| LGPL-3.0-or-later | `@img/sharp-libvips-*`（10 个） | libvips 的运行时，Next.js `sharp` 的可选依赖；**平台专属**（linux/darwin/win32），且 `sharp` 本身是 `next` 的 optional dependency |
| LGPL-3.0-or-later | `@img/sharp-win32-*` / `@img/sharp-wasm32` | 同上（`Apache-2.0 AND LGPL-3.0-or-later`，AND 语义下 LGPL 确实生效） |
| LGPL-3.0 | `edge-tts` | Python `import`（动态链接等价），不修改上游代码 |
| LGPL-2.1 | `pygame` / `pygame-ce` | 同上，`requirements.txt` 里按 Python 版本二选一 |
| MPL-2.0 | `cssparser` / `cssparser-macros` / `dtoa-short` / `option-ext` / `selectors` | 文件级 copyleft，**未修改**上游文件（不修改即无义务） |

**GPL-3.0 系列 —— 因为项目已是 AGPL 才能放行，但仍要逐条确认**：

AGPL-3.0 与 GPL-3.0 之间通过 GPL-3.0 §13 的双向授权条款可以组合，所以
GPL-3.0 依赖不再天然违规；但它会把「整体必须以 GPL-3.0 或 AGPL-3.0 分发」
的义务传导进来，与 AGPL 双轨（商业授权）的条款需要逐条核对。因此放在
**Flag for Review**，人工确认后再批准单条 issue，不批量 Approve。

> FOSSA 支持条件规则（Conditional Policy Rules），可对 MPL 加"仅第三方依赖且未修改"条件，把噪音降到 0。

#### ⛔ Deny（与 AGPL 不兼容 / 非商业条款，保持启用）

```
GPL-2.0
GPL-2.0-only
SSPL-1.0
BUSL-1.1
Elastic-2.0
Commons Clause
CC-BY-NC-3.0
CC-BY-NC-4.0
CC-BY-NC-SA-4.0
```

> `GPL-2.0-only` 与 AGPL-3.0 **不兼容**（GPL-2.0 无 "or later" 时无法升级到
> GPL-3.0，也就走不到 §13 的互认路径），保持 Deny。`GPL-2.0-or-later` 理论上
> 可经 GPL-3.0 中转，但组合链条复杂，按 Deny 处理、需要时单独评估。

#### 3.2.1 那 7 条 Denied 的逐项复核（2026-11-03 完成）

扫描对象 `main@8f1ad93d`（2026-10-29）。后台 Issues 页可逐条读出（登录态下
`…/issues/licensing?page=1&count=50&grouping=issue`），7 条**全部是 CC-BY-SA**，
分布与本地复算结果：

| 条目 | 复算到的载体 | 是否构成分发义务 |
|---|---|---|
| `CC-BY-SA-1.0/2.0/2.5/3.0 in golang.org/x/text v0.43.0`（4 条） | `cases/map_test.go:851/861/869/876` —— 那些 CC-BY-SA **URL 字符串本身是大小写折叠的测试输入**（`grep -rn creativecommons $GOMODCACHE/golang.org/x/text@v0.43.0`） | 否。`_test.go` 不参与编译，不进 Go 二进制 |
| `CC-BY-SA-4.0/3.0 in numpy (2.4.6)`（2 条） | 判的是旧 pin 2.4.6；当前随包已是 **2.5.4**，且 `grep -rl "CC-BY-SA" bundle/python/Lib/site-packages` 只剩 `packaging/licenses/_spdx.py`（SPDX 标识表） | 否。重扫后这两条预计自动消失，属可验证预测而非既成结论 |
| `CC-BY-SA-4.0 in next (16.4.3)`（1 条） | `next/dist/compiled/{glob,lodash.curry,postcss-preset-env}/LICENSE` 里的 CC 链接；`grep -rl "CC-BY-SA" web/node_modules` 命中 **0** | 否。next 只用于构建静态导出，不随安装包分发 |

同批读出的其余两类：

- **2 条 Vulnerabilities 是同一个 CVE**：CVE-2026-112366（picomatch ReDoS，CWE-1333，
  High 8.7，EPSS 0.3%，FOSSA 标记 **No fix**）在 2.3.2 与 4.0.7 两个副本上各记一次。
  锁文件里两者均为 `dev: true`，依赖方是 anymatch / micromatch / readdirp / tinyglobby
  （`next build` 阶段），不进产物。
- **5 条 Outdated** 全是 NPM 传递依赖：chokidar 3.6.0、postcss-nested 6.2.0、
  commander 4.1.1、js-tokens 4.0.0、nanoid 3.3.20 —— 不是我们的直接依赖，随 next/tailwind 升级自然带走。

两条与"我们自己"有关的：

- `AGPL-3.0-only in live2d-auto-pipeline` 只是 **Flagged 而非 Denied**，说明后台策略
  已经按 AGPL 双轨放开（§3.1 担心的旧策略卡 AGPL 没有发生）。
- `Apache License 2.0 directly in code` 的载体是 `scripts/seedream_image_generate.py`
  （火山引擎样例，版权头属上游，不能改成 AGPL）。它全仓库零引用，已 `git mv` 到
  `tools/archive/vendor/`（`cddcacc4`），该目录被 `.fossa.yml` 的 `paths.exclude` 排除。

**处理动作与限制**：逐条忽略要在后台 Issues 页勾选后走 Actions，或用 Ignore Rules；
实测本机账号在 **Free 计划**，`Add Ignore Rule` 直接弹 Business 定价页 —— 自动忽略规则
是付费功能。因此上面这张表就是复核结论的落盘处：重扫后若这些条目复现，照表处理，
不必重新推导。

验收判据（重扫 `main` 当前提交后核对）：Denied 归 0；Vulnerabilities 归 0；
Licensing Active 由 65 降到 **57**（−7 条 Denied，−1 条 Apache-in-code）；
其余 LGPL / MPL 的 Flagged **保留可见**，不为了状态变绿而批量 Approve。

### 3.2.2 2026-11-18 那次扫描（72 issues）的逐条复核

本次扫描摘要：**72 issues = 58 Flagged License + 7 Denied License + 5 Outdated + 2 Vulnerabilities**。
下面按**载体**逐条给结论 —— 判据是「这条 ID 在依赖里到底有没有许可证全文载体」，
而不是扫描器的编号。可复现命令见 [`scripts/fossa_license_carrier_check.py`](../scripts/fossa_license_carrier_check.py)：

```bash
python scripts/fossa_license_carrier_check.py \
  --expect numpy:GPL-1.0 --expect scipy:LGPL-3.0-only \
  --expect scipy:LGPL-2.0-or-later --expect scipy:LGPL-2.1-later \
  --expect scipy:GPL-3.0-with-GCC-exception \
  --expect pillow:GPL-3.0-only --expect pillow:Bitstreamream
```

**Python 侧许可证 ID（58 条 Flagged 的主体）**

| FOSSA 报的 ID | 真实载体 | 结论 |
|---|---|---|
| `numpy: GPL-1.0` | **无载体**：numpy 2.4.6/2.5.3 的 wheel、sdist、已装版里都没有 GPL-1 全文 | **扫描器误判**（编号错位），按「识别有误」ignore |
| `scipy: LGPL-3.0-only` | **无载体** | **扫描器误判**（把 GPL-3 尾段与 LGPL 提及跨段拼接） |
| `scipy: LGPL-2.0-or-later` | **无载体**；真实是 `LGPL-2.1-or-later` | **扫描器误判**（版本号错位） |
| `scipy: LGPL-2.1-later` | `scipy-*.dist-info/LICENSE.txt` —— 捆绑 **libquadmath**（`LGPL-2.1-or-later`） | 真实载体；弱 copyleft + 动态链接，与 AGPL-3.0 兼容 → 按 §3.2 审阅后放行 |
| `scipy: GPL-3.0-with-GCC-exception` | 同上文件 —— 捆绑 **libgfortran**（`GPL-3.0-or-later WITH GCC-exception-3.1`） | 真实载体；项目已是 AGPL-3.0，可经 GPL-3.0 §13 互认 → 逐条审阅后放行 |
| `pillow: GPL-3.0-only` | 载体是**聚合许可告知文件**里的 liblzma「COPYING.GPLv3」提及；真正的 GPLv3 字体（GNU FreeFont）在 sdist 的 `Tests/fonts/`，**不进 wheel** | 真实载体为告知文件而非被 import 的代码 → 审阅后放行 |
| `pillow: Bitstreamream`（2 条） | **无载体**：Bitstream Vera 只在 sdist 的 `Tests/fonts/DejaVuSans/`；wheel 内无 `Tests/`。另有一条命中来自 `PIL/MpegImagePlugin.py` 的 `class BitStream` 与 `*_BITSTREAM_*` 错误串 | **扫描器误判**（另一半是普通单词） |
| `pygame-ce: LGPL-2.1-only` / `GPL-2.0-only` / `GPL-2.0-later` | 都在 wheel 内 `pygame/docs/`（自身 LGPL-2.1 全文 + 捆绑 mpg123 的 GPL-2.0 全文；`GPL-2.0-later` 是 LGPL-2.1 正文第 3 条的交叉引用） | 载体是**文档/许可告知**，不是被 import 的 GPL-2.0 代码 → 按「载体不是 GPL-2.0 实现」逐条审阅 |
| `pygame-ce: LGPL-3.0-only` | **无载体**（上游 README 写明 `LGPL-2.1-or-later`） | **扫描器误判**（classifier 与版本号拼接） |
| `onnxruntime-directml: MPL-2.0`、`next: MPL-2.0` | 传递依赖里的文件级 copyleft | 与 §3.2 既有结论一致：未修改上游文件 → 审阅后放行 |
| `live2d-auto-pipeline: AGPL-3.0-only` | **项目自身** `LICENSE`（ADR-001 Rev.5 起为 AGPL-3.0 + 商业授权双轨） | 不是第三方依赖问题；策略 §3.1 已把 AGPL-3.0 列入 Approve，若后台仍报 Flag，属策略未生效 → 后台核对 |

> 关键事实：numpy 的 `License-Expression` 是 `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`，
> Pillow 是 `MIT-CMU` —— **元数据层面两者都干净**，FOSSA 报的全是包内许可证全文文件。

**Outdated（5 条）—— npm 侧，仓库侧已处理**

| FOSSA 报的 | 实际已装 | 最新 | 处置 |
|---|---|---|---|
| `chokidar` | 3.6.0（dev） | 6.0.0 | override 到 **4.0.3**：4.x 仍是 CJS，tailwind 用的 `atomic/usePolling/awaitWriteFinish/ignoreInitial` 全支持；5.x+ 是 ESM-only，`require()` 直接失败 |
| `postcss-nested` | 6.2.0（dev） | 9.0.1 | override 到 **7.0.2**：仍是 CJS（`engines.node>=18`），与仓库已有 `postcss-selector-parser` override 相容；8.x+ ESM-only |
| `js-tokens` | 4.0.0（**prod**，react→loose-envify） | 10.0.0 | override 到 **5.0.0**：最后一个兼容版（`exports.default` 仍是 global RegExp）；6.0.0+ 没有 `.default`，loose-envify 直接崩 |
| `commander` | **4.1.1**（dev） | 15.1.0 | override 到 **6.2.1**：FOSSA 写的 `14.1.1` **是 registry 里从未发布过的无效版本号**；12.0.0 起 `module.exports` 不再有 `.option()`/`.parse()`，sucrase CLI 会崩，故停在 6.x |
| `nanoid` | 3.3.20（prod，postcss） | 6.0.2 | **不动**：3.3.20 已是 registry 的 `legacy` tag；4.x/5.x 丢掉 CJS `require` 条件，6.0.2 要求 node≥22（仓库未声明 `engines`，会打破 CI） |

> 说明：FOSSA 的「Outdated」是拿**最新版本**比对的，因此上面 4 条即使升到「最后兼容版」，
> 后台仍会显示 outdated —— 要归零只能升到 ESM-only 大版本，而 Tailwind 3.4.19 是 CJS
> `require()` 语义，升级会破坏构建。**这一项以「已升到最后一个兼容版本」为验收口径**，
> 不是以计数归零为口径。

**Vulnerabilities（2 条）—— CVE-2026-112366（picomatch ReDoS）**

- 权威记录真实存在（cvelistV5 + NVD，`CWE-1333`），但 **FOSSA 标的 `High 9.7` 无出处**：
  NVD 给的是 **CVSS4 8.7 / CVSS3.1 7.5**，EPSS 0.00335（0.27 百分位）。
- 受影响范围 `≤4.0.7`，而 registry 的 `latest` 就是 `4.0.7` → **没有可升的修复版本**；
  上游修复 PR 至今未合并，实测把 PR 打上后 `+(*a)` 载荷仍复现 11 秒级回溯。FOSSA 的
  「No fix」属实。
- 本仓库两个实例（2.3.2、4.0.7）均为 **dev-only**：BFS 验证从 `dependencies` 完全不可达、
  不进 `next build` 产物；唯一输入源是 `tailwind.config.js` 里写死的静态 glob，
  **没有用户输入流向 picomatch**。风险 = 构建期本地 DoS，不是线上可利用漏洞。
- 结论：**仓库侧无可修项**，后台按「仅 dev 依赖、不进产物、无用户可控输入」逐条 ignore；
  跟踪上游修复 PR 合并后即随常规依赖升级带走。

**本轮仓库侧实际改动**：仅 `web/package.json` 的 `overrides`（4 条）与对应
`package-lock.json`；`npm audit` 0 漏洞、`npm run build` 通过、
`scripts/check-license-bundle.sh web/public` 通过。**没有改任何 Python 下限**：
scipy 的 Kokkos mdspan（`Apache-2.0 WITH LLVM-exception`）只存在于 scipy ≤1.17，
而 scipy ≥1.18 要求 Python ≥3.12，会牺牲仓库声明的 Python 3.11 支持 —— 保持现状并在此记录取舍。

### 3.3 一个待确认项

`segment-anything` 的 PyPI 元数据缺 `license` 字段（FOSSA 会记成"未声明许可"）。
它此前是依赖树里唯一的"未声明许可"项；**随 torch 组移出基础依赖树后已归零**
（见 `requirements.desktop.txt`）。若用户之后在应用内启用 SAM2 能力组，
它会重新出现在依赖树里 —— 届时上游 GitHub 为 Apache-2.0，可用 FOSSA 的
**依赖级规则**（按 dependency name 匹配 `segment-anything`）单独 Approve。

---

## 4. CI 接线

`.github/workflows/fossa.yml` 已内置 FOSSA 扫描 job：

- **无 `FOSSA_API_KEY` 时自动跳过**，不阻断 PR（fork 场景没有 secret，必须能过）；
- **有 key 时**执行 `fossa analyze` 并在 PR 上回写 commit status；
- `run-tests` 只在 PR 上开启，用 `fossa test` 校验"没有引入新的 issue"。

### 4.1 先在本地确认能跑通

```bash
# 下载 CLI（Linux x64，其他平台见 https://github.com/fossas/fossa-cli/releases）
curl -sSL -o fossa.zip \
  https://github.com/fossas/fossa-cli/releases/download/v3.20.7/fossa_3.20.7_linux_amd64.zip
unzip -q fossa.zip && chmod +x fossa

export FOSSA_API_KEY=<你复制的 key>
./fossa analyze           # 上传分析
./fossa test              # 按策略检查 issue，有 issue 时退出码非 0
```

### 4.2 仓库侧已修的两个坑

1. **`telemetry.scope` 必须是字符串**。原来写的是 `scope: off`（YAML 解析成布尔），FOSSA CLI 会直接报
   `parsing ConfigTelemetryScope failed, expected String, but encountered Boolean` —— **整个扫描跑不起来**。现已改为 `scope: "off"`。
2. **策略匹配按单个 SPDX 标识**，不是按整个表达式。`MIT OR Apache-2.0` 不会命中 `"MIT OR Apache-2.0"` 这个条目，必须把 `MIT`、`Apache-2.0` 分别列入。

---

## 5. 仓库侧能改什么、不能改什么

| 事项 | 仓库侧 | FOSSA 后台 | 备注 |
|---|---|---|---|
| 依赖版本、CVE 治理 | ✅ | — | 已完成：`next` 16.4.0→16.4.1 等 |
| SPDX 元数据补全（Cargo.toml 等） | ✅ | — | 已完成 |
| 许可全文随产物分发（FF-15） | ✅ | — | 已完成：`scripts/check-license-bundle.sh` 闸门 |
| 分析范围收敛（排除归档/文档） | ✅ | — | 已完成：`.fossa.yml` 的 `paths.exclude` |
| 许可策略的三档规则 | ❌ | ✅ | 只能后台建，仓库侧无法声明 |
| 项目绑定哪个策略 | ❌ | ✅ | 官方明确：改 `.fossa.yml` 不生效 |
| issue 明细逐条读取 | ❌ | ✅ | 登录态下 Issues 页可读全量（见 §3.2.1），无需企业 API |
| 逐条 / 自动忽略规则 | ❌ | ✅ **Business 计划** | 实测 Free 计划点 `Add Ignore Rule` 直接弹定价页；自定义策略同样付费 |

---

## 6. 相关文件

| 文件 | 作用 |
|---|---|
| `.fossa.yml` | FOSSA 扫描配置（分析范围、项目定位符、策略名） |
| `scripts/fossa_license_inventory.py` | 四生态依赖许可清单 + 策略建议生成器 |
| `scripts/fossa_license_carrier_check.py` | FOSSA 报的 SPDX ID 在依赖里的**载体核对**（运行时 / 告知文件 / 文档 / 测试 / 无载体=误判） |
| `scripts/check-license-bundle.sh` | FF-15 许可全文随产物分发的发布闸门 |
| `.github/workflows/fossa.yml` | FOSSA 扫描 CI job（无 key 自动跳过） |
| `docs/LIMITATIONS.md` §9 | FOSSA 三项告警的真实数字与归因 |
