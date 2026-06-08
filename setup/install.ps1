# ============================================================================
# Multi-Agent Hermes Installer for Windows
# ============================================================================
# Installs Hermes Agent (via upstream nous/hermes-agent) + multi-agent config
#
# Usage:
#   iex (irm https://raw.githubusercontent.com/tamtruongpk-beep/multi-agent-with-hermes/main/setup/install.ps1)
#
# ============================================================================
param(
    [switch]$SkipHermesInstall,
    [switch]$SkipMcpSetup,
    [switch]$SkipGatewaySetup,
    [switch]$NonInteractive,
    [string]$HermesHome = $(if ($env:HERMES_HOME) { $env:HERMES_HOME } else { "$env:LOCALAPPDATA\hermes" }),
    [string]$InstallDir = $(if ($env:HERMES_HOME) { "$env:HERMES_HOME\hermes-agent" } else { "$env:LOCALAPPDATA\hermes\hermes-agent" }),
    [string]$Branch = "main",
    [string]$McpRepo = "https://github.com/taylorwilsdon/google_workspace_mcp.git",
    [string]$McpRepoDir = "$HermesHome\google_workspace_mcp"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

# ============================================================================
# Helpers
# ============================================================================
function Write-Banner {
    Write-Host ""
    Write-Host "+---------------------------------------------------------+" -ForegroundColor Magenta
    Write-Host "| Multi-Agent Hermes Installer                      |" -ForegroundColor Magenta
    Write-Host "+---------------------------------------------------------+" -ForegroundColor Magenta
    Write-Host "|  Hermes Agent + Telegram + Google Workspace MCP         |" -ForegroundColor Magenta
    Write-Host "+---------------------------------------------------------+" -ForegroundColor Magenta
    Write-Host ""
}

function Write-Info  ($m) { Write-Host "-> $m" -ForegroundColor Cyan }
function Write-Success($m) { Write-Host "[OK] $m" -ForegroundColor Green }
function Write-Warn  ($m) { Write-Host "[!] $m" -ForegroundColor Yellow }
function Write-Err   ($m) { Write-Host "[X] $m" -ForegroundColor Red }

# ============================================================================
# Stage 1: Hermes Agent (upstream nous/hermes-agent)
# ============================================================================
function Install-HermesAgent {
    Write-Info "Installing Hermes Agent (upstream)..."
    $installScript = "$env:TEMP\hermes-install.ps1"
    try {
        Invoke-WebRequest -Uri "https://hermes-agent.nousresearch.com/install.ps1" -OutFile $installScript -UseBasicParsing
        Write-Info "Running upstream install.ps1..."
       & $installScript -HermesHome $HermesHome -InstallDir $InstallDir -Branch $Branch
        Remove-Item $installScript -Force -ErrorAction SilentlyContinue
        Write-Success "Hermes Agent installed"
    } catch {
        Write-Err "Hermes install failed: $_"
        throw
    }
}

# ============================================================================
# Stage 2: Google Workspace MCP
# ============================================================================
function Install-GoogleWorkspaceMcp {
    Write-Info "Setting up Google Workspace MCP..."
    if (Test-Path $McpRepoDir) {
        Write-Success "MCP repo already exists at $McpRepoDir"
    } else {
        Write-Info "Cloning google_workspace_mcp..."
        git clone $McpRepo $McpRepoDir
    }
    $uvCmd = "$HermesHome\bin\uv.exe"
    if (Test-Path $uvCmd) {
        Push-Location $McpRepoDir
        try {
            & $uvCmd pip install -e . 2>&1 | Out-Null
            Write-Success "Google Workspace MCP installed"
        } finally { Pop-Location }
    } else {
        Write-Warn "uv not found, skipping MCP pip install"
    }
}

# ============================================================================
# Stage 3: Config setup
# ============================================================================
function Setup-Config {
    Write-Info "Config setup..."
    $configSrc = "$PSScriptRoot\..\config\config.yaml.example"
    $configDst = "$HermesHome\config.yaml"
    if (-not (Test-Path $configDst)) {
        if (Test-Path $configSrc) {
            Copy-Item $configSrc $configDst
            Write-Success "config.yaml created from template"
        }
    }
    Write-Host ""
    Write-Host "=== IMPORTANT: Fill in secrets ===" -ForegroundColor Yellow
    Write-Host "Edit $configDst:" -ForegroundColor Yellow
    Write-Host "  - TELEGRAM_TOKEN (required)" -ForegroundColor Yellow
    Write-Host "  - Google OAuth credentials" -ForegroundColor Yellow
    Write-Host "  - Model provider base_url / api_key" -ForegroundColor Yellow
    Write-Host ""
}

# ============================================================================
# Stage 4: Gateway service files
# ============================================================================
function Setup-GatewayService {
    Write-Info "Setting up gateway service files..."
    $svcDir = "$HermesHome\gateway-service"
    if (-not (Test-Path $svcDir)) {
        New-Item -ItemType Directory -Path $svcDir -Force | Out-Null
    }

    $svcSrc = "$PSScriptRoot\..\gateway-service"
    if (Test-Path $svcSrc) {
        Copy-Item "$svcSrc\*" $svcDir -Force
        Write-Success "Gateway service files copied to $svcDir"
    }

    # Patch hardcoded paths
    $oldHome = "C:\Users\thanhtam"
    $newHome = $env:USERPROFILE
    $serviceFiles = @(
        "$svcDir\Hermes_Gateway_watchdog_launcher.vbs",
        "$svcDir\Hermes_Gateway_watchdog.py",
        "$svcDir\Hermes_Gateway_launcher.py"
    )
    foreach ($f in $serviceFiles) {
        if (Test-Path $f) {
            $content = Get-Content $f -Raw
            if ($content -match [regex]::Escape($oldHome)) {
                $content = $content -replace [regex]::Escape($oldHome), $newHome
                Set-Content $f -Value $content -NoNewline
                Write-Success "  Patched: $(Split-Path $f -Leaf)"
            }
        }
    }
}

# ============================================================================
# Stage 5: Startup entries
# ============================================================================
function Setup-StartupEntries {
    Write-Info "Setting up Windows Startup entries..."
    $startupDir = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
    $srcStartup = "$PSScriptRoot\..\startup"
    $oldHome = "C:\Users\thanhtam"
    $newHome = $env:USERPROFILE

    if (Test-Path "$srcStartup\Hermes_Gateway_watchdog_launcher.vbs") {
        $vbsContent = Get-Content "$srcStartup\Hermes_Gateway_watchdog_launcher.vbs" -Raw
        $vbsContent = $vbsContent -replace [regex]::Escape($oldHome), $newHome
        Set-Content "$startupDir\Hermes_Gateway_watchdog_launcher.vbs" -Value $vbsContent -NoNewline
        Write-Success "Gateway watchdog VBS added to Startup"
    }

    if (Test-Path "$srcStartup\start_wsmcp.bat") {
        $batContent = Get-Content "$srcStartup\start_wsmcp.bat" -Raw
        $batContent = $batContent -replace [regex]::Escape($oldHome), $newHome
        Set-Content "$startupDir\start_wsmcp.bat" -Value $batContent -NoNewline
        Write-Success "Google Workspace MCP startup script added"
    }
}

# ============================================================================
# Stage 6: Replace modified hermes-agent files
# ============================================================================
function Install-ModifiedFiles {
    Write-Info "Applying local modifications to hermes-agent..."
    $modSrc = "$PSScriptRoot\..\config"

    # Map: filename in repo -> real path in hermes-agent
    $fileMap = @{
        "hermes_cli_gateway_windows.py" = "hermes_cli\gateway_windows.py"
        "hermes_cli_uninstall.py"        = "hermes_cli\uninstall.py"
        "tools_checkpoint_manager.py"    = "tools\checkpoint_manager.py"
        "tools_env_probe.py"             = "tools\env_probe.py"
        "tools_environments_base.py"     = "tools\environments\base.py"
        "tools_environments_docker.py"   = "tools\environments\docker.py"
        "tools_lazy_deps.py"             = "tools\lazy_deps.py"
    }

    foreach ($entry in $fileMap.GetEnumerator()) {
        $src = Join-Path $modSrc $entry.Key
        $dst = Join-Path $InstallDir $entry.Value
        if (Test-Path $src) {
            if (Test-Path $dst) {
                Copy-Item $src $dst -Force
                Write-Success "  $($entry.Key) -> $($entry.Value)"
            }
        }
    }
}

# ============================================================================
# Main
# ============================================================================
Write-Banner

Write-Info "HermesHome: $HermesHome"
Write-Info "InstallDir: $InstallDir"
Write-Host ""

if (-not $SkipHermesInstall) {
    Install-HermesAgent
} else {
    Write-Warn "Skipping Hermes Agent install"
}

if (-not $SkipMcpSetup) {
    Install-GoogleWorkspaceMcp
} else {
    Write-Warn "Skipping MCP setup"
}

Setup-Config
Setup-GatewayService
Setup-StartupEntries
Install-ModifiedFiles

Write-Host ""
Write-Success "=== Setup Complete ==="
Write-Host "Next steps:"
Write-Host "  1. Edit $HermesHome\.env with your API keys"
Write-Host "  2. Edit $HermesHome\config.yaml with provider/model settings"
Write-Host "  3. Run: hermes gateway install"
Write-Host "  4. Restart or: hermes gateway run"
Write-Host ""
