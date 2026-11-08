Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "nsDialogs.nsh"
!include "FileFunc.nsh"

!define APP_NAME "大肥鱼"
!define APP_EXE "大肥鱼.exe"
!define APP_VERSION "1.0.0"
!define APP_PUBLISHER "Dafeiyu"
!define PAYLOAD "..\build\installer\payload"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Dafeiyu"
!define RUN_KEY "Software\Microsoft\Windows\CurrentVersion\Run"
!define WEBVIEW_KEY "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

Name "${APP_NAME}"
OutFile "..\dist\Dafeiyu-Setup-${APP_VERSION}.exe"
InstallDir "$LOCALAPPDATA\Dafeiyu"
InstallDirRegKey HKCU "Software\Dafeiyu" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64
SetOverwrite on
BrandingText "${APP_NAME} 安装程序"

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName" "${APP_NAME}"
VIAddVersionKey "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey "FileDescription" "${APP_NAME} 安装程序"
VIAddVersionKey "FileVersion" "${APP_VERSION}.0"
VIAddVersionKey "ProductVersion" "${APP_VERSION}"
VIAddVersionKey "LegalCopyright" "Dafeiyu"

!define MUI_ABORTWARNING
!define MUI_ICON "..\qqbot\ui\assets\dafeiyu.ico"
!define MUI_UNICON "..\qqbot\ui\assets\dafeiyu.ico"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "启动大肥鱼"

Var MasterQQ
Var ModelBase
Var ModelId
Var ApiKey
Var DesktopShortcut
Var StartWithWindows
Var Dialog
Var QQEdit
Var BaseEdit
Var IdEdit
Var KeyEdit
Var DesktopCheck
Var AutoStartCheck

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
Page custom ConfigPageCreate ConfigPageLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  ; WebView2 是界面运行前提；缺少时直接引导安装，避免装完打不开。
  ReadRegStr $0 HKLM "SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  StrCmp $0 "" 0 webview_ok
  ReadRegStr $0 HKLM "SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  StrCmp $0 "" 0 webview_ok
  ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  StrCmp $0 "" webview_missing webview_ok

webview_missing:
  MessageBox MB_ICONEXCLAMATION|MB_YESNO "没有检测到 Microsoft Edge WebView2 Runtime。$\r$\n$\r$\n大肥鱼需要它来显示界面。是否打开微软下载页？" IDNO webview_abort
  ExecShell "open" "https://developer.microsoft.com/zh-cn/microsoft-edge/webview2/"
webview_abort:
  Abort

webview_ok:
  ; 静默安装/批量部署可以传 /MASTERQQ=... /MODELBASE=... /MODELID=... /APIKEY=...
  ${GetParameters} $R0
  ${GetOptions} $R0 "/MASTERQQ=" $MasterQQ
  ${GetOptions} $R0 "/MODELBASE=" $ModelBase
  ${GetOptions} $R0 "/MODELID=" $ModelId
  ${GetOptions} $R0 "/APIKEY=" $ApiKey
FunctionEnd

Function ConfigPageCreate
  !insertmacro MUI_HEADER_TEXT "连接你的 QQ 和模型" "这些信息只保存在本机，安装完成后可以直接启动。"
  nsDialogs::Create 1018
  Pop $Dialog
  ${If} $Dialog == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0 100% 20u "主人 QQ（只有这个号能和她私聊）"
  Pop $0
  ${NSD_CreateText} 0 20u 100% 14u ""
  Pop $QQEdit

  ${NSD_CreateLabel} 0 42u 100% 20u "模型接口地址"
  Pop $0
  ${NSD_CreateText} 0 62u 100% 14u "https://api.deepseek.com"
  Pop $BaseEdit

  ${NSD_CreateLabel} 0 84u 100% 20u "模型名称"
  Pop $0
  ${NSD_CreateText} 0 104u 100% 14u "deepseek-flash"
  Pop $IdEdit

  ${NSD_CreateLabel} 0 126u 100% 20u "API Key（只写不读，保存在本机 .env）"
  Pop $0
  ${NSD_CreatePassword} 0 146u 100% 14u ""
  Pop $KeyEdit

  ${NSD_CreateCheckbox} 0 174u 100% 12u "创建桌面快捷方式"
  Pop $DesktopCheck
  ${NSD_Check} $DesktopCheck

  ${NSD_CreateCheckbox} 0 190u 100% 12u "开机时自动启动机器人"
  Pop $AutoStartCheck

  ${NSD_CreateLabel} 0 210u 100% 24u "QQ 登录需要 NapCat 扫码；安装完成后会打开使用说明。"
  Pop $0

  nsDialogs::Show
FunctionEnd

Function ConfigPageLeave
  ${NSD_GetText} $QQEdit $MasterQQ
  ${NSD_GetText} $BaseEdit $ModelBase
  ${NSD_GetText} $IdEdit $ModelId
  ${NSD_GetText} $KeyEdit $ApiKey
  ${NSD_GetState} $DesktopCheck $DesktopShortcut
  ${NSD_GetState} $AutoStartCheck $StartWithWindows

  ${If} $MasterQQ == ""
    MessageBox MB_ICONEXCLAMATION "请填写主人 QQ 号。"
    Abort
  ${EndIf}
  ${If} $ApiKey == ""
    MessageBox MB_ICONEXCLAMATION "请填写模型 API Key。"
    Abort
  ${EndIf}
  ${If} $ModelBase == ""
    StrCpy $ModelBase "https://api.deepseek.com"
  ${EndIf}
  ${If} $ModelId == ""
    StrCpy $ModelId "deepseek-flash"
  ${EndIf}
FunctionEnd

Section "安装大肥鱼" SEC_MAIN
  SetOutPath "$INSTDIR"
  File /r "${PAYLOAD}\*.*"

  ; 人格是用户内容：升级时不要用默认 prompt 覆盖已经编辑过的 system.md。
  IfFileExists "$INSTDIR\prompts\system.md" prompts_ready
  SetOutPath "$INSTDIR\prompts"
  File /r "${PAYLOAD}\defaults\prompts\*.*"
prompts_ready:
  SetOutPath "$INSTDIR"

  WriteUninstaller "$INSTDIR\Uninstall.exe"

  Call WriteConfig

  WriteRegStr HKCU "Software\Dafeiyu" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\Dafeiyu" "Version" "${APP_VERSION}"

  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1

  CreateDirectory "$SMPROGRAMS\大肥鱼"
  CreateShortCut "$SMPROGRAMS\大肥鱼\大肥鱼.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
  CreateShortCut "$SMPROGRAMS\大肥鱼\卸载大肥鱼.lnk" "$INSTDIR\Uninstall.exe"

  ${If} $DesktopShortcut == ${BST_CHECKED}
    CreateShortCut "$DESKTOP\大肥鱼.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
  ${EndIf}

  ${If} $StartWithWindows == ${BST_CHECKED}
    WriteRegStr HKCU "${RUN_KEY}" "Dafeiyu" '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$INSTDIR\scripts\supervise.ps1"'
  ${Else}
    DeleteRegValue HKCU "${RUN_KEY}" "Dafeiyu"
  ${EndIf}
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\大肥鱼.lnk"
  RMDir /r "$SMPROGRAMS\大肥鱼"
  DeleteRegValue HKCU "${RUN_KEY}" "Dafeiyu"
  DeleteRegKey HKCU "Software\Dafeiyu"
  DeleteRegKey HKCU "${UNINST_KEY}"

  ; Silent uninstall must not block on a prompt. Default to keeping user data;
  ; an interactive uninstall still asks.
  IfSilent keep_data
  MessageBox MB_ICONQUESTION|MB_YESNO "是否同时删除聊天记录、人格和配置？$\r$\n选择“否”会保留 data 目录和 .env，下次安装可以继续使用。" IDNO keep_data
  RMDir /r "$INSTDIR\data"
  RMDir /r "$INSTDIR\logs"
  Delete "$INSTDIR\.env"

keep_data:
  RMDir /r "$INSTDIR\qqbot"
  RMDir /r "$INSTDIR\prompts"
  RMDir /r "$INSTDIR\defaults"
  RMDir /r "$INSTDIR\scripts"
  RMDir /r "$INSTDIR\tools"
  RMDir /r "$INSTDIR\assets"
  RMDir /r "$INSTDIR\docs"
  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\Microsoft.Web.WebView2.Core.dll"
  Delete "$INSTDIR\Microsoft.Web.WebView2.WinForms.dll"
  Delete "$INSTDIR\WebView2Loader.dll"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\.env.example"
  Delete "$INSTDIR\setup.env"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd

Function WriteConfig
  IfFileExists "$INSTDIR\.env" config_exists
  IfFileExists "$INSTDIR\setup.env" 0 config_done
  FileOpen $0 "$INSTDIR\setup.env" r
  FileOpen $1 "$INSTDIR\.env" w
  ${Do}
    FileRead $0 $2
    ${If} ${Errors}
      ${ExitDo}
    ${EndIf}
    StrCpy $3 $2 14
    ${If} $3 == "ALLOW_USER_ID="
      ${If} $MasterQQ != ""
        FileWrite $1 "ALLOW_USER_ID=$MasterQQ$\r$\n"
      ${Else}
        FileWrite $1 $2
      ${EndIf}
    ${Else}
      StrCpy $3 $2 15
      ${If} $3 == "MODEL_BASE_URL="
        ${If} $ModelBase != ""
          FileWrite $1 "MODEL_BASE_URL=$ModelBase$\r$\n"
        ${Else}
          FileWrite $1 $2
        ${EndIf}
      ${Else}
        StrCpy $3 $2 9
        ${If} $3 == "MODEL_ID="
          ${If} $ModelId != ""
            FileWrite $1 "MODEL_ID=$ModelId$\r$\n"
          ${Else}
            FileWrite $1 $2
          ${EndIf}
        ${Else}
          StrCpy $3 $2 14
          ${If} $3 == "MODEL_API_KEY="
            ${If} $ApiKey != ""
              FileWrite $1 "MODEL_API_KEY=$ApiKey$\r$\n"
            ${Else}
              FileWrite $1 $2
            ${EndIf}
          ${Else}
            FileWrite $1 $2
          ${EndIf}
        ${EndIf}
      ${EndIf}
    ${EndIf}
  ${Loop}
  FileClose $0
  FileClose $1
  Delete "$INSTDIR\setup.env"
config_exists:
config_done:
FunctionEnd
