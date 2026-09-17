; installer.iss â€” Inno Setup script for Presentia
;
; Prereqs before compiling this (see BUILD.md for the full pipeline):
;   1. cd presentia-desktop/frontend && npm install && cd ../..
;   2. cd presentia-desktop && wails build && cd ..
;   3. pyinstaller build/presentia-sidecar.spec --noconfirm --clean
;
; Expects, relative to this .iss file's folder (build/):
;   ..\presentia-desktop\build\bin\Presentia.exe
;   ..\dist\presentia-sidecar\                (whole onedir folder from PyInstaller)
;
; Compile with the Inno Setup Compiler (ISCC.exe) or the GUI:
;   ISCC.exe build\installer.iss
; Output: build\output\PresentiaSetup.exe

#define MyAppName "Presentia"
; Do not edit MyAppVersion by hand â€” it must match AppVersion in
; presentia-desktop\update.go and the git tag of the GitHub Release, or the
; in-app update banner will never clear. Use:
;     powershell -ExecutionPolicy Bypass -File build\set-version.ps1 1.1.0
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Presentia Team"
#define MyAppExeName "Presentia.exe"

[Setup]
AppId={{8F2C1A40-9B3D-4E6A-9C7F-PRESENTIA0001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Per-machine install needs admin; per-user avoids the UAC prompt entirely.
; Per-user is usually the better call for a school-deployed app.
PrivilegesRequired=lowest
OutputDir=output
OutputBaseFilename=PresentiaSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Presentia does its OWN first-launch wizard inside the app (system check,
; model download, camera test) â€” this installer wizard should stay minimal.
DisableWelcomePage=no
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Wails-built desktop app
Source: "..\presentia-desktop\build\bin\Presentia.exe"; DestDir: "{app}"; Flags: ignoreversion

; Frozen Python sidecar (onedir â€” exe + its _internal libs), goes in a
; "sidecar" subfolder. app.go looks for exactly this path:
;   <exeDir>\sidecar\presentia-sidecar.exe
Source: "..\dist\presentia-sidecar\*"; DestDir: "{app}\sidecar"; Flags: ignoreversion recursesubdirs createallsubdirs

; MediaPipe landmark model is already bundled INSIDE the sidecar build via
; the .spec file's datas=[...], so it does not need a separate line here.
; InsightFace's buffalo_l pack is intentionally NOT included â€” it downloads
; on first launch as part of the in-app wizard (see BUILD.md).

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up the sidecar's writable state if it ever leaves anything in {app}.
Type: filesandordirs; Name: "{app}\sidecar"

; NOTE: this does NOT delete the user's data dir (%APPDATA%\Presentia â€”
; attendance.db, InsightFace model cache) on uninstall, which matches most
; users' expectation that their data survives a reinstall/upgrade. Add an
; [UninstallDelete] entry for {userappdata}\Presentia only if you want a
; "clean uninstall" option instead.
