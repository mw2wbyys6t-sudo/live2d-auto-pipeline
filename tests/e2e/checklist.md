# 桌面安装包验收清单（Release Gate）

> 适用对象：Tauri NSIS 安装包（ADR-007「自带运行时」布局）。
> 登记依据：`docs/architecture/index.md` 约定——手动 e2e 用例统一登记在本文件。
>
> 判定规则：
> - **A 类**（阻断发布）：任一失败即不得发布，必须修完重打。
> - **B 类**（记录不阻断）：失败写入发布说明，说明影响面与后续计划。

## 0. 前置条件

- Windows 10/11 x64，已安装 **WebView2 Runtime**（Win11 自带；Win10 多数随 Edge 安装）。
- 有一个可用的摄像头（仅面捕相关项需要）。
- 安装包来自可信渠道：GitHub Actions 产物 `tauri-nsis-installer`，或 Release 附件。
- 记录本次验收版本号（`{res}\BUILD-INFO.json` 与 `/api/info` 应一致）。

约定：**`{res}` = 安装目录**（Tauri 在 Windows 上 `resource_dir()` 就是 exe 所在目录）。
默认安装位置为当前用户目录（`installMode: currentUser`，无需管理员），
以桌面快捷方式「属性 → 目标」里的实际路径为准。

```powershell
# 定位安装目录（改一次，后面所有命令复用）
$exe = Get-ChildItem "$env:LOCALAPPDATA\Live2D Master Agent\Live2D Master Agent.exe" -ErrorAction SilentlyContinue
if (-not $exe) { $exe = Get-ChildItem "$env:ProgramFiles\Live2D Master Agent\Live2D Master Agent.exe" -ErrorAction SilentlyContinue }
$res = if ($exe) { Split-Path $exe.FullName } else { "" }
"RES = $res"
```

---

## 1. 安装包本体（A）

- [ ] 双击能正常进入安装向导（中/英文语言选择可用）。
- [ ] 安装过程不要求管理员提权（currentUser 安装）。
- [ ] 安装包体积在预期区间（约 **0.9 GB**；**不含** torch 组 —— 那组由应用内按需下载）。
      显著偏小说明装配不完整；**超过 1.2 GB 说明 torch 组被误打回了包内**。
- [ ] 安装完成后开始菜单/桌面快捷方式存在，目标指向 `{res}\Live2D Master Agent.exe`。
- [ ] 杀毒软件未隔离安装目录下的 `live2d-api.exe` / `python\python.exe`（检查杀软隔离区）。

## 2. 落盘完整性（A，可一键跑）

```powershell
$need = @(
  "Live2D Master Agent.exe",
  "live2d-api.exe",
  "face_tracker.exe",
  "onnxruntime.dll",
  "models\face_detection_yunet_2023mar.onnx",
  "models\face_landmarker.task",
  "python\python.exe",
  "python\pythonw.exe",
  "python\Lib\site-packages",
  "python\Lib\site-packages\sitecustomize.py",
  "python\Lib\site-packages\PIL",
  "python\Lib\site-packages\numpy",
  "python\Lib\site-packages\mediapipe",
  "python\Lib\site-packages\live2d\v3",
  "app\core\workflow.py",
  "app\drivers\desktop_pet",
  "BUILD-INFO.json"
)
$missing = @()
foreach ($n in $need) {
  $p = Join-Path $res $n
  if (Test-Path $p) { "[OK]      $n" } else { "[MISSING] $n"; $missing += $n }
}
"`n缺失 $($missing.Count) 项"
```

- [ ] 缺失 0 项。
- [ ] `BUILD-INFO.json` 含 `sha256`（python-embed / yunet-onnx / face-landmarker-task）与 `sizes_bytes`。

> 说明：`models\face_landmarker.task` 是 Python 面捕走 MediaPipe **Tasks** 路径所需模型；
> `/api/diagnostics` 目前只校验 YuNet 与 `onnxruntime.dll`，这一项需人工确认。

## 3. 随包解释器自检（A）

```powershell
& "$res\python\python.exe" --version
& "$res\python\python.exe" -c "import PIL, numpy, cv2, scipy, sklearn, psd_tools, onnxruntime, mediapipe; print('IMPORTS_OK')"
& "$res\python\python.exe" -c "import core.version, live2d_builder.pipeline; print('APP_IMPORTS_OK')"
# torch 组不随包分发（见 requirements.desktop.txt 与 core/capabilities.py），
# 这里只确认它**不该**在包内 —— 有则是装配回退。
& "$res\python\python.exe" -c "import importlib.util as u; print('TORCH_IN_BUNDLE' if u.find_spec('torch') else 'TORCH_ABSENT_OK')"
```

- [ ] `--version` 输出 **Python 3.12.x**。
- [ ] `IMPORTS_OK`（核心依赖全部可导入）。
- [ ] `APP_IMPORTS_OK` —— 这一条同时验证了 `._pth` isolated 模式下
      `sitecustomize.py` 已把 `{res}\app` 注入 `sys.path`。
- [ ] `TORCH_ABSENT_OK` —— torch 组**不应**出现在包内（出现即装配回退到旧清单）。
      需要 SAM2 时走应用内「能力」页下载，见 §9。

## 4. 启动与后端（A）

- [ ] 双击快捷方式后 **10 秒内**出现应用窗口。
- [ ] 窗口**不是** `Go API backend not found` 错误页（若出现，见 §8）。
- [ ] 首次启动自动创建：
      `%APPDATA%\Live2DMasterAgent\`（含 `logs` / `hf-cache` / `characters`）
      与 `%USERPROFILE%\Documents\Live2DMasterAgent\output\`。
- [ ] 安装目录内**没有**新增 `output\` 等可写产物（用户数据不得写安装目录）。

```powershell
Invoke-RestMethod http://127.0.0.1:8080/api/health | ConvertTo-Json -Depth 6
Invoke-RestMethod http://127.0.0.1:8080/api/info   | ConvertTo-Json -Depth 6
```

- [ ] `/api/health` 返回 `success = true`、`data.python_ok = true`。
      （若返回 503 且 `python_ok = false`，看 `error` 里的具体缺失模块。）
- [ ] `/api/diagnostics` 的 `checks` 全部 `ok = true`：
      `backend`、`python`、`scripts_dir`、`output_dir`、`tracker_exe`、
      `tracker_model`、`tracker_dll`、`bind_loopback`。

```powershell
$d = Invoke-RestMethod http://127.0.0.1:8080/api/diagnostics
$d.data.checks | Format-Table key, ok, detail -AutoSize
$d.data | Select-Object python_path, output_dir
```

- [ ] `python_path` 指向 `{res}\python\pythonw.exe`（或 `python.exe`）。
- [ ] `output_dir` 指向 `Documents\Live2DMasterAgent\output`（**不是**安装目录）。
- [ ] `bind_loopback = true`（监听 `127.0.0.1`）。

## 5. 核心业务链路（A）

按 UI 从左到右走一遍（Generate → Layers → Export → Preview → Pet）：

- [ ] **Generate**：输入提示词生成一张立绘，页面出现图片；
      产物落在 `Documents\Live2DMasterAgent\output\`。
      （默认走 Pollinations 免费云，需要联网；失败时会给出明确报错而非假成功。）
- [ ] **Layers**：对刚生成的图执行分层，产出 `output\layers_*\layer_*.png`。
- [ ] **Export**：导出 Live2D，`output\live2d_exports\<角色>\` 下出现
      `.moc3` / `model3.json` / 纹理图集；`build_meta.json` 中
      `runtime_ready` 字段如实反映官方内核验收结果（不要求必为 true）。
- [ ] **Preview**：预览页能加载模型并拖动参数滑块看到形变
      （Web 侧为近似渲染，不等同官方内核画面，属已知边界）。
- [ ] **Pet**：部署桌宠后出现独立窗口；关闭后
      `GET /api/deploy/desktop/status` 返回 `deployment_status = stopped`。

## 6. 面捕（A 基础 / B 增强）

- [ ] 前端「面捕」入口能启动：`POST /api/tracking/start` → `GET /api/tracking/status`
      返回运行中（`source = native`），画面有参数变化。
- [ ] `POST /api/tracking/stop` 后状态停止，**摄像头指示灯熄灭**。
- [ ] （B）Python 桌宠跟随：若桌宠启用了 Python 面捕，检查
      `%APPDATA%\Live2DMasterAgent\logs\` 中无
      `Failed to start FaceTracker` / `未找到 face_landmarker.task`。

> 两条面捕路径：原生 `face_tracker.exe`（默认）+ Python `mediapipe`
> （0.10.30+ 走 Tasks `FaceLandmarker`）。任一不可用时应优雅降级，不得崩溃。

## 7. 进程回收与二次启动（A）

- [ ] 关闭主窗口后，任务管理器/命令里**没有**残留：
      `live2d-api.exe`、`python.exe`/`pythonw.exe`、`face_tracker.exe`。
- [ ] 立刻再次双击启动，**不报**「端口 8080 已被占用」。
- [ ] 启动期间 `http://127.0.0.1:8080/` 的 Host 白名单生效（用 `localhost` 访问正常，
      用伪造 Host 头访问返回 400）。

```powershell
Get-Process live2d-api, face_tracker, python, pythonw -ErrorAction SilentlyContinue |
  Select-Object Name, Id
```

## 8. 失败排查对照表

| 现象 | 最可能原因 | 处理 |
|---|---|---|
| 窗口显示 `Go API backend not found` | `{res}\live2d-api.exe` 缺失/被隔离；或 resources 未打进 `$RESOURCE` | 跑 §2 检查；确认 `tauri.conf.json` 为 `resources: {"bundle/": ""}` |
| 应用白屏 | WebView2 运行时缺失 | 安装 Evergreen WebView2 Runtime |
| `/api/health` 503、`python_ok=false` | 随包解释器/依赖缺失 | 跑 §3；看 `error` 里的模块名 |
| `import core.version` 失败 | `sitecustomize.py` 未生效 / `{res}\app` 缺失 | 检查 `python\Lib\site-packages\sitecustomize.py` 与 `_pth` 是否含 `import site` |
| 报「端口 8080 已被占用」 | 上一次 `live2d-api.exe` 未退出 | 结束进程后重启；确认关窗回收逻辑 |
| 面捕无参数/回退肤色检测 | `models\face_detection_yunet_2023mar.onnx` 或 `onnxruntime.dll` 缺失 | §4 diagnostics 的 `tracker_model` / `tracker_dll` |
| Python 桌宠面捕不可用 | `models\face_landmarker.task` 缺失，或 mediapipe 版本不符 | 确认 `mediapipe>=0.10.30,<0.10.32` 与模型文件 |
| 生成一直失败 | 无网络（默认云生图）或 API Key 未配置 | 配置 Key 或联网后重试；查看页面报错原文 |
| SmartScreen 拦截 | 未签代码证书 | 「更多信息 → 仍要运行」；发布说明中如实标注 |

## 9. 已知非阻断项（B）

- 安装包**未签名**：SmartScreen 告警属预期。
- 预览页是 Web 侧近似渲染（非官方 Cubism 内核画面）。
- **torch 组不随包分发**（torch / torchvision / transformers / segment-anything /
  huggingface-hub）：安装包因此少约 500MB。默认分层走 `isnet`（rembg 的 ISNet
  权重已内置），不受影响；桌面端 `auto` 后端要「有 torch **且**有 SAM 权重」才
  会选 `sam`，而包内本来就不带 SAM 权重。
- SAM2 / 其他重型能力走应用内按需下载（`/api/capabilities`）。
- 官方内核逐像素验收依赖运行环境 GL 上下文；某些机器上会如实标记
  `runtime_ready=false`，不代表导出文件不可用。

## 10. 结果记录

| 项 | 内容 |
|---|---|
| 验收人 | |
| 日期 | |
| 安装包版本 / SHA256 | |
| A 类结果 | 通过 / 不通过（列出失败项） |
| B 类结果 | 记录 |
| 结论 | 可发布 / 退回修复 |
