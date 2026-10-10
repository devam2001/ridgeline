; Inno Setup script for the Ridgeline Windows installer.
; Build it with installer\build.ps1, which stages a private Python with every
; dependency, FreqUI and the app files, then compiles this script.
;
; Installs per user (no admin rights) into %LOCALAPPDATA%\Programs\Ridgeline.
; Everything under user_data\ that you create (private config, paper
; sessions, market data, audit trail) is kept on upgrade and on uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#ifndef StageDir
  #define StageDir "..\build\stage"
#endif
#ifndef OutputDir
  #define OutputDir "..\build\installer"
#endif

[Setup]
AppId={{6F7C2B1E-3A54-4F0B-9C8D-2E51A9D47B10}
AppName=Ridgeline
AppVersion={#AppVersion}
AppVerName=Ridgeline {#AppVersion}
AppPublisher=devam2001
AppPublisherURL=https://github.com/devam2001/ridgeline
AppSupportURL=https://github.com/devam2001/ridgeline/issues
DefaultDirName={localappdata}\Programs\Ridgeline
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
OutputDir={#OutputDir}
OutputBaseFilename=Ridgeline-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Ridgeline {#AppVersion}
UninstallDisplayIcon={app}\python\python.exe
InfoBeforeFile={#StageDir}\INSTALL-NOTES.txt

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; Start each version with a clean Python environment.
Type: filesandordirs; Name: "{app}\python"

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Excludes: "\user_data\config.json"; Flags: recursesubdirs createallsubdirs ignoreversion
; config.json holds your live pair list and settings: install it once, never overwrite it.
Source: "{#StageDir}\user_data\config.json"; DestDir: "{app}\user_data"; Flags: onlyifdoesntexist uninsneveruninstall

[Icons]
Name: "{autoprograms}\Ridgeline\Ridgeline"; Filename: "{app}\Ridgeline.cmd"; WorkingDir: "{app}"; Comment: "Start the Ridgeline panel (paper trading, replay, research)"
Name: "{autoprograms}\Ridgeline\Update market data"; Filename: "{app}\Update market data.cmd"; WorkingDir: "{app}"; Comment: "Download candles and refresh the live pair list (monthly)"
Name: "{autoprograms}\Ridgeline\Ridgeline folder"; Filename: "{app}"
Name: "{autoprograms}\Ridgeline\Uninstall Ridgeline"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Ridgeline"; Filename: "{app}\Ridgeline.cmd"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; Creates user_data\config.private.json with random API credentials (never overwrites).
Filename: "{app}\python\python.exe"; Parameters: "scripts\init_private_config.py"; WorkingDir: "{app}"; Flags: runhidden; StatusMsg: "Creating your private settings..."
Filename: "{app}\Ridgeline.cmd"; WorkingDir: "{app}"; Description: "Start Ridgeline now"; Flags: postinstall nowait skipifsilent shellexec

[UninstallDelete]
; Files Python creates at runtime; your user_data is left alone.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\panel\__pycache__"
Type: filesandordirs; Name: "{app}\ridgeline\__pycache__"
Type: filesandordirs; Name: "{app}\scripts\__pycache__"
Type: filesandordirs; Name: "{app}\user_data\strategies\__pycache__"
