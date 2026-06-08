@echo off
REM Google Workspace MCP startup
REM - Reads credentials from MCP_REPO/.env (loaded by fastmcp_server.py)
REM - Tokens stored in ~/.google_workspace_mcp/credentials/

set "HERMES_HOME=%LOCALAPPDATA%\hermes"
set "MCP_REPO=%HERMES_HOME%\google_workspace_mcp"

REM Kill existing server
taskkill /F /IM pythonw.exe /FI "WINDOWTITLE eq workspace-mcp-server" 2>nul
taskkill /F /IM python.exe /FI "WINDOWTITLE eq workspace-mcp-server" 2>nul

REM Start MCP server (fastmcp_server.py loads .env via load_dotenv())
start "workspace-mcp-server" /b pythonw "%MCP_REPO%\start_wsmcp.py"
exit
