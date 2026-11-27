; 安装包仅包含运行文件；业务库、日志及备份始终保存在用户选择的独立目录。
#ifndef AppVersion
  #error 请使用项目根目录的制作工具编译安装包
#endif

[Setup]
AppId={{EF00F632-38D8-4885-AFD3-DCED58A4E9F2}
AppName=BigScreen 生产监控
AppVersion={#AppVersion}
AppVerName=BigScreen 生产监控 {#AppVersion}
DefaultDirName={autopf}\BigScreen
DefaultGroupName=BigScreen 生产监控
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=BigScreen-{#AppVersion}-win-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
UninstallDisplayIcon={app}\BigScreen.exe
CloseApplications=no
RestartApplications=no
; 升级和卸载前需由维护人员验证退出展示，避免守护进程持有旧文件。
AppMutex=Local\BigScreen.Display.Running
SetupLogging=yes
VersionInfoVersion={#AppVersion}

[Languages]
Name: "chinesesimp"; MessagesFile: "{#LanguageFile}"

[Tasks]
Name: "firewall"; Description: "允许局域网设备访问（仅专用网络和域网络）"; Flags: checkedonce
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked
Name: "displaystartup"; Description: "当前用户登录 Windows 后自动打开大屏展示"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "BigScreen.ini,BigScreen.Service.xml,*.cmd"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "BigScreen.Service.xml"; Flags: dontcopy

[Icons]
Name: "{group}\生产监控大屏"; Filename: "{app}\BigScreen.Display.exe"
Name: "{group}\大屏展示设置"; Filename: "{app}\BigScreen.Display.exe"; Parameters: "--configure"
Name: "{group}\数据编辑后台"; Filename: "http://127.0.0.1:{code:GetPort}/edit"
Name: "{group}\使用说明"; Filename: "{app}\使用说明.txt"
Name: "{group}\卸载 BigScreen"; Filename: "{uninstallexe}"
Name: "{autodesktop}\生产监控大屏"; Filename: "{app}\BigScreen.Display.exe"; Tasks: desktopicon
Name: "{userstartup}\BigScreen 大屏展示"; Filename: "{app}\BigScreen.Display.exe"; Tasks: displaystartup

[Run]
Filename: "{app}\BigScreen.Display.exe"; Description: "打开生产监控大屏"; Flags: postinstall skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{app}\BigScreen.Service.exe"; Parameters: "stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopBigScreen"
Filename: "{app}\BigScreen.Service.exe"; Parameters: "uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "UninstallBigScreenService"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""BigScreen-HTTP"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveBigScreenFirewall"

[Code]
var
  DataPage: TInputDirWizardPage;
  PortPage: TInputQueryWizardPage;
  ServiceStopped: Boolean;

function GetPort(Param: String): String;
begin
  { Param 由安装器传入且未使用；返回用户选定的端口。 }
  Result := PortPage.Values[0];
end;

function GetDataDir: String;
begin
  { 无参数；返回去掉末尾分隔符的数据目录。 }
  Result := RemoveBackslashUnlessRoot(DataPage.Values[0]);
end;

function SaveUtf8(FileName, Content: String): Boolean;
begin
  { FileName 为目标文件、Content 为 Unicode 内容；返回 UTF-8 原始字节保存结果。 }
  Result := SaveStringToFile(FileName, UTF8Encode(Content), False);
end;

procedure RunChecked(FileName, Parameters, Description: String);
var
  ExitCode: Integer;
begin
  { 参数指定命令及用途；执行失败抛错并保留安装日志，无返回值。 }
  Log(Description + ': ' + FileName + ' ' + Parameters);
  if not Exec(FileName, Parameters, '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then
    RaiseException(Description + '：无法启动命令。请查看安装日志。');
  if ExitCode <> 0 then
    RaiseException(Description + '失败，退出码 ' + IntToStr(ExitCode) + '。请查看安装日志。');
end;

function InitializeSetup: Boolean;
begin
  { 无参数；返回当前系统是否满足展示程序所需的 .NET Framework 4.6.2。 }
  Result := IsDotNetInstalled(net462, 0);
  if not Result then
    MsgBox('此安装包需要 .NET Framework 4.6.2 或以上版本。请先安装，建议使用 Windows 10/11。', mbError, MB_OK);
end;

procedure EnsureWebViewRuntime;
var
  ExitCode: Integer;
  Version: String;
begin
  { 无参数；安装随包提供的微软离线内核，已有更新版本时保留现有版本，无返回值。 }
  if RegQueryStringValue(HKLM32,
      'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) then
    if StrToIntDef(Copy(Version, 1, Pos('.', Version) - 1), 0) >= 131 then Exit;
  if not Exec(ExpandConstant('{app}\MicrosoftEdgeWebView2RuntimeInstallerX64.exe'),
      '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then
    RaiseException('无法启动 WebView2 离线运行库安装程序。');
  if (ExitCode <> 0) and (ExitCode <> 3010) then
    RaiseException('WebView2 离线运行库安装失败，退出码 ' + IntToStr(ExitCode) + '。');
end;

procedure InitializeWizard;
begin
  { 无参数；建立独立的数据目录及端口页面，升级默认沿用上次选择。 }
  DataPage := CreateInputDirPage(wpSelectDir, '选择数据保存目录',
    '数据库、日志和升级备份将保存在这里',
    '请选择本机磁盘上的独立文件夹。卸载程序会保留此目录。', False, 'BigScreenData');
  DataPage.Add('数据目录：');
  DataPage.Values[0] := GetPreviousData('DataDir', ExpandConstant('{sd}\BigScreenData'));
  PortPage := CreateInputQueryPage(DataPage.ID, '设置服务端口',
    '局域网设备通过此端口访问大屏和后台', '请使用未被其他程序占用的端口。');
  PortPage.Add('端口：', False);
  PortPage.Values[0] := GetPreviousData('Port', '{#DefaultPort}');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Directory, PreviousDirectory: String;
  Port: Integer;
begin
  { CurPageID 为当前向导页；校验目录及端口，返回是否允许继续。 }
  Result := True;
  if CurPageID = DataPage.ID then begin
    Directory := GetDataDir;
    PreviousDirectory := GetPreviousData('DataDir', '');
    if (Length(Directory) < 4) or (Copy(Directory, 2, 2) <> ':\') or
       (Pos(Lowercase(AddBackslash(ExpandConstant('{win}'))), Lowercase(AddBackslash(Directory))) = 1) or
       (Pos(Lowercase(AddBackslash(ExpandConstant('{app}'))), Lowercase(AddBackslash(Directory))) = 1) then begin
      MsgBox('请选择本机磁盘上的独立文件夹，例如 D:\BigScreenData；不要选择磁盘根目录、Windows 目录或程序安装目录。', mbError, MB_OK);
      Result := False;
    end else if (PreviousDirectory <> '') and
        (CompareText(PreviousDirectory, Directory) <> 0) then begin
      { 升级不隐式迁移数据库，避免误把新目录当作旧业务库。 }
      MsgBox('升级请沿用原数据目录：' + PreviousDirectory + #13#10 +
        '如需迁移，请先停止服务并完整迁移数据，再重新安装。', mbError, MB_OK);
      Result := False;
    end;
  end;
  if CurPageID = PortPage.ID then begin
    Port := StrToIntDef(PortPage.Values[0], 0);
    Result := (Port >= 1) and (Port <= 65535);
    if not Result then MsgBox('端口必须是 1～65535 的整数。', mbError, MB_OK);
  end;
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  { PreviousDataKey 为安装器记录句柄；保存数据目录和端口供升级复用，无返回值。 }
  SetPreviousData(PreviousDataKey, 'DataDir', GetDataDir);
  SetPreviousData(PreviousDataKey, 'Port', GetPort(''));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ImagePath, ExpectedPath: String;
begin
  { NeedsRestart 为安装器状态；停止本项目旧服务，返回空字符串表示可继续。 }
  Result := '';
  if RegQueryStringValue(HKLM, 'SYSTEM\CurrentControlSet\Services\BigScreen', 'ImagePath', ImagePath) then begin
    ExpectedPath := ExpandConstant('{app}\BigScreen.Service.exe');
    if CompareText(RemoveQuotes(ImagePath), ExpectedPath) <> 0 then begin
      Result := '发现其他位置的 BigScreen 服务：' + ImagePath + #13#10 +
        '请使用原程序安装目录进行升级，或先卸载原程序（业务数据会保留）。';
      Exit;
    end;
    try
      RunChecked(ExpectedPath, 'stop', '停止旧服务');
      ServiceStopped := True;
    except
      Result := GetExceptionMessage;
    end;
  end;
end;

procedure VerifyService;
var
  Request: Variant;
  ResponseText: String;
  Attempt: Integer;
begin
  { 无参数；等待数据库健康接口可用，超时抛出包含排查路径的错误。 }
  for Attempt := 1 to 30 do begin
    try
      Request := CreateOleObject('WinHttp.WinHttpRequest.5.1');
      Request.SetProxy(1);
      Request.SetTimeouts(1000, 1000, 1000, 1000);
      Request.Open('GET', 'http://127.0.0.1:' + GetPort('') + '/api/health', False);
      Request.Send;
      ResponseText := Request.ResponseText;
      if (Request.Status = 200) and (Pos('"ok"', ResponseText) > 0) then Exit;
    except
      Log('服务尚未就绪：' + GetExceptionMessage);
    end;
    Sleep(500);
  end;
  RaiseException('服务未通过健康检查。请检查端口 ' + GetPort('') +
    ' 是否被占用，以及数据目录权限。日志位于：' + GetDataDir + '\logs');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  DataDir, Database, Template, ServiceConfig, Parameters: String;
  TemplateBytes: AnsiString;
  ExitCode: Integer;
begin
  { CurStep 为安装阶段；复制后完成备份、外置配置、服务和防火墙设置，无返回值。 }
  if CurStep <> ssPostInstall then Exit;
  EnsureWebViewRuntime;
  DataDir := GetDataDir;
  Database := AddBackslash(DataDir) + 'dashboard.sqlite3';
  if not ForceDirectories(DataDir) then RaiseException('无法创建数据目录：' + DataDir);
  { 仅向低权限服务账号增加所选数据目录的读写权限，不更改其他文件夹权限。 }
  RunChecked(ExpandConstant('{sys}\icacls.exe'),
    '"' + DataDir + '" /grant "*S-1-5-19:(OI)(CI)M"', '设置数据目录权限');
  if FileExists(Database) then
    RunChecked(ExpandConstant('{app}\BigScreen.exe'),
      '--backup-database "' + Database + '" --backup-directory "' + DataDir + '\backups"', '备份现有数据库');
  if ServiceStopped then
    RunChecked(ExpandConstant('{app}\BigScreen.Service.exe'), 'uninstall', '更新服务注册');
  if not SaveUtf8(ExpandConstant('{app}\BigScreen.ini'),
      '[server]' + #13#10 + 'port=' + GetPort('') + #13#10 + 'data_directory=' + DataDir + #13#10) then
    RaiseException('无法写入运行配置。');
  ExtractTemporaryFile('BigScreen.Service.xml');
  if not LoadStringFromFile(ExpandConstant('{tmp}\BigScreen.Service.xml'), TemplateBytes) then
    RaiseException('无法读取服务配置模板。');
  Template := UTF8Decode(TemplateBytes);
  { XML 特殊字符必须转义，支持目录名中包含 & 等字符。 }
  ServiceConfig := DataDir + '\logs';
  StringChangeEx(ServiceConfig, '&', '&amp;', True);
  StringChangeEx(ServiceConfig, '<', '&lt;', True);
  StringChangeEx(ServiceConfig, '>', '&gt;', True);
  StringChangeEx(Template, '@LOG_DIRECTORY@', ServiceConfig, True);
  if not SaveUtf8(ExpandConstant('{app}\BigScreen.Service.xml'), Template) then
    RaiseException('无法写入服务配置。');
  RunChecked(ExpandConstant('{app}\BigScreen.Service.exe'), 'install', '注册后台服务');
  { 只维护本项目规则；公共网络不放行端口。 }
  Exec(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall delete rule name="BigScreen-HTTP"',
       '', SW_HIDE, ewWaitUntilTerminated, ExitCode);
  if WizardIsTaskSelected('firewall') then begin
    Parameters := 'advfirewall firewall add rule name="BigScreen-HTTP" dir=in action=allow' +
      ' protocol=TCP localport=' + GetPort('') + ' profile=private,domain';
    RunChecked(ExpandConstant('{sys}\netsh.exe'), Parameters, '设置局域网访问规则');
  end;
  RunChecked(ExpandConstant('{app}\BigScreen.Service.exe'), 'start', '启动后台服务');
  VerifyService;
  ServiceStopped := False;
end;

procedure DeinitializeSetup;
var
  ExitCode: Integer;
begin
  { 无参数；取消升级时尝试恢复已停止的旧服务，不删除业务数据。 }
  if ServiceStopped then
    Exec(ExpandConstant('{app}\BigScreen.Service.exe'), 'start', '', SW_HIDE, ewWaitUntilTerminated, ExitCode);
end;
