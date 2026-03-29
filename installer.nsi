; LCO Windows Installer — NSIS script
; Build: makensis installer.nsi
; Requires: NSIS 3.x from https://nsis.sourceforge.io

!define APPNAME     "LCO"
!define APPVERSION  "0.2.5"
!define APPFULLNAME "LCO, LLM Context Optimizer"
!define PUBLISHER   "LCO Project"
!define APPEXE      "LCO.exe"
!define INSTALLDIR  "$PROGRAMFILES64\LCO"
!define UNINSTKEY   "Software\Microsoft\Windows\CurrentVersion\Uninstall\LCO"

Name               "${APPFULLNAME} ${APPVERSION}"
OutFile            "LCO-Setup-${APPVERSION}.exe"
InstallDir         "${INSTALLDIR}"
InstallDirRegKey   HKLM "${UNINSTKEY}" "InstallLocation"
RequestExecutionLevel admin
SetCompressor      /SOLID lzma

; Uncomment after creating assets\lco.ico:
!define MUI_ICON    "assets\lco.ico"
!define MUI_UNICON  "assets\lco.ico"

!include "MUI2.nsh"
!define MUI_WELCOMEPAGE_TEXT "LCO reduces your LLM costs by compressing API calls.$\n$\nClick Next to install."
!define MUI_FINISHPAGE_RUN       "$INSTDIR\${APPEXE}"
!define MUI_FINISHPAGE_RUN_TEXT  "Launch LCO now"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "LCO (required)" SecMain
    SectionIn RO
    SetOutPath "$INSTDIR"
    File "dist\${APPEXE}"
    WriteUninstaller "$INSTDIR\Uninstall.exe"
    WriteRegStr   HKLM "${UNINSTKEY}" "DisplayName"     "${APPFULLNAME}"
    WriteRegStr   HKLM "${UNINSTKEY}" "DisplayVersion"   "${APPVERSION}"
    WriteRegStr   HKLM "${UNINSTKEY}" "Publisher"        "${PUBLISHER}"
    WriteRegStr   HKLM "${UNINSTKEY}" "InstallLocation"  "$INSTDIR"
    WriteRegStr   HKLM "${UNINSTKEY}" "UninstallString"  "$INSTDIR\Uninstall.exe"
    WriteRegStr   HKLM "${UNINSTKEY}" "DisplayIcon"      "$INSTDIR\${APPEXE}"
    WriteRegDWORD HKLM "${UNINSTKEY}" "NoModify"         1
    WriteRegDWORD HKLM "${UNINSTKEY}" "NoRepair"         1
    CreateDirectory "$SMPROGRAMS\LCO"
    CreateShortcut  "$SMPROGRAMS\LCO\LCO.lnk" "$INSTDIR\${APPEXE}"
    CreateShortcut  "$SMPROGRAMS\LCO\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
SectionEnd

Section /o "Desktop shortcut" SecDesktop
    CreateShortcut "$DESKTOP\LCO.lnk" "$INSTDIR\${APPEXE}"
SectionEnd

Section "Uninstall"
    ExecWait 'taskkill /F /IM "${APPEXE}"'
    Delete "$INSTDIR\${APPEXE}"
    Delete "$INSTDIR\Uninstall.exe"
    RMDir  "$INSTDIR"
    Delete "$SMPROGRAMS\LCO\LCO.lnk"
    Delete "$SMPROGRAMS\LCO\Uninstall.lnk"
    RMDir  "$SMPROGRAMS\LCO"
    Delete "$DESKTOP\LCO.lnk"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "LCO"
    DeleteRegKey HKLM "${UNINSTKEY}"
    ; User data in %APPDATA%\LCO\ is intentionally kept across reinstalls
SectionEnd