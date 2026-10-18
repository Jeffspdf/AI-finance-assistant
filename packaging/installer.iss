#ifndef AppVersion
  #define AppVersion "0.4.1"
#endif
[Setup]
AppId={{A88EA818-B539-4A70-B546-032EE1893D91}
AppName=AI理财小助手
AppVersion={#AppVersion}
AppPublisher=AI Finance Helper Project
DefaultDirName={localappdata}\Programs\ShiguangLedger
DefaultGroupName=AI理财小助手
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=AIFinanceHelper-{#AppVersion}-Setup-x64
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\AIFinanceHelper.exe
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
Source: "..\dist\AIFinanceHelper\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[InstallDelete]
Type: files; Name: "{autodesktop}\拾光账本.lnk"
Type: files; Name: "{autoprograms}\拾光账本.lnk"
[Icons]
Name: "{autoprograms}\AI理财小助手"; Filename: "{app}\AIFinanceHelper.exe"
Name: "{autodesktop}\AI理财小助手"; Filename: "{app}\AIFinanceHelper.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\AIFinanceHelper.exe"; Description: "Launch AI Finance Helper"; Flags: nowait postinstall skipifsilent
