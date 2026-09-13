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
#elif Flavor == "desktop"
#define SetupSuffix "桌面版"
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
; 中文界面：Inno 官方不带简中语言包，本仓库自带 packaging/ChineseSimplified.isl
; （相对路径基于脚本目录解析，本机与 CI runner 通用）
[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

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
// ---------------------------------------------------------------------------
// WebView2 运行时检测与静默安装（B19.T3 / DESKTOP-UPGRADE.md R2）
//
// 桌面版（{#Flavor == "desktop"}）依赖系统 WebView2 运行时承载 UI。老 Win10
// 可能未预装。安装时检测注册表，缺失则静默安装微软 Evergreen Bootstrapper
// （go.microsoft.com/fwlink/p/?LinkId=2124703，官方分发渠道）。
//
// 检测依据：WebView2 Runtime 的 Evergreen 安装态在 EdgeUpdate Clients 下
// 的固定 AppGUID 上记录版本号 `pv`。三处都查（用户级 / x64 系统级 / 32 位系统级），
// 任一命中即视为已装，跳过静默安装。在线版（cloud/full）走浏览器，不依赖 WebView2，
// 故仅 desktop flavor 执行检测。
//
// 静默安装方式：下载官方 Evergreen Bootstrapper 到 {tmp}，以
//   MicrosoftEdgeWebview2Setup.exe --silent --install
// 运行并等待完成（--silent 免交互，--install 装 Evergreen 运行时）。
// 幂等：已装则直接跳过；安装失败仅 Log + 弹提示，不阻断桌面版安装
// （兜底：桌面版启动时若仍无 WebView2，壳会走 --browser 降级，见 R2 对策）。
// ---------------------------------------------------------------------------

const
  // WebView2 Runtime 的固定 AppGUID（Evergreen 安装态在 EdgeUpdate Clients 下）
  WebView2AppGuid = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  // 官方 Evergreen Bootstrapper 下载地址（微软 go 短链）
  WebView2BootstrapperUrl = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';

// 检测 WebView2 运行时是否已安装（三处注册表任一命中版本号即视为已装）
function IsWebView2Installed(): Boolean;
var
  Version: string;
begin
  Result := False;
  // 1. 用户级安装（per-user，最常见于 {localappdata} 安装）
  if RegQueryStringValue(HKCU,
     'Software\Microsoft\EdgeUpdate\Clients\' + WebView2AppGuid, 'pv', Version) then
  begin
    if Version <> '' then
    begin
      Log('WebView2 已装（HKCU 用户级，pv=' + Version + '）');
      Result := True;
      Exit;
    end;
  end;
  // 2. x64 系统级安装（WOW6432Node 视图）
  if RegQueryStringValue(HKLM32,
     'Software\WOW6432Node\Microsoft\EdgeUpdate\Clients\' + WebView2AppGuid, 'pv', Version) then
  begin
    if Version <> '' then
    begin
      Log('WebView2 已装（HKLM x64，pv=' + Version + '）');
      Result := True;
      Exit;
    end;
  end;
  // 3. 32 位系统级安装
  if RegQueryStringValue(HKLM32,
     'Software\Microsoft\EdgeUpdate\Clients\' + WebView2AppGuid, 'pv', Version) then
  begin
    if Version <> '' then
    begin
      Log('WebView2 已装（HKLM x86，pv=' + Version + '）');
      Result := True;
      Exit;
    end;
  end;
  Log('未检测到 WebView2 运行时');
end;

// 用 PowerShell 下载 Evergreen Bootstrapper 到 {tmp} 并返回本地路径（成功）或 ''（失败）
function DownloadWebView2Bootstrapper(TargetPath: string): Boolean;
var
  PSArgs: string;
  RetCode: Integer;
begin
  // 单行 PowerShell：Invoke-WebRequest 下载到目标路径；失败抛异常 → Inno 判非 0
  PSArgs := '-NoProfile -ExecutionPolicy Bypass -Command "' +
    'try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; ' +
    'Invoke-WebRequest -Uri ' + WebView2BootstrapperUrl + ' -OutFile ' +
    '"' + TargetPath + '" -UseBasicParsing; exit 0 } catch { Write-Error $_; exit 1 }"';
  // Exec 的 ShowWindow: 0=隐藏；Wait: 1=等待完成（MWait）
  if Exec('powershell.exe', PSArgs, '', SW_HIDE, ewWaitUntilTerminated, RetCode) and (RetCode = 0) then
  begin
    Log('WebView2 Bootstrapper 下载成功：' + TargetPath);
    Result := True;
  end
  else
  begin
    Log('WebView2 Bootstrapper 下载失败（RetCode=' + IntToStr(RetCode) + '）');
    Result := False;
  end;
end;

// 桌面版：检测并静默安装 WebView2（缺失时）
procedure EnsureWebView2();
var
  TmpBoot, RetCode: Integer;
begin
  if IsWebView2Installed() then
    Exit;
  Log('WebView2 缺失，开始静默安装 Evergreen Bootstrapper……');
  TmpBoot := 0;
  if not DownloadWebView2Bootstrapper(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe')) then
  begin
    // 下载失败不阻断安装（R2 兜底：壳可 --browser 降级）
    MsgBox('未能下载 WebView2 运行时，桌面版可能无法以原生窗口启动。'#13#10 +
      '可到微软官网手动安装 WebView2 运行时，或使用浏览器降级模式。',
      mbInformation, MB_OK);
    Exit;
  end;
  // --silent 免交互、--install 装 Evergreen 运行时；等待完成
  if Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'),
          '--silent --install', '', SW_HIDE, ewWaitUntilTerminated, RetCode) and (RetCode = 0) then
    Log('WebView2 Evergreen 静默安装成功')
  else
  begin
    Log('WebView2 静默安装返回非 0（RetCode=' + IntToStr(RetCode) + '）');
    MsgBox('WebView2 运行时安装未成功，桌面版可能无法以原生窗口启动。'#13#10 +
      '可到微软官网手动安装 WebView2 运行时，或使用浏览器降级模式。',
      mbInformation, MB_OK);
  end;
end;

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
  end
  // 桌面版：文件装完后检测并静默安装 WebView2（在线版走浏览器，不需要）
  else if (CurStep = ssPostInstall) and ('{#Flavor}' = 'desktop') then
  begin
    EnsureWebView2();
  end;
end;
