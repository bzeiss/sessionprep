; SessionPrep Windows Installer (Inno Setup 6)
;
; Build from repo root:
;   ISCC /DAPP_VERSION=x.y.z /DDIST_DIR=dist_nuitka packaging\windows\sessionprep.iss

; ---------------------------------------------------------------------------
; Defines
; ---------------------------------------------------------------------------

#ifndef APP_VERSION
  #define APP_VERSION "0.0.0"
#endif
#ifndef DIST_DIR
  #define DIST_DIR "dist_nuitka"
#endif

#define AppName      "SessionPrep"
#define AppPublisher "SessionPrep"
#define AppExe       "sessionprep-gui-win-x64.exe"
#define AppCli       "sessionprep-cli-win-x64.exe"
#define AppIconSrc   "..\..\sessionprepgui\res\sessionprep.ico"

; ---------------------------------------------------------------------------
; Setup
; ---------------------------------------------------------------------------

[Setup]
AppId={{A9F4C2E1-7B3D-4A6E-8C1F-5D2E0B9A3C78}
AppName={#AppName}
AppVersion={#APP_VERSION}
AppPublisher={#AppPublisher}
AppVerName={#AppName} {#APP_VERSION}

DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}

OutputDir=..\..\{#DIST_DIR}
OutputBaseFilename={#AppName}-{#APP_VERSION}-setup

SetupIconFile={#AppIconSrc}
UninstallDisplayIcon={app}\sessionprep.ico

Compression=lzma
SolidCompression=yes
WizardStyle=modern

PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; ---------------------------------------------------------------------------
; Languages
; ---------------------------------------------------------------------------

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

; ---------------------------------------------------------------------------
; Tasks
; ---------------------------------------------------------------------------

[Tasks]
Name: "startmenu"; \
  Description: "Create a Start Menu shortcut for SessionPrep GUI"; \
  GroupDescription: "Shortcuts:"; \
  Flags: checked
Name: "addtopath"; \
  Description: "Add installation directory to PATH (enables 'sessionprep' CLI in any terminal)"; \
  GroupDescription: "System:"; \
  Flags: checked

; ---------------------------------------------------------------------------
; Files
; ---------------------------------------------------------------------------

[Files]
; GUI executable
Source: "..\..\{#DIST_DIR}\{#AppExe}"; \
  DestDir: "{app}"; \
  Flags: ignoreversion

; CLI executable
Source: "..\..\{#DIST_DIR}\{#AppCli}"; \
  DestDir: "{app}"; \
  Flags: ignoreversion

; Icon (used by the uninstaller entry and shortcuts)
Source: "{#AppIconSrc}"; \
  DestDir: "{app}"; \
  DestName: "sessionprep.ico"; \
  Flags: ignoreversion

; ---------------------------------------------------------------------------
; Shortcuts
; ---------------------------------------------------------------------------

[Icons]
Name: "{group}\{#AppName}"; \
  Filename: "{app}\{#AppExe}"; \
  IconFilename: "{app}\sessionprep.ico"; \
  Tasks: startmenu

; ---------------------------------------------------------------------------
; Code  —  PATH add/remove
; ---------------------------------------------------------------------------

[Code]

const
  SysEnvKey = 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment';

{ Read the current system PATH from the registry. }
function GetSystemPath: string;
var
  Path: string;
begin
  if not RegQueryStringValue(HKEY_LOCAL_MACHINE, SysEnvKey, 'Path', Path) then
    Path := '';
  Result := Path;
end;

{ Write back to the registry using REG_EXPAND_SZ so %SystemRoot% etc. survive. }
procedure SetSystemPath(const Path: string);
begin
  RegWriteExpandStringValue(HKEY_LOCAL_MACHINE, SysEnvKey, 'Path', Path);
end;

{ Case-insensitive check: is Dir already present in PathList? }
function DirInPath(const Dir, PathList: string): Boolean;
var
  Needle, Haystack: string;
begin
  Needle   := Lowercase(RemoveBackslash(Dir));
  Haystack := ';' + Lowercase(PathList) + ';';
  Result   := (Pos(';' + Needle + ';',    Haystack) > 0) or
              (Pos(';' + Needle + '\;',   Haystack) > 0);
end;

{ Add Dir to the system PATH only if it is not already present. }
procedure AddDirToPath(const Dir: string);
var
  OldPath: string;
begin
  OldPath := GetSystemPath;
  if DirInPath(Dir, OldPath) then
    Exit;  { already there — nothing to do }
  if OldPath = '' then
    SetSystemPath(Dir)
  else
    SetSystemPath(OldPath + ';' + Dir);
  RefreshEnvironment;
end;

{ Remove Dir from the system PATH (handles trailing backslash variants). }
procedure RemoveDirFromPath(const Dir: string);
var
  OldPath, D, P: string;
begin
  OldPath := GetSystemPath;
  D := RemoveBackslash(Dir);
  P := OldPath;
  StringChangeEx(P, ';' + D + '\', ';', False);
  StringChangeEx(P, ';' + D,       '',  False);
  StringChangeEx(P, D + ';\',      '',  False);
  StringChangeEx(P, D + ';',       '',  False);
  StringChangeEx(P, D,             '',  False);
  if P <> OldPath then
  begin
    SetSystemPath(P);
    RefreshEnvironment;
  end;
end;

{ Called by the installer after files are laid down. }
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    if WizardIsTaskSelected('addtopath') then
      AddDirToPath(ExpandConstant('{app}'));
end;

{ Called by the uninstaller after files are removed. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemoveDirFromPath(ExpandConstant('{app}'));
end;
