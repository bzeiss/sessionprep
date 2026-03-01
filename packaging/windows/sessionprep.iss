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

#define AppName         "SessionPrep"
#define AppPublisher    "Benjamin Zeiss"
#define AppPublisherURL "https://github.com/bzeiss/sessionprep"
#define AppExe          "sessionprep-gui-win-x64.exe"
#define AppCli          "sessionprep-win-x64.exe"
#define AppIconSrc      "..\..\sessionprepgui\res\sessionprep.ico"

; ---------------------------------------------------------------------------
; Setup
; ---------------------------------------------------------------------------

[Setup]
AppId={{A9F4C2E1-7B3D-4A6E-8C1F-5D2E0B9A3C78}
AppName={#AppName}
AppVersion={#APP_VERSION}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppPublisherURL}
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

; Tell InnoSetup to broadcast WM_SETTINGCHANGE so Explorer picks up PATH changes
ChangesEnvironment=yes

; lowest = per-user by default; the dialog lets the user switch to all-users (admin).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
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
  GroupDescription: "Shortcuts:"
Name: "addtopath"; \
  Description: "Add installation directory to PATH (enables 'sessionprep' CLI in any terminal)"; \
  GroupDescription: "System:"

; ---------------------------------------------------------------------------
; Files
; ---------------------------------------------------------------------------

[Files]
; GUI standalone directory
Source: "..\..\{#DIST_DIR}\sessionprep-gui.dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

; CLI standalone directory
Source: "..\..\{#DIST_DIR}\sessionprep.dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

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
  { Registry sub-keys for the two PATH locations. }
  AdminEnvKey = 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment';
  UserEnvKey  = 'Environment';

{ Resolve the correct registry root and sub-key for the active install mode. }
procedure GetEnvKey(out RootKey: Integer; out SubKey: string);
begin
  if IsAdminInstallMode then
  begin
    RootKey := HKEY_LOCAL_MACHINE;
    SubKey  := AdminEnvKey;
  end
  else
  begin
    RootKey := HKEY_CURRENT_USER;
    SubKey  := UserEnvKey;
  end;
end;

{ Read PATH for the current install mode. Returns empty string on failure. }
function GetPath: string;
var
  RootKey:     Integer;
  SubKey, Val: string;
begin
  GetEnvKey(RootKey, SubKey);
  if not RegQueryStringValue(RootKey, SubKey, 'Path', Val) then
    Val := '';
  Result := Val;
end;

{ Write PATH using REG_EXPAND_SZ so %SystemRoot% etc. survive. }
procedure SetPath(const Path: string);
var
  RootKey: Integer;
  SubKey:  string;
begin
  GetEnvKey(RootKey, SubKey);
  RegWriteExpandStringValue(RootKey, SubKey, 'Path', Path);
end;

{ Case-insensitive check: is Dir already present in PathList? }
function DirInPath(const Dir, PathList: string): Boolean;
var
  Needle, Haystack: string;
begin
  Needle   := Lowercase(RemoveBackslash(Dir));
  Haystack := ';' + Lowercase(PathList) + ';';
  Result   := (Pos(';' + Needle + ';',  Haystack) > 0) or
              (Pos(';' + Needle + '\;', Haystack) > 0);
end;

{ Append Dir to PATH only if it is not already present. }
procedure AddDirToPath(const Dir: string);
var
  OldPath: string;
begin
  OldPath := GetPath;
  if DirInPath(Dir, OldPath) then
    Exit;  { idempotent — already present }
  if OldPath = '' then
    SetPath(Dir)
  else
    SetPath(OldPath + ';' + Dir);
end;

{ Remove Dir from PATH, handling all trailing-backslash variants. }
procedure RemoveDirFromPath(const Dir: string);
var
  OldPath, D, P: string;
begin
  OldPath := GetPath;
  D := RemoveBackslash(Dir);
  P := OldPath;
  { middle of PATH:  ;DIR\ -> ;  and  ;DIR -> (empty — merges with next ;) }
  StringChangeEx(P, ';' + D + '\', ';', False);
  StringChangeEx(P, ';' + D,       '',  False);
  { start of PATH:  DIR\; ->      and  DIR; -> (empty) }
  StringChangeEx(P, D + ';\',      '',  False);
  StringChangeEx(P, D + ';',       '',  False);
  { PATH contained only DIR }
  StringChangeEx(P, D,             '',  False);
  if P <> OldPath then
  begin
    SetPath(P);
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
