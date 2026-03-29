@echo off
setlocal ENABLEEXTENSIONS

if /I "%~1"=="--help" goto :usage
if /I "%~1"=="/?" goto :usage

echo.
echo Zenbot v3 context refresh
echo Run this when docs, schema, koan source, or search context changed.
echo.
python "%~dp0sync_openai_vector_store.py" %*
if errorlevel 1 (
  echo.
  echo Context refresh failed.
  echo If this is your first search-context store, try:
  echo   update_context.bat --create
  exit /b 1
)

echo.
echo Context refresh complete.
exit /b 0

:usage
echo Usage:
echo   update_context.bat [--create] [--vector-store-id VS_ID] [--name NAME] [--path RELATIVE_FILE]
echo.
echo Normal use:
echo   update_context.bat
echo.
echo First-time search-context creation:
echo   update_context.bat --create
echo.
exit /b 0
