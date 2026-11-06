; CopyOver installer for Windows (Inno Setup 6). build.ps1 builds it after PyInstaller:
;   ISCC /DVersion=1.2.3 /DRepository=owner/repo installer.iss  ->  dist\CopyOver-Setup-1.2.3.exe
; Per-user install with no admin rights, in %LOCALAPPDATA%\Programs\CopyOver. Settings and history live next
; to CopyOver.exe (see bundle.data_folder), so updates install over the same folder and keep them.

#ifndef Version
  #error Pass the version: ISCC /DVersion=x.y.z installer.iss
#endif
#ifndef Repository
  #define Repository "aritabryan/copyover"
#endif
; Size of dist\CopyOver in MB (build.ps1 measures it), for «45 of 120 MB» under the progress bar.
#ifndef SizeMB
  #define SizeMB "0"
#endif

[Setup]
; Never change the AppId: Windows uses it to recognize an update of the same app.
AppId={{5CCE71F4-BA1A-4FFB-BB41-4A337AFFC666}
AppName=CopyOver
AppVersion={#Version}
AppVerName=CopyOver {#Version}
AppPublisher=CopyOver
AppPublisherURL=https://github.com/{#Repository}
AppSupportURL=https://github.com/{#Repository}/issues
AppUpdatesURL=https://github.com/{#Repository}/releases/latest
VersionInfoVersion={#Version}
VersionInfoProductName=CopyOver
VersionInfoDescription=CopyOver {#Version}
DefaultDirName={localappdata}\Programs\CopyOver
PrivilegesRequired=lowest
DisableDirPage=yes
DisableProgramGroupPage=yes
; Without the ready page, the tasks page button already says «Install»: one click and done.
DisableReadyPage=yes
UsePreviousTasks=yes
OutputDir=dist
OutputBaseFilename=CopyOver-Setup-{#Version}
SetupIconFile=puente.ico
UninstallDisplayIcon={app}\CopyOver.exe
UninstallDisplayName=CopyOver
; Follows the Windows light or dark theme, like the CopyOver panel.
WizardStyle=modern dynamic windows11 hidebevels
; Fixed backgrounds so the logo's square blends in: white in light mode, dark gray in dark mode.
WizardBackColor=#ffffff
WizardBackColorDynamicDark=#2b2b2b
WizardImageFile=build\installer\side.bmp,build\installer\side@2x.bmp
WizardImageFileDynamicDark=build\installer\side-dark.bmp,build\installer\side-dark@2x.bmp
WizardSmallImageFile=build\installer\logo.bmp,build\installer\logo@2x.bmp
WizardSmallImageFileDynamicDark=build\installer\logo-dark.bmp,build\installer\logo-dark@2x.bmp
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Always asks English or Spanish first, with the Windows language already chosen.
ShowLanguageDialog=yes
ChangesAssociations=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[CustomMessages]
en.TaskStartup=Start with Windows and add «Send with CopyOver» to the right-click menu, to send files to another PC
es.TaskStartup=Iniciar con Windows y agregar «Enviar con CopyOver» al clic derecho, para mandar archivos a otra PC
en.OpenCopyOver=Open CopyOver
es.OpenCopyOver=Abrir CopyOver
en.TaskDesktop=Create a shortcut on the desktop
es.TaskDesktop=Crear un acceso directo en el escritorio
en.StepFiles=Copying CopyOver and its libraries
es.StepFiles=Copiando CopyOver y sus librerías
en.CopyDetail=%1 of %2 MB   ·   %3 MB left
es.CopyDetail=%1 de %2 MB   ·   faltan %3 MB
en.RateDetail=%1   ·   %2 MB/s   ·   about %3 left
es.RateDetail=%1   ·   %2 MB/s   ·   quedan unos %3
en.CopyDone=%1 MB copied in %2
es.CopyDone=%1 MB copiados en %2
en.StepShortcuts=Shortcuts in the Start menu and on the desktop
es.StepShortcuts=Accesos directos en el menú Inicio y el escritorio
en.StepStartup=Start with Windows and right-click menu
es.StepStartup=Inicio con Windows y menú del clic derecho

[Tasks]
Name: "inicio"; Description: "{cm:TaskStartup}"
Name: "escritorio"; Description: "{cm:TaskDesktop}"

[InstallDelete]
; Libraries from the previous version: only the package folder, never the settings next to the exe.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\CopyOver\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Same name as the shortcut CopyOver creates itself (windows_system.START_MENU_NAME): no duplicates.
Name: "{autoprograms}\CopyOver"; Filename: "{app}\CopyOver.exe"; WorkingDir: "{app}"
; Not on the quiet update CopyOver starts itself: it would bring back an icon the user deleted.
Name: "{autodesktop}\CopyOver"; Filename: "{app}\CopyOver.exe"; WorkingDir: "{app}"; Tasks: escritorio; Check: not WizardSilent

[Registry]
; File notification actions must work even when the optional startup task is disabled.
Root: HKCU; Subkey: "Software\Classes\copyover-notice"; ValueType: string; ValueName: ""; ValueData: "URL:CopyOver"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\copyover-notice"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\copyover-notice\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\CopyOver.exe"" --notification ""%1"""

[Run]
; Autostart, right-click menu, «Send to» and, only if the network needs it, the firewall rule (asks for UAC).
Filename: "{app}\CopyOver.exe"; Parameters: "--install"; WorkingDir: "{app}"; Tasks: inicio; Flags: runhidden waituntilterminated; BeforeInstall: BeginStartup; AfterInstall: EndStartup
Filename: "{app}\CopyOver.exe"; WorkingDir: "{app}"; Description: "{cm:OpenCopyOver}"; Flags: nowait postinstall skipifsilent
; The update CopyOver starts itself (updates.INSTALLER_ARGUMENTS) is silent: it opens again on its own.
Filename: "{app}\CopyOver.exe"; WorkingDir: "{app}"; Flags: nowait; Check: WizardSilent

[UninstallDelete]
; Uninstalling leaves nothing behind: config, history, log, keys, certificates and the panel's WebView data
; live next to CopyOver.exe, and the browser profile of the panel in LocalAppData. The files received from
; other devices stay in Downloads: they are the user's.
Type: filesandordirs; Name: "{app}"
Type: filesandordirs; Name: "{localappdata}\PuentePortapapeles"

[UninstallRun]
Filename: "{app}\CopyOver.exe"; Parameters: "--uninstall"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveShortcuts"

[Code]
// CopyOver lives in the tray and its windows (panel, quick view) are the same exe: all of them are closed so
// the files can be replaced. Only by name and without /T: when CopyOver starts its own update, this installer
// is its child and the process tree would take it down too.
procedure CloseCopyOver;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM CopyOver.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

// The steps under the progress bar, like the Linux installer: each one with its mark (pending, running,
// done or skipped), and the file copy with its percentage.
const
  STEP_FILES = 0;
  STEP_SHORTCUTS = 1;
  STEP_STARTUP = 2;
  PENDING = 0;
  RUNNING = 1;
  DONE = 2;
  SKIPPED = 3;

// Milliseconds since Windows started, to measure the copy speed.
function GetTickCount: Cardinal;
  external 'GetTickCount@kernel32.dll stdcall';

var
  Marks, Steps: array of TNewStaticText;
  Names: array of String;
  States: array of Integer;
  Detail: TNewStaticText;
  CopyStart: Cardinal;

procedure MarkStep(Step, State: Integer);
begin
  States[Step] := State;
  case State of
    PENDING: Marks[Step].Caption := #$25CB;
    RUNNING: Marks[Step].Caption := #$25CF;
    DONE: Marks[Step].Caption := #$2714;
    SKIPPED: Marks[Step].Caption := #$2013;
  end;
  Steps[Step].Caption := Names[Step];
  Steps[Step].Enabled := State <> PENDING;
  Marks[Step].Enabled := State <> PENDING;
end;

procedure InitializeWizard;
var
  I, TopY: Integer;
begin
  SetArrayLength(Marks, 3);
  SetArrayLength(Steps, 3);
  SetArrayLength(Names, 3);
  SetArrayLength(States, 3);
  Names[STEP_FILES] := CustomMessage('StepFiles');
  Names[STEP_SHORTCUTS] := CustomMessage('StepShortcuts');
  Names[STEP_STARTUP] := CustomMessage('StepStartup');
  TopY := WizardForm.ProgressGauge.Top + WizardForm.ProgressGauge.Height + ScaleY(22);
  for I := 0 to 2 do
  begin
    Marks[I] := TNewStaticText.Create(WizardForm);
    Marks[I].Parent := WizardForm.InstallingPage;
    Marks[I].Font.Name := 'Segoe UI Symbol';
    Marks[I].Left := WizardForm.ProgressGauge.Left;
    Marks[I].Top := TopY + I * ScaleY(26);
    Steps[I] := TNewStaticText.Create(WizardForm);
    Steps[I].Parent := WizardForm.InstallingPage;
    Steps[I].Left := Marks[I].Left + ScaleX(24);
    Steps[I].Top := Marks[I].Top;
    MarkStep(I, PENDING);
  end;
  // MB copied and left, speed and time left, under the steps.
  Detail := TNewStaticText.Create(WizardForm);
  Detail.Parent := WizardForm.InstallingPage;
  Detail.Left := Steps[0].Left;
  Detail.Top := TopY + 3 * ScaleY(26) + ScaleY(8);
  Detail.Caption := '';
end;

function Duration(Seconds: Integer): String;
begin
  if Seconds < 60 then
    Result := IntToStr(Seconds) + ' s'
  else
    Result := IntToStr(Seconds div 60) + ' min ' + IntToStr(Seconds mod 60) + ' s';
end;

procedure CopyFinished;
var
  Seconds: Integer;
begin
  if ({#SizeMB} > 0) and (Detail.Caption <> '') then
  begin
    Seconds := (GetTickCount - CopyStart) div 1000;
    if Seconds < 1 then Seconds := 1;
    Detail.Caption := FmtMessage(CustomMessage('CopyDone'), [IntToStr({#SizeMB}), Duration(Seconds)]);
  end;
end;

procedure CurInstallProgressChanged(CurProgress, MaxProgress: Integer);
var
  Total, Copied, Remaining: Integer;
  Share, Seconds, Speed: Extended;
  Text: String;
begin
  if (MaxProgress <= 0) or (States[STEP_FILES] <> RUNNING) then
    exit;
  if CurProgress >= MaxProgress then
  begin
    MarkStep(STEP_FILES, DONE);
    CopyFinished;
    MarkStep(STEP_SHORTCUTS, RUNNING);
    exit;
  end;
  // In Pascal Script «/» between integers drops the decimals, so the share is computed as Extended.
  Share := CurProgress;
  Share := Share / MaxProgress;
  Steps[STEP_FILES].Caption := Names[STEP_FILES] + '   ' + IntToStr(Round(Share * 100)) + ' %';
  Total := {#SizeMB};
  if Total <= 0 then
    exit;
  Copied := Round(Total * Share);
  Remaining := Total - Copied;
  Text := FmtMessage(CustomMessage('CopyDetail'), [IntToStr(Copied), IntToStr(Total), IntToStr(Remaining)]);
  Seconds := GetTickCount - CopyStart;
  Seconds := Seconds / 1000;
  // Speed and time left only after a second, when they no longer jump around.
  if (Seconds >= 1) and (Share > 0) then
  begin
    Speed := Total * Share / Seconds;
    Text := FmtMessage(CustomMessage('RateDetail'), [Text, Format('%.1f', [Speed]),
      Duration(Round(Remaining / Speed))]);
  end;
  Detail.Caption := Text;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    CopyStart := GetTickCount;
    MarkStep(STEP_FILES, RUNNING);
  end
  else if CurStep = ssPostInstall then
  begin
    // Files and shortcuts were done before [Run]; this only closes what is still open.
    if States[STEP_FILES] <> DONE then
    begin
      MarkStep(STEP_FILES, DONE);
      CopyFinished;
    end;
    if States[STEP_SHORTCUTS] <> DONE then MarkStep(STEP_SHORTCUTS, DONE);
    if States[STEP_STARTUP] = PENDING then MarkStep(STEP_STARTUP, SKIPPED);
  end;
end;

procedure BeginStartup;
begin
  if States[STEP_FILES] <> DONE then
  begin
    MarkStep(STEP_FILES, DONE);
    CopyFinished;
  end;
  MarkStep(STEP_SHORTCUTS, DONE);
  MarkStep(STEP_STARTUP, RUNNING);
end;

procedure EndStartup;
begin
  MarkStep(STEP_STARTUP, DONE);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CloseCopyOver;
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  CloseCopyOver;
  Result := True;
end;
