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
Filename: "{app}\ZapretAuto.exe"; Flags: nowait shellexec; Check: IsSelfUpdate

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/F /IM ZapretAuto.exe"; Flags: runhidden; RunOnceId: "KillApp"
Filename: "taskkill.exe"; Parameters: "/F /IM winws.exe"; Flags: runhidden; RunOnceId: "KillWinws"
Filename: "schtasks.exe"; Parameters: "/Delete /TN ZapretAuto /F"; Flags: runhidden; RunOnceId: "DelTask"
Filename: "sc.exe"; Parameters: "stop WinDivert"; Flags: runhidden; RunOnceId: "StopWD"
Filename: "sc.exe"; Parameters: "delete WinDivert"; Flags: runhidden; RunOnceId: "DelWD"
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Remove-MpPreference -ExclusionPath '{app}'"""; Flags: runhidden; RunOnceId: "DelExcl"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\vendor"

[Code]
function IsSelfUpdate: Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') = '1';
end;
