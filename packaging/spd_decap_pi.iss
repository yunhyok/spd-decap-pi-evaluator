#ifndef RepoRoot
  #define RepoRoot ".."
#endif
#ifndef AppVersion
  #define AppVersion "0.21.0"
#endif

#define AppName "SPD Decap PI Evaluator"
#define AppExeName "SPDDecapPIEvaluator.exe"

[Setup]
AppId={{9D82CD53-E925-4C44-961A-735D37728A36}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} v{#AppVersion}
AppPublisher=Probe Card MLO PDN Team
DefaultDirName={autopf}\SPD Decap PI Evaluator
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#RepoRoot}\installer-output
OutputBaseFilename=SPDDecapPIEvaluatorSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#AppExeName}

[Files]
Source: "{#RepoRoot}\dist\SPDDecapPIEvaluator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
