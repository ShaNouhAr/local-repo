@echo off
rem loco (serveur Windows) : administration, dans une console administrateur.
rem   loco user list / add NOM [admin ou viewer] / passwd NOM / enable NOM / disable NOM
rem   loco backup create [DOSSIER] / list / restore ARCHIVE
rem   loco status / restart / fingerprint
setlocal
set "LOCO_CONFIG=%ProgramData%\loco\etc\loco.json"
set "PY=%~dp0python\python.exe"
if /i "%~1"=="status" goto status
if /i "%~1"=="restart" goto restart
if /i "%~1 %~2"=="backup restore" goto restore
"%PY%" -m locowin %*
exit /b %ERRORLEVEL%
:status
sc.exe query loco
exit /b %ERRORLEVEL%
:restart
powershell.exe -NoProfile -Command "$ErrorActionPreference='Stop'; Restart-Service loco; (Get-Service loco).WaitForStatus('Running','00:01:00')"
exit /b %ERRORLEVEL%
:restore
powershell.exe -NoProfile -Command "$ErrorActionPreference='Stop'; Stop-Service loco; (Get-Service loco).WaitForStatus('Stopped','00:01:00')"
if errorlevel 1 exit /b 1
"%PY%" -m locowin %*
set RC=%ERRORLEVEL%
net start loco
if errorlevel 1 exit /b 1
exit /b %RC%
