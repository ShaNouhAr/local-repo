@echo off
rem local-repo (serveur Windows) : administration, dans une console administrateur.
rem   local-repo user list / add NOM [admin ou viewer] / passwd NOM / enable NOM / disable NOM
rem   local-repo backup create [DOSSIER] / list / restore ARCHIVE
rem   local-repo status / restart / fingerprint
setlocal
set "LR_CONFIG=%ProgramData%\local-repo\etc\local-repo.json"
set "PY=%~dp0python\python.exe"
if /i "%~1"=="status" goto status
if /i "%~1"=="restart" goto restart
if /i "%~1 %~2"=="backup restore" goto restore
"%PY%" -m lrwin %*
exit /b %ERRORLEVEL%
:status
sc.exe query local-repo
exit /b %ERRORLEVEL%
:restart
powershell.exe -NoProfile -Command "$ErrorActionPreference='Stop'; Restart-Service local-repo; (Get-Service local-repo).WaitForStatus('Running','00:01:00')"
exit /b %ERRORLEVEL%
:restore
powershell.exe -NoProfile -Command "$ErrorActionPreference='Stop'; Stop-Service local-repo; (Get-Service local-repo).WaitForStatus('Stopped','00:01:00')"
if errorlevel 1 exit /b 1
"%PY%" -m lrwin %*
set RC=%ERRORLEVEL%
net start local-repo
if errorlevel 1 exit /b 1
exit /b %RC%
