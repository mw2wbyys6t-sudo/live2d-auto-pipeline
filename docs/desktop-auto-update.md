# 桌面版自动更新

目标：用户装过一次之后，只要发布新版本，桌面端下次启动就自动下载安装并重启，
不需要用户手动重装。

## 工作方式

1. 桌面壳启动后后台检查（`desktop/src/lib.rs::check_for_updates`），不阻塞启动页。
2. 检查地址是 GitHub Release 的固定文件：
   `https://github.com/mw2wbyys6t-sudo/live2d-auto-pipeline/releases/latest/download/latest.json`
3. `latest.json` 里的 `version` 高于本地版本时才更新；下载后用
   `desktop/tauri.conf.json` 的 `plugins.updater.pubkey` 校验签名，签名不符直接拒绝。
4. 安装完成后自动重启到新版本。更新失败只写 stderr，不影响主程序。

## 一次性配置（必须做，否则自动更新不生效）

CI 构建时用私钥签名，仓库里只放公钥。私钥已在本机生成：

- 私钥：`C:\Users\Administrator\.tauri\pygmalion-updater.key`（**不要提交、不要外发**）
- 公钥：`C:\Users\Administrator\.tauri\pygmalion-updater.key.pub`（已写入 tauri.conf.json）

到 GitHub 仓库 → Settings → Secrets and variables → Actions 添加：

| Secret | 值 |
| --- | --- |
| `TAURI_SIGNING_PRIVATE_KEY` | 上面私钥文件的**全部内容** |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | 留空（生成时未设密码） |

> 没有这两个 secret 时，打 tag 会在「Prepare updater manifest」步骤失败并明确报错，
> 不会静默产出一个装不上更新的包。

## 发版步骤

1. 同时把两个版本号改到同一个新版本（否则 `latest.json` 与安装包版本不一致）：
   - `desktop/tauri.conf.json` → `version`
   - `desktop/Cargo.toml` → `[package] version`
2. 提交并推送 `main`。
3. 打 tag 并推送：`git tag v0.11.0 && git push origin v0.11.0`。
4. CI（`.github/workflows/tauri-build.yml`）会构建 NSIS、签名、生成 `latest.json`，
   并把 `setup.exe`、`.nsis.zip`、`.sig`、`latest.json` 一起挂到该 Release。
5. 已安装的旧版本下次启动即自动升级。

## 密钥丢失怎么办

私钥丢失后，已发布版本会拒绝新包（签名校验失败）。只能换一对新密钥：重新生成、
更新 `plugins.updater.pubkey`、更新 secret，然后**发一个不依赖自动更新的大版本**
（让用户手动装一次），从下一版起自动更新恢复。

## 与其它 CI 的关系

- 手工触发 `workflow_dispatch` 或推 `main` 也会构建安装包，但**不生成** `latest.json`
  （只有 tag 才发版），所以日常合并不会让用户被更新打扰。
- 本仓库没有 `.circleci/config.yml`，CircleCI 侧无需配置；发版链路全在 GitHub Actions。
