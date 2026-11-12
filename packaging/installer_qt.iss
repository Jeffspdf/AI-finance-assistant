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
[Files]
Source: "..\dist\AIFinanceHelperQt\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\AI Finance Helper Qt"; Filename: "{app}\AIFinanceHelperQt.exe"
Name: "{autodesktop}\AI Finance Helper Qt"; Filename: "{app}\AIFinanceHelperQt.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\AIFinanceHelperQt.exe"; Description: "Launch AI Finance Helper Qt"; Flags: nowait postinstall skipifsilent
