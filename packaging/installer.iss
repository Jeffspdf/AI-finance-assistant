#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
[Setup]
AppId={{A88EA818-B539-4A70-B546-032EE1893D91}
AppName=拾光账本
AppVersion={#AppVersion}
AppPublisher=Shiguang Ledger Project
DefaultDirName={localappdata}\Programs\ShiguangLedger
DefaultGroupName=拾光账本
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=ShiguangLedger-{#AppVersion}-Setup-x64
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\ShiguangLedger.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
DisableProgramGroupPage=yes
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: checkedonce
[Files]
Source: "..\dist\ShiguangLedger\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{autoprograms}\拾光账本"; Filename: "{app}\ShiguangLedger.exe"
Name: "{autodesktop}\拾光账本"; Filename: "{app}\ShiguangLedger.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\ShiguangLedger.exe"; Description: "Launch Shiguang Ledger"; Flags: nowait postinstall skipifsilent
