@echo off
REM Multi-Agent Hermes Setup - Windows
REM Run as normal user (no admin required)

echo === Multi-Agent Hermes Setup ===
echo.

REM ===1. Hermes Agent ===
echo [1/5] Installing Hermes Agent...
pip install hermes-agent 2>nul
if errorlevel 1 (
    echo FAILED: hermes-agent install
    pause
    exit /b 1
)
echo OK

REM === 2. Google Workspace MCP ===
echo [2/5] Google Workspace MCP...
set MCP_REPO=%~dp0..\google_workspace_mcp
if not exist "%MCP_REPO%" (
    echo Cloning google_workspace_mcp...
    git clone https://github.com/taylorwilsdon/google_workspace_mcp.git "%MCP_REPO%"
)
cd /d "%MCP_REPO%"
pip install -e . 2>nul
echo OK

REM === 3. Gateway Install ===
echo [3/5] Gateway install (Startup folder, no admin)...
hermes gateway install
echo OK

REM === 4. Config ===
echo [4/5] Config setup...
set CONFIG_DIR=%~dp0..\config
if not exist "%CONFIG_DIR%\config.yaml" (
    if exist "%CONFIG_DIR%\config.yaml.example" (
        copy "%CONFIG_DIR%\config.yaml.example" "%CONFIG_DIR%\config.yaml"
        echo NOTE: Edit %CONFIG_DIR%\config.yaml with your API keys
    )
)
echo OK

REM === 5. Startup entries ===
echo [5/5] Startup entries...
set STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set SRC_DIR=%~dp0..\startup
copy /Y "%SRC_DIR%\Hermes_Gateway_watchdog_launcher.vbs" "%STARTUP_DIR%\" 2>nul
copy /Y "%SRC_DIR%\start_wsmcp.bat" "%STARTUP_DIR%\" 2>nul
echo OK

echo.
echo === Setup Complete ===
echo Next: Edit config/config.yaml with your API keys
echo Then: hermes gateway run
pause
