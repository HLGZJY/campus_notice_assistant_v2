; 校园通知智能助手 Inno Setup 脚本（PACKAGING.md 实施步骤 4）
;
; 用法（由 build.py --innosetup 自动调用，或手动在 Inno Setup 编译器打开）：
;   ISCC.exe /DFlavor=cloud /DVersion=0.1.0 packaging\campus_notice.iss
;
; 关键设计（对应 PACKAGING.md 关键决策记录）：
;   - 安装到 %LOCALAPPDATA%\CampusNoticeAssistant（per-user，无需管理员，
;     运行时写 .env / config / data 不触发 UAC）
;   - 卸载/升级均不触碰 data\（用户数据：通知库 / 向量库 / 日志）
;   - 升级前把用户改过的 app.yaml 备份为 app.yaml.old（代码迭代可能依赖新配置，
;     覆盖安装仍以新配置为准，备份用于人工找回）
;   - 桌面快捷方式默认勾选；开机自启默认不勾选

#ifndef Flavor
#define Flavor "cloud"
#endif
#ifndef Version
#define Version ReadIni(SourcePath + "\version.ini", "Version", "value", "0.0.0")
#endif

#if Flavor == "cloud"
#define SetupSuffix "云端版"
#else
#define SetupSuffix "完整版"
#endif

#define MyAppName "校园通知智能助手"
#define MyAppNameEn "CampusNoticeAssistant"
#define MyAppExeName "CampusNoticeAssistant.exe"
#define MyAppPublisher "HLGZJY"
#define DistDir SourcePath + "\dist-" + Flavor + "\CampusNoticeAssistant"

[Setup]
AppId={{8B3D7C2A-6F1E-4C9B-9A5D-1E2F3A4B5C6D}
AppName={#MyAppName}
AppVersion={#Version}
AppVerName={#MyAppName} {#Version}（{#SetupSuffix}）
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
; per-user 安装：不申请管理员权限（这是选 %LOCALAPPDATA% 的核心原因）
PrivilegesRequired=lowest
OutputDir={#SourcePath}\out
OutputBaseFilename=校园通知助手-{#SetupSuffix}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; 无代码签名证书（个人开发者），关闭 UAC 对未签名安装器的额外弹窗提示可选项
DisableProgramGroupPage=yes
; 中文界面（Inno Setup 6 自带；若你的安装目录缺该文件，注释掉下一行即可回退英文）
[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; GroupDescription: "附加任务:"
Name: "autostart"; Description: "开机自动启动(&S)"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs

[Dirs]
; 数据目录：卸载不删（升级覆盖安装时 Inno 只覆盖 [Files] 注册的文件，天然保留）
Name: "{app}\data"; Flags: uninsneveruninstall
Name: "{app}\data\logs"; Flags: uninsneveruninstall

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; 开机自启（HKCU 当前用户，卸载时清理）
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
    ValueName: "{#MyAppNameEn}"; ValueData: """{app}\{#MyAppExeName}"""; \
    Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigPath, BackupPath: string;
begin
  // 覆盖安装前备份用户可能改过的主配置（app.yaml 会被新版本覆盖）
  if CurStep = ssInstall then
  begin
    ConfigPath := ExpandConstant('{app}\config\app.yaml');
    BackupPath := ExpandConstant('{app}\config\app.yaml.old');
    if FileExists(ConfigPath) then
    begin
      if FileExists(BackupPath) then
        DeleteFile(BackupPath);
      RenameFile(ConfigPath, BackupPath);
      Log('已备份 app.yaml → app.yaml.old');
    end;
  end;
end;
