@echo off
setlocal
set "DASHBOARD_NODE=node"
where node >nul 2>&1
if errorlevel 1 (
  set "DASHBOARD_NODE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
)
"%DASHBOARD_NODE%" "%~dp0run.mjs" %*
exit /b %errorlevel%
