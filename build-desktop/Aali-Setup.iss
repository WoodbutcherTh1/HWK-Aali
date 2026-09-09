; Inno Setup script for آلي Desktop
; Build:  ISCC.exe build-desktop\Aali-Setup.iss
; Output: build-desktop\installer\Aali-Desktop-Setup.exe

#define MyAppName "Aali Desktop"
#define MyAppNameAr "آلي — Desktop"
#define MyAppVersion "1.0.2"
#define MyAppExeName "Aali-Desktop.exe"

[Setup]
AppId={{8E1B4C2A-77D3-4A5E-9B0F-AALI00000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppNameAr} {#MyAppVersion}
DefaultDirName={autopf}\AaliDesktop
DefaultGroupName=آلي
UninstallDisplayName=آلي — Desktop
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=installer
OutputBaseFilename=Aali-Desktop-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
SetupIconFile=icon.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\Aali-Desktop.exe"; DestDir: "{app}"; Flags: ignoreversion
; Terminal client (Claude Code-style CLI) — same app, chat from any console.
Source: "dist\aali-cli.exe"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
; Bundled tunnel binary — powers the in-app «شارك آلي» one-click public link.
Source: "cloudflared.exe"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\آلي — Desktop"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\آلي — Terminal"; Filename: "{app}\aali-cli.exe"; Parameters: "--base http://127.0.0.1:5055"; IconFilename: "{app}\aali-cli.exe"
Name: "{group}\Uninstall آلي Desktop"; Filename: "{uninstallexe}"
Name: "{userdesktop}\آلي — Desktop"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "أنشئ اختصاراً على سطح المكتب"; GroupDescription: "اختصارات:"; Flags: checkedonce

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "شغّل آلي الآن"; Flags: nowait postinstall skipifsilent

[Messages]
WelcomeLabel2=هذا سيثبّت آلي — Desktop على جهازك.%n%nآلي: عقل يعمل على جهازك — محادثة، ملفات، وسائط، وذاكرة دائمة.%n%nبعد التثبيت يمكنك مشاركة آلي مع الأصدقاء عبر زر «شارك آلي» داخل التطبيق.
