; Установщик Ven4Control.
;
; Зачем он вообще нужен: раздавать приложение одним портативным EXE удобно
; вручную, но каталог приложений Ven4Tools так работать не умеет — он скачивает
; файл и запускает его как установщик с тихими аргументами. Портативный EXE в
; этом сценарии просто открыл бы окно от администратора из %TEMP%, после чего
; временный файл был бы удалён, и никакой установки не произошло.
;
; Внешние зависимости не ставятся сознательно: PyInstaller укладывает внутрь
; onefile-сборки и весь рантайм Visual C++ (vcruntime140*.dll, msvcp140*.dll,
; ucrtbase.dll, api-ms-win-*), и все библиотеки Qt. Проверено по составу
; сборки — VC++ Redistributable приложению не требуется, и его установка была
; бы лишней качкой и лишней элевацией.
;
; Установка идёт в профиль пользователя, без UAC (RequestExecutionLevel user) —
; тот же подход, что у установщика лаунчера Ven4Tools.
;
; ВАЖНО про каталоги: данные приложения (devices.db, ключ, резервные копии,
; журналы) живут в %LOCALAPPDATA%\Ven4Control — см. src/ven4control/paths.py.
; Поэтому программа ставится в ПОДПАПКУ App, а деинсталлятор трогает только её:
; иначе удаление приложения уносило бы вместе с собой базу устройств.

Unicode true

; Версия приходит из единственной константы проекта: version.nsh генерируется
; tools/gen_version_info.py из src/ven4control/_version.py. Руками её здесь
; больше не задают — разошедшийся номер приводил бы к вечному предложению
; одного и того же обновления.
!include "version.nsh"
!ifndef SOURCE_EXE
  !define SOURCE_EXE "..\dist\Ven4Control.exe"
!endif
!ifndef OUTFILE
  !define OUTFILE "..\dist\Ven4Control.Setup-${VERSION}.exe"
!endif

!define APP_NAME    "Ven4Control"
!define EXE_NAME    "Ven4Control.exe"
!define PUBLISHER   "Ven4ru"
!define REPO_URL    "https://github.com/Ven4ru/Ven4Control"
!define UNINST_KEY  "Software\Microsoft\Windows\CurrentVersion\Uninstall\Ven4Control"
!define DATA_DIR    "$LOCALAPPDATA\Ven4Control"
!define SM_DIR      "$SMPROGRAMS\Ven4Control"

Name "${APP_NAME} ${VERSION}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Ven4Control\App"
InstallDirRegKey HKCU "Software\Ven4Control" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show
BrandingText "Ven4Control — ${REPO_URL}"

VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=1049 "ProductName"     "${APP_NAME}"
VIAddVersionKey /LANG=1049 "ProductVersion"  "${VERSION}"
VIAddVersionKey /LANG=1049 "FileVersion"     "${VERSION}.0"
VIAddVersionKey /LANG=1049 "FileDescription" "Установщик ${APP_NAME}"
VIAddVersionKey /LANG=1049 "CompanyName"     "${PUBLISHER}"
VIAddVersionKey /LANG=1049 "LegalCopyright"  "${PUBLISHER}"

!include "MUI2.nsh"
!include "FileFunc.nsh"

!define MUI_ABORTWARNING
!define MUI_ICON "..\assets\ven4control.ico"
!define MUI_UNICON "..\assets\ven4control.ico"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXE_NAME}"
!define MUI_FINISHPAGE_RUN_TEXT "Запустить ${APP_NAME}"

!insertmacro MUI_PAGE_LICENSE "..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "Russian"


Var WaitPid
Var Relaunch

; Самообновление: приложение запускает установщик и сразу выходит, поэтому
; установщик обязан дождаться исчезновения процесса — пока exe занят, заменить
; его нельзя, — и поднять приложение обратно после замены. При обычной
; установке приложение поднимает финишная страница, но в тихом режиме её нет.
Function .onInit
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/WAITPID=" $WaitPid
  IfErrors 0 +2
    StrCpy $WaitPid ""
  ClearErrors
  ${GetOptions} $R0 "/RELAUNCH" $R1
  IfErrors +2
    StrCpy $Relaunch "1"
FunctionEnd

Function WaitForCaller
  StrCmp $WaitPid "" done
  ; SYNCHRONIZE = 0x00100000
  System::Call 'kernel32::OpenProcess(i 0x00100000, i 0, i $WaitPid) i .r9'
  IntCmp $9 0 done
  System::Call 'kernel32::WaitForSingleObject(i r9, i 60000)'
  System::Call 'kernel32::CloseHandle(i r9)'
  done:
FunctionEnd


; Закрывает работающее приложение: пока его EXE открыт, файл заменить нельзя.
; Сначала вежливо, и только потом принудительно — у Ven4Control могут быть
; живые фоновые сессии логирования, которым лучше завершиться самим.
Function CloseRunningApp
  nsExec::Exec 'taskkill /im "${EXE_NAME}"'
  Pop $0
  Sleep 1500
  nsExec::Exec 'taskkill /f /im "${EXE_NAME}"'
  Pop $0
  Sleep 500
FunctionEnd

Function un.CloseRunningApp
  nsExec::Exec 'taskkill /im "${EXE_NAME}"'
  Pop $0
  Sleep 1500
  nsExec::Exec 'taskkill /f /im "${EXE_NAME}"'
  Pop $0
  Sleep 500
FunctionEnd


Section "Ven4Control" SecMain
  SectionIn RO

  Call WaitForCaller
  Call CloseRunningApp

  SetOutPath "$INSTDIR"
  SetOverwrite on

  ClearErrors
  File "${SOURCE_EXE}"
  IfErrors 0 +3
    DetailPrint "Не удалось записать ${EXE_NAME} — файл занят другим процессом."
    Abort "Не удалось установить ${APP_NAME}: исполняемый файл занят."

  File "..\THIRD-PARTY-NOTICES.md"
  File "..\LICENSE"

  WriteRegStr HKCU "Software\Ven4Control" "InstallDir" "$INSTDIR"

  ; Ярлыки. В тихом режиме тоже создаются: установка из каталога приложений
  ; иначе оставила бы программу без единой точки запуска.
  CreateDirectory "${SM_DIR}"
  CreateShortCut "${SM_DIR}\${APP_NAME}.lnk" "$INSTDIR\${EXE_NAME}" "" "$INSTDIR\${EXE_NAME}" 0
  CreateShortCut "${SM_DIR}\Удалить ${APP_NAME}.lnk" "$INSTDIR\uninstall.exe"
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${EXE_NAME}" "" "$INSTDIR\${EXE_NAME}" 0

  WriteUninstaller "$INSTDIR\uninstall.exe"

  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0

  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayName"          "${APP_NAME}"
  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayVersion"       "${VERSION}"
  WriteRegStr   HKCU "${UNINST_KEY}" "Publisher"            "${PUBLISHER}"
  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayIcon"          '"$INSTDIR\${EXE_NAME}"'
  WriteRegStr   HKCU "${UNINST_KEY}" "InstallLocation"      "$INSTDIR"
  WriteRegStr   HKCU "${UNINST_KEY}" "UninstallString"      '"$INSTDIR\uninstall.exe"'
  WriteRegStr   HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\uninstall.exe" /S'
  WriteRegStr   HKCU "${UNINST_KEY}" "URLInfoAbout"         "${REPO_URL}"
  WriteRegStr   HKCU "${UNINST_KEY}" "HelpLink"             "${REPO_URL}/issues"
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize"        "$0"
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify"             1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair"             1

  ; Перезапуск только при самообновлении: при обычной установке приложение
  ; поднимает финишная страница, а в тихом режиме её нет.
  StrCmp $Relaunch "1" 0 +2
    Exec '"$INSTDIR\${EXE_NAME}"'
SectionEnd


Section "Uninstall"
  Call un.CloseRunningApp

  Delete "$INSTDIR\${EXE_NAME}"
  Delete "$INSTDIR\THIRD-PARTY-NOTICES.md"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"

  Delete "${SM_DIR}\${APP_NAME}.lnk"
  Delete "${SM_DIR}\Удалить ${APP_NAME}.lnk"
  RMDir "${SM_DIR}"
  Delete "$DESKTOP\${APP_NAME}.lnk"

  ; Автозапуск приложение пишет само (HKCU\...\Run), поэтому убирается здесь же.
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Ven4Control"

  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\Ven4Control"

  ; Данные пользователя (devices.db, ключ приложения, резервные копии, журналы)
  ; остаются в ${DATA_DIR} сознательно: удаление программы не должно уносить
  ; список устройств и собранные журналы. Инструкция по полной очистке — в
  ; README, раздел «Удаление».
  DetailPrint "Данные оставлены в ${DATA_DIR} — удалите папку вручную, если они больше не нужны."
SectionEnd
