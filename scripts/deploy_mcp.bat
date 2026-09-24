@echo off
setlocal ENABLEEXTENSIONS
if "%~1"=="" goto :usage
if /I "%~1"=="--help" goto :usage
set "MCP_DEPLOY_MANIFEST=%~1"
set "MCP_PROJECT_ID=%~2"
if not defined MCP_PROJECT_ID set "MCP_PROJECT_ID=zenbot-434517"
call gcloud app deploy "%MCP_DEPLOY_MANIFEST%" --project "%MCP_PROJECT_ID%" --no-promote
exit /b %ERRORLEVEL%
:usage
echo Usage: scripts\deploy_mcp.bat mcp_app.local.yaml [PROJECT_ID]
echo Copy mcp_app.yaml to mcp_app.local.yaml and configure your allowed email first.
echo Deploys an unpromoted MCP version for smoke tests. See MCP_SETUP.md.
exit /b 1
