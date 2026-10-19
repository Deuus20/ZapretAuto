#ifndef AppVer
#define AppVer "1.0.0"
#endif
[Setup]
AppName=ZapretAuto
AppVersion={#AppVer}
AppPublisher=ZapretAuto
DefaultDirName={autopf}\ZapretAuto
DefaultGroupName=ZapretAuto
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/max
SolidCompression=yes
OutputDir=dist
OutputBaseFilename=ZapretAuto-Setup
UninstallDisplayIcon={app}\ZapretAuto.exe
SetupIconFile=assets\icon.ico
WizardStyle=modern
CloseApplications=force

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
russian.DefExcl=Добавить папку в исключения Защитника Windows
english.DefExcl=Add the folder to Windows Defender exclusions

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "defexcl"; Description: "{cm:DefExcl}"; GroupDescription: "Windows Defender"; Flags: unchecked

[Files]
Source: "dist\ZapretAuto\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\ZapretAuto"; Filename: "{app}\ZapretAuto.exe"
Name: "{autodesktop}\ZapretAuto"; Filename: "{app}\ZapretAuto.exe"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Add-MpPreference -ExclusionPath '{app}'"""; Flags: runhidden; Tasks: defexcl
Filename: "{app}\ZapretAuto.exe"; Description: "{cm:LaunchProgram,ZapretAuto}"; Flags: nowait postinstall skipifsilent shellexec
Filename: "{app}\ZapretAuto.exe"; Parameters: "--tray"; Flags: nowait shellexec; Check: IsSelfUpdate
Filename: "{sys}\ie4uinit.exe"; Parameters: "-show"; Flags: runhidden

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/F /IM ZapretAuto.exe"; Flags: runhidden; RunOnceId: "KillApp"
Filename: "taskkill.exe"; Parameters: "/F /IM winws.exe"; Flags: runhidden; RunOnceId: "KillWinws"
Filename: "taskkill.exe"; Parameters: "/F /IM winws2.exe"; Flags: runhidden; RunOnceId: "KillWinws2"
Filename: "schtasks.exe"; Parameters: "/Delete /TN ZapretAuto /F"; Flags: runhidden; RunOnceId: "DelTask"
Filename: "sc.exe"; Parameters: "stop WinDivert"; Flags: runhidden; RunOnceId: "StopWD"
Filename: "sc.exe"; Parameters: "delete WinDivert"; Flags: runhidden; RunOnceId: "DelWD"
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Remove-MpPreference -ExclusionPath '{app}'"""; Flags: runhidden; RunOnceId: "DelExcl"
; browser policies set by diagnostics fixes (QUIC off, secure DNS): otherwise browsers keep saying "managed"
Filename: "reg.exe"; Parameters: "delete ""HKLM\SOFTWARE\Policies\Google\Chrome"" /v QuicAllowed /f"; Flags: runhidden; RunOnceId: "PolCQ"
Filename: "reg.exe"; Parameters: "delete ""HKLM\SOFTWARE\Policies\Google\Chrome"" /v DnsOverHttpsMode /f"; Flags: runhidden; RunOnceId: "PolCM"
Filename: "reg.exe"; Parameters: "delete ""HKLM\SOFTWARE\Policies\Google\Chrome"" /v DnsOverHttpsTemplates /f"; Flags: runhidden; RunOnceId: "PolCT"
Filename: "reg.exe"; Parameters: "delete ""HKLM\SOFTWARE\Policies\Microsoft\Edge"" /v QuicAllowed /f"; Flags: runhidden; RunOnceId: "PolEQ"
Filename: "reg.exe"; Parameters: "delete ""HKLM\SOFTWARE\Policies\Microsoft\Edge"" /v DnsOverHttpsMode /f"; Flags: runhidden; RunOnceId: "PolEM"
Filename: "reg.exe"; Parameters: "delete ""HKLM\SOFTWARE\Policies\Microsoft\Edge"" /v DnsOverHttpsTemplates /f"; Flags: runhidden; RunOnceId: "PolET"
Filename: "reg.exe"; Parameters: "delete ""HKLM\SOFTWARE\Policies\YandexBrowser"" /v QuicAllowed /f"; Flags: runhidden; RunOnceId: "PolYQ"
Filename: "reg.exe"; Parameters: "delete ""HKLM\SOFTWARE\Policies\YandexBrowser"" /v DnsOverHttpsMode /f"; Flags: runhidden; RunOnceId: "PolYM"
Filename: "reg.exe"; Parameters: "delete ""HKLM\SOFTWARE\Policies\YandexBrowser"" /v DnsOverHttpsTemplates /f"; Flags: runhidden; RunOnceId: "PolYT"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\vendor"
Type: filesandordirs; Name: "{commonappdata}\ZapretAuto"

[Code]
procedure RunHidden(const Exe, Params: String);
var
  Code: Integer;
begin
  Exec(Exe, Params, '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

{ WinDivert64.sys stays locked by the loaded driver even after winws exits: unload it before copying files }
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  RunHidden(ExpandConstant('{sys}\taskkill.exe'), '/F /IM ZapretAuto.exe');
  RunHidden(ExpandConstant('{sys}\taskkill.exe'), '/F /IM winws.exe');
  RunHidden(ExpandConstant('{sys}\taskkill.exe'), '/F /IM winws2.exe');
  RunHidden(ExpandConstant('{sys}\sc.exe'), 'stop WinDivert');
  RunHidden(ExpandConstant('{sys}\sc.exe'), 'delete WinDivert');
  Sleep(1500);
  Result := '';
end;

function IsSelfUpdate: Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') = '1';
end;
