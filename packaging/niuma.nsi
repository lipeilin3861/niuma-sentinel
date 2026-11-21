Unicode true
RequestExecutionLevel user
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "WinVer.nsh"
!include "x64.nsh"

!define APP_EXE "NiuMaSentinel.exe"
!define APP_DIR "NiuMaSentinel"
!define PRODUCT_MARKER ".niuma-sentinel-install"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\PlayAtelier.NiuMaSentinel"

Name "${PRODUCT_NAME} v${APP_VERSION}"
OutFile "${SETUP_OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\Play Atelier\${APP_DIR}"
InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"
ShowInstDetails show
ShowUninstDetails show
BrandingText "PLAY atelier  ·  派徕设计"

!define MUI_ICON "${PRODUCT_ICON}"
!define MUI_UNICON "${PRODUCT_ICON}"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TEXT "安装 ${PRODUCT_NAME} v${APP_VERSION}。安装程序已包含运行所需的程序和模型。完成后可从开始菜单打开应用。"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "打开${PRODUCT_NAME}"
!define MUI_FINISHPAGE_RUN_NOTCHECKED

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey /LANG=2052 "ProductName" "${PRODUCT_NAME}"
VIAddVersionKey /LANG=2052 "CompanyName" "${COMPANY}"
VIAddVersionKey /LANG=2052 "FileDescription" "${PRODUCT_NAME} Windows 安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=2052 "ProductVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "${COMPANY}"

!macro REFUSE_IF_RUNNING
  System::Call 'kernel32::OpenMutexW(i 0x00100000, i 0, w "Local\TingYiXiaCameraApp") p.r0'
  ${If} $0 != 0
    System::Call 'kernel32::CloseHandle(p r0)'
    SetErrorLevel 5
    IfSilent +2
      MessageBox MB_OK|MB_ICONINFORMATION "请从托盘菜单退出${PRODUCT_NAME}，然后重新运行程序。"
    Abort
  ${EndIf}
!macroend

Function .onInit
  SetShellVarContext current
  ${IfNot} ${RunningX64}
  ${OrIfNot} ${AtLeastWin10}
    SetErrorLevel 2
    IfSilent +2
      MessageBox MB_OK|MB_ICONSTOP "${PRODUCT_NAME}需要 Windows 10 或 Windows 11（64 位 x64）。"
    Abort
  ${EndIf}
  !insertmacro REFUSE_IF_RUNNING
FunctionEnd

Function .onVerifyInstDir
  IfFileExists "$INSTDIR\${PRODUCT_MARKER}" safe
  ClearErrors
  FindFirst $0 $1 "$INSTDIR\*"
  IfErrors safe
  loop:
    StrCmp $1 "" empty
    StrCmp $1 "." next
    StrCmp $1 ".." next
    FindClose $0
    Abort
  next:
    FindNext $0 $1
    IfErrors empty loop
  empty:
    FindClose $0
  safe:
FunctionEnd

Section "${PRODUCT_NAME}"
  Call .onVerifyInstDir
  SetOutPath "$INSTDIR"
  File /r "${PAYLOAD_DIR}\*"
  FileOpen $0 "$INSTDIR\${PRODUCT_MARKER}" w
  FileWrite $0 "PlayAtelier.NiuMaSentinel"
  FileClose $0
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\${PRODUCT_NAME}"
  CreateShortcut "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortcut "$SMPROGRAMS\${PRODUCT_NAME}\卸载${PRODUCT_NAME}.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "${COMPANY}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "URLInfoAbout" "${WEBSITE_URL}"
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

Function un.onInit
  SetShellVarContext current
  !insertmacro REFUSE_IF_RUNNING
  IfFileExists "$INSTDIR\${PRODUCT_MARKER}" owned
  SetErrorLevel 2
  Abort
  owned:
FunctionEnd

Section "Uninstall"
  Delete "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk"
  Delete "$SMPROGRAMS\${PRODUCT_NAME}\卸载${PRODUCT_NAME}.lnk"
  RMDir "$SMPROGRAMS\${PRODUCT_NAME}"
  Delete "$INSTDIR\${APP_EXE}"
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\${PRODUCT_MARKER}"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
SectionEnd
