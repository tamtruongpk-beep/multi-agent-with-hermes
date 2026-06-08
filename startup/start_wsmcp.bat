@echo off
REM Hannie - MCP Workspace Production v2 (Optimized for Windows Startup)
set WORKDIR=C:\Users\thanhtam\google_workspace_mcp
cd /d %WORKDIR%

REM Config credentials
set GOOGLE_OAUTH_CLIENT_ID=YOUR_GOOGLE_CLIENT_ID
set GOOGLE_OAUTH_CLIENT_SECRET=YOUR_GOOGLE_CLIENT_SECRET
set GOOGLE_OAUTH_REDIRECT_URI=http://localhost:8000/oauth2callback

REM Server Port & Host
set WORKSPACE_MCP_PORT=8000
set WORKSPACE_MCP_HOST=127.0.0.1
set OAUTHLIB_INSECURE_TRANSPORT=1
set MCP_ENABLE_OAUTH21=1

REM Kill existing server before starting new one to avoid port conflict
taskkill /F /IM pythonw.exe /FI "WINDOWTITLE eq workspace-mcp-server" 2>nul
taskkill /F /IM python.exe /FI "WINDOWTITLE eq workspace-mcp-server" 2>nul

REM Run in background using pythonw (no console window)
start "workspace-mcp-server" /b pythonw start_wsmcp.py --port 8000 --transport streamable-http

exit
