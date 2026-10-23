# FOSSA 许可策略与 API Key 配置

> **读者（Audience）**：维护者与合规负责人。
> **文档类型（Diátaxis）**：explanation + how-to。
> **真相源（Source of Truth）**：依赖许可清单由 [`scripts/fossa_license_inventory.py`](../scripts/fossa_license_inventory.py) 从四个生态的依赖树实算得出；本文件记录**判定依据**与**后台操作步骤**，清单本身请以脚本输出为准。
> **最后校验（Last verified）**：2026-10-23（依赖树 716 条，基线测试 809 passed / 115 skipped）。

---

## 1. 结论先说

剩余的 **64 条 License Compliance issue 不是合规风险，是策略未配置**。

按 Apache-2.0 分发场景（ADR-001 Rev.4：本项目自 2026-09-22 起为 Apache-2.0，产出物对外分发）逐条实算 716 个依赖：

| 档位 | 数量 | 含义 |
|---|---|---|
| 允许（ALLOW） | **694** | 宽松型许可，可自由再分发，不施加文件级传染条件 |
| 需审阅（REVIEW） | **22** | 弱 copyleft（LGPL / MPL），以库形式随附是兼容的，确认一次即可 |
| 禁止（DENY） | **0** | **没有任何强 copyleft 或非商业条款依赖** |
| 许可未声明 | **0** | （`segment-anything` 随 torch 组移出基础依赖树后归零，见 §3.3）|

也就是说：**没有一条依赖的许可证与 Apache-2.0 分发冲突**。那 64 条是 FOSSA 拿着默认策略逐条比对后记下的"未在允许清单内"，而不是"发现了违规"。

复现：

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

## 3. 后台配置策略（这一步才真正消掉 64 条 issue）

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

#### ⛔ Deny（禁止，0 项）

当前依赖树**没有**任何强 copyleft / 非商业条款依赖。这一档留空即可；若希望"未来引入即报警"，可预先加入：

```
AGPL-3.0
GPL-2.0
GPL-3.0
SSPL-1.0
BUSL-1.1
Elastic-2.0
Commons Clause
```

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
| issue 明细逐条读取 | ❌ | — | 需企业版 API |

---

## 6. 相关文件

| 文件 | 作用 |
|---|---|
| `.fossa.yml` | FOSSA 扫描配置（分析范围、项目定位符、策略名） |
| `scripts/fossa_license_inventory.py` | 四生态依赖许可清单 + 策略建议生成器 |
| `scripts/check-license-bundle.sh` | FF-15 许可全文随产物分发的发布闸门 |
| `.github/workflows/fossa.yml` | FOSSA 扫描 CI job（无 key 自动跳过） |
| `docs/LIMITATIONS.md` §9 | FOSSA 三项告警的真实数字与归因 |
