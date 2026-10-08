; ---------------------------------------------------------------------------
; Live2D Master Agent - Windows 安装包（Inno Setup 6）
;
; 编译（本机当前没有 iscc.exe，需先自行安装 Inno Setup）：
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" deploy\installer\desktop.iss
;
; 前置：先跑 `python scripts/build_portable.py` 生成 ..\..\runtime\
; （便携 Python + CUDA torch + 只含 safetensors 的权重缓存）。
;
; 安装后目录：
;   {app}\Live2DMasterAgent.exe        Go 桌面程序（内嵌 Web 工作台）
;   {app}\runtime\python\              便携解释器
;   {app}\runtime\hf-cache\            模型权重
;   {app}\app\                         Python 工程（core/drivers/... ）
;   {app}\config.portable.json         安装时按 {app} 生成，见 [Code]
;
; 已知未接上的一项：HF_HOME。权重缓存在 {app}\runtime\hf-cache，但
; transformers 默认读用户目录下的缓存；Go 侧已采纳方案 B：python_bridge
; 在 exec 时注入 HF_HOME / HUGGINGFACE_HUB_CACHE / TRANSFORMERS_CACHE
; （api/services/python_bridge.go executePythonScript 的 cmd.Env）。
; 桌面程序读 config.portable.json 的 hf_cache 字段（config.go 的扁平键
; 迁移），无需写注册表或改 Python 启动脚本。
; ---------------------------------------------------------------------------

#define MyAppName      "Live2D Master Agent"
#define MyAppVersion   "0.10.2"
#define MyAppPublisher "Live2D Master Agent"
#define MyAppExeName   "Live2DMasterAgent.exe"
#define ProjectRoot    "..\.."
#define RuntimeDir     ProjectRoot + "\runtime"

[Setup]
AppId={{8F3C1D7A-6E2B-4C55-9A31-7D2C5B0E91F4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=Live2DMasterAgent-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
SetupIconFile=

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english";           MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
  GroupDescription: "{cm:AdditionalIcons}"

[Files]
; 桌面程序（scripts/build_desktop.bat 产物）
Source: "{#ProjectRoot}\dist\Live2DMasterAgent.exe"; DestDir: "{app}"; \
  Flags: ignoreversion
; 便携 Python 与权重
Source: "{#RuntimeDir}\python\*";  DestDir: "{app}\runtime\python";   \
  Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#RuntimeDir}\hf-cache\*"; DestDir: "{app}\runtime\hf-cache"; \
  Flags: ignoreversion recursesubdirs createallsubdirs
; Python 工程：Go 桥以 scripts_dir 为 PYTHONPATH 直接跑 core/workflow.py
Source: "{#ProjectRoot}\core\*.py";            DestDir: "{app}\app\core"; \
  Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ProjectRoot}\drivers\*";            DestDir: "{app}\app\drivers"; \
  Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ProjectRoot}\live2d_builder\*";     DestDir: "{app}\app\live2d_builder"; \
  Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ProjectRoot}\llm_bridge\*";         DestDir: "{app}\app\llm_bridge"; \
  Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ProjectRoot}\scripts\*";            DestDir: "{app}\app\scripts"; \
  Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ProjectRoot}\prompts\*";            DestDir: "{app}\app\prompts"; \
  Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ProjectRoot}\templates\*";          DestDir: "{app}\app\templates"; \
  Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ProjectRoot}\requirements.txt";     DestDir: "{app}\app"; Flags: ignoreversion
Source: "{#ProjectRoot}\api_server.py";        DestDir: "{app}\app"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
  Parameters: "-config ""{app}\config.portable.json"""
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; \
  Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
  Parameters: "-config ""{app}\config.portable.json"""; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; \
  Parameters: "-config ""{app}\config.portable.json"""; \
  Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; \
  Flags: nowait postinstall skipifsilent

[Code]
// 安装时按实际安装目录生成配置：build_portable.py 写的是开发机绝对路径，
// 直接复制过去会让所有用户指到作者的盘符上。
procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigPath, PythonExe, ScriptsDir, HfCache: String;
  Lines: TArrayOfString;
begin
  if CurStep <> ssPostInstall then Exit;

  PythonExe   := ExpandConstant('{app}\runtime\python\Scripts\python.exe');
  ScriptsDir  := ExpandConstant('{app}\app');
  HfCache     := ExpandConstant('{app}\runtime\hf-cache');
  ConfigPath  := ExpandConstant('{app}\config.portable.json');

  // HF_HOME 已采纳方案 B：Go 侧 python_bridge 在 exec 时注入环境变量
  // （api/services/python_bridge.go executePythonScript）。本安装脚本只需
  // 把 hf_cache 写进 config.portable.json，桌面程序读到即生效，无需在此
  // 写注册表或改 Python 启动脚本。
  SetArrayLength(Lines, 5);
  Lines[0] := '{';
  Lines[1] := '  "python_path": "' + StringReplace(PythonExe,  '\', '\\', rfReplaceAll) + '",';
  Lines[2] := '  "scripts_dir": "' + StringReplace(ScriptsDir, '\', '\\', rfReplaceAll) + '",';
  Lines[3] := '  "hf_cache":    "' + StringReplace(HfCache,    '\', '\\', rfReplaceAll) + '"';
  Lines[4] := '}';

  if not SaveStringsToUTF8File(ConfigPath, Lines, False) then
    Log('写入 config.portable.json 失败: ' + ConfigPath);
end;
