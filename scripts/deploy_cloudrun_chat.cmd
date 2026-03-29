@echo off
setlocal ENABLEEXTENSIONS
if /I "%~1"=="--help" goto :help
if /I "%~1"=="/?" goto :help
echo deploy_cloudrun_chat.cmd is a compatibility alias for deploy_cloudrun_service.cmd.
call "%~dp0deploy_cloudrun_service.cmd" %*
exit /b %ERRORLEVEL%

:help
echo Compatibility alias:
echo   deploy_cloudrun_chat.cmd [PROJECT_ID] [REGION] [SERVICE_NAME] [SERVICE_ACCOUNT]
echo.
echo This forwards to deploy_cloudrun_service.cmd, which is the real v3 deploy script.
echo.
call "%~dp0deploy_cloudrun_service.cmd" --help
exit /b 0
