@echo off
REM Google Workspace MCP startup
REM Reads credentials from %HERMES_HOME%\.env

set "HERMES_HOME=%LOCALAPPDATA%\hermes"
set "MCP_REPO=%HERMES_HOME%\google_workspace_mcp"

REM Load .env vars
for /f "usebackq tokens=1,* delims==" %%a in ("%HERMES_HOME%\.env") do (
    echo %%a | findstr /b "GOOGLE_OAUTH" >nul && set "%%a=%%b"
)

REM Kill existing server
taskkill /F /IM pythonw.exe /FI "WINDOWTITLE eq workspace-mcp-server" 2>nul
taskkill /F /IM python.exe /FI "WINDOWTITLE eq workspace-mcp-server" 2>nul

REM Start MCP server
start "workspace-mcp-server" /b pythonw "%MCP_REPO%\start_wsmcp.py"
exit
