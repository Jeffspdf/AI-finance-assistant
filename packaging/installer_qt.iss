#ifndef AppVersion
  #define AppVersion "0.10.0"
#endif
[Setup]
AppId={{46A1E87C-1E92-47BD-BDB3-E50318298764}
AppName=AI Finance Helper Qt
AppVersion={#AppVersion}
AppPublisher=AI Finance Helper Project
DefaultDirName={localappdata}\Programs\ShiguangLedgerQt
DefaultGroupName=AI Finance Helper Qt
PrivilegesRequired=lowest
CreateUninstallRegKey=not SkipRegistration
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=AIFinanceHelperQt-{#AppVersion}-Setup-x64
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\AIFinanceHelperQt.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
DisableProgramGroupPage=yes
AllowNoIcons=yes
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"
Name: "noregistration"; Description: "Install without a Windows Installed Apps entry"; Flags: unchecked
[Files]
Source: "..\dist\AIFinanceHelperQt\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\AI Finance Helper Qt"; Filename: "{app}\AIFinanceHelperQt.exe"
Name: "{group}\Uninstall AI Finance Helper Qt"; Filename: "{uninstallexe}"; Check: SkipRegistration
Name: "{autodesktop}\AI Finance Helper Qt"; Filename: "{app}\AIFinanceHelperQt.exe"; Tasks: desktopicon
[INI]
Filename: "{app}\qt-install.ini"; Section: "installation"; Key: "registration"; String: "standalone"; Check: SkipRegistration; Flags: uninsdeletesection
Filename: "{app}\qt-install.ini"; Section: "installation"; Key: "registration"; String: "windows"; Check: not SkipRegistration; Flags: uninsdeletesection
Filename: "{app}\qt-install.ini"; Section: "installation"; Key: "version"; String: "{#AppVersion}"; Flags: uninsdeletesection
[UninstallDelete]
Type: files; Name: "{app}\qt-install.ini"
[Run]
Filename: "{app}\AIFinanceHelperQt.exe"; Description: "Launch AI Finance Helper Qt"; Flags: nowait postinstall skipifsilent

[Code]
function SkipRegistration: Boolean;
begin
  Result := (CompareText(ExpandConstant('{param:NOREGISTRATION|0}'), '1') = 0) or WizardIsTaskSelected('noregistration');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Folder: String;
begin
  Folder := ExpandConstant('{app}');
  Result := '';
  if FileExists(AddBackslash(Folder) + 'AIFinanceHelper.exe') or
     (CompareText(Folder, ExpandConstant('{localappdata}\ShiguangLedger')) = 0) or
     (CompareText(Folder, ExpandConstant('{localappdata}\ShiguangLedger-QtPreview')) = 0) or
     FileExists(AddBackslash(Folder) + 'personal.sqlite3') or
     FileExists(AddBackslash(Folder) + 'demo.sqlite3') then
    Result := 'Choose a separate program folder. This directory belongs to Tk or contains ledger data.';
end;
