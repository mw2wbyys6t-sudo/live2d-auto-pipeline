# FOSSA 许可策略与 API Key 配置

> **读者（Audience）**：维护者与合规负责人。
> **文档类型（Diátaxis）**：explanation + how-to。
> **真相源（Source of Truth）**：包元数据清单由 [`scripts/fossa_license_inventory.py`](../scripts/fossa_license_inventory.py) 实算；FOSSA 另做依赖内嵌许可证深扫，两者口径不同。本文件记录判定依据与后台操作步骤。
> **最后校验（Last verified）**：2026-10-29（FOSSA revision scan `126535209`，commit `42a99c7`；本项目自身协议已于 v0.10.10 起改为 AGPL-3.0 + 商业授权双轨，见 ADR-001 Rev.5）。

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

- **Title**：`Apache-2.0 Distribution`（与 `.fossa.yml` 里的 `project.policy` 保持一致）
- **Description**：`基于本仓库实算依赖树的 Apache-2.0 分发许可策略`

### 3.2 填三档规则

FOSSA 的规则分三档：**Approve**（永不记 issue）、**Flag for Review**（记 issue 待人工批准）、**Deny**（记 issue 要求移除）。

#### ✅ Approve（允许，18 项）

```
0BSD
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

#### ⚠️ Flag for Review（弱 copyleft，5 项）

```
LGPL
LGPL-2.1
LGPL-3.0
LGPL-3.0-or-later
MPL-2.0
```

这些是**已确认兼容**的，确认一次即可放行。逐条依据：

| 许可 | 依赖 | 依据 |
|---|---|---|
| LGPL-3.0-or-later | `@img/sharp-libvips-*`（10 个） | libvips 的运行时，Next.js `sharp` 的可选依赖；**平台专属**（linux/darwin/win32），且 `sharp` 本身是 `next` 的 optional dependency |
| LGPL-3.0-or-later | `@img/sharp-win32-*` / `@img/sharp-wasm32` | 同上（`Apache-2.0 AND LGPL-3.0-or-later`，AND 语义下 LGPL 确实生效） |
| LGPL-3.0 | `edge-tts` | Python `import`（动态链接等价），不修改上游代码 |
| LGPL-2.1 | `pygame` / `pygame-ce` | 同上，`requirements.txt` 里按 Python 版本二选一 |
| MPL-2.0 | `cssparser` / `cssparser-macros` / `dtoa-short` / `option-ext` / `selectors` | 文件级 copyleft，**未修改**上游文件（不修改即无义务） |

> FOSSA 支持条件规则（Conditional Policy Rules），可对 MPL 加"仅第三方依赖且未修改"条件，把噪音降到 0。

#### ⛔ Deny（强 copyleft / 非商业条款，保持启用）

```
AGPL-3.0
GPL-2.0
GPL-2.0-only
GPL-2.0-or-later
GPL-3.0
GPL-3.0-only
GPL-3.0-or-later
SSPL-1.0
BUSL-1.1
Elastic-2.0
Commons Clause
CC-BY-NC-3.0
CC-BY-NC-4.0
CC-BY-NC-SA-4.0
```

当前公共扫描有 **7 条 Denied**，但公共页不给出逐条映射，不能预设它们全是误报。维护者必须逐项核对：

- 若强 copyleft 组件实际进入发行产物且与目标分发方式不兼容，替换或移除对应依赖；不要为了状态变绿而 Approve。
- 若许可证只存在于上游源码包、对应组件未进入本仓库产物，在 issue 中记录产物清单证据后按该条处理。
- 若 FOSSA 把双许可的 `OR` 分支误当成强制条件，记录实际选择的许可证后修正该条识别。
- `JSON` 等含额外使用条件的许可证单独做合规审阅，不混入弱 copyleft 的批量放行。

验收判据：同一提交重扫后 License Compliance 为 0；若组织允许保留已批准例外，每条例外都必须能回读到上述证据。

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
| issue 明细逐条读取 | ❌ | ✅ | 公共页只给汇总；维护者可在后台逐项审阅 |

---

## 6. 相关文件

| 文件 | 作用 |
|---|---|
| `.fossa.yml` | FOSSA 扫描配置（分析范围、项目定位符、策略名） |
| `scripts/fossa_license_inventory.py` | 四生态依赖许可清单 + 策略建议生成器 |
| `scripts/check-license-bundle.sh` | FF-15 许可全文随产物分发的发布闸门 |
| `.github/workflows/fossa.yml` | FOSSA 扫描 CI job（无 key 自动跳过） |
| `docs/LIMITATIONS.md` §9 | FOSSA 三项告警的真实数字与归因 |
