; UpSpace installer (per-user, no admin rights needed)
Unicode true
!define APPNAME "UpSpace"
!ifndef APPVER
  !define APPVER "2.0"
!endif
Name "${APPNAME} Bookings & Billing"
OutFile "..\dist\UpSpace-Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\${APPNAME}"
RequestExecutionLevel user
SetCompressor /SOLID lzma
Icon "..\assets\upspace.ico"
UninstallIcon "..\assets\upspace.ico"

!include "MUI2.nsh"
!define MUI_ICON "..\assets\upspace.ico"
!define MUI_UNICON "..\assets\upspace.ico"
!define MUI_FINISHPAGE_RUN "$INSTDIR\UpSpace.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Open UpSpace now"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "Install"
  SetOutPath "$INSTDIR"
  ; remove old program files (data lives in %LOCALAPPDATA%\UpSpace\data and is kept)
  RMDir /r "$INSTDIR\_internal"
  File /r "..\dist\UpSpace\*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\UpSpace.lnk" "$INSTDIR\UpSpace.exe"
  CreateDirectory "$SMPROGRAMS\UpSpace"
  CreateShortcut "$SMPROGRAMS\UpSpace\UpSpace.lnk" "$INSTDIR\UpSpace.exe"
  CreateShortcut "$SMPROGRAMS\UpSpace\Uninstall UpSpace.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" "DisplayName" "UpSpace Bookings & Billing"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" "DisplayVersion" "${APPVER}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" "Publisher" "UpSpace"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" "DisplayIcon" "$INSTDIR\UpSpace.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" "UninstallString" "$INSTDIR\Uninstall.exe"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\UpSpace.lnk"
  RMDir /r "$SMPROGRAMS\UpSpace"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}"
  ; The database in %LOCALAPPDATA%\UpSpace\data is intentionally NOT deleted.
SectionEnd
