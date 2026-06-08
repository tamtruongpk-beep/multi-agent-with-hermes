# Multi-Agent with Hermes

Cấu hình multi-agent system dùng Hermes Agent (Nous Research) với Telegram làm home channel.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│ TELEGRAM                          │
│  Tam Truong DM ·  Tam Truong & Co (group)          │
└──────────┬──────────────────────────────────────────┘
           │ webhook / polling
┌──────────▼──────────────────────────────────────────┐
│  HANNIE (default profile) — Gateway + Autoheal      │
│  hermes gateway run                                 │
│  Startup → VBS → watchdog (30s) → launcher → gw    │
└──────────┬──────────────────────────────────────────┘
           │ dispatcher / kanban
┌──────────▼──────────────────────────────────────────┐
│  WORKER PROFILES (on-demand)                         │
│  Kevin (CTO/infra) · Sophia (frontend/UI)           │
│  Tracy (content/marketing) · Elsa (insurance)       │
│  Michael (research/strategy)                        │
└─────────────────────────────────────────────────────┘

┌── MCP Servers ─────────────────────────────────────┐
│  Stitch (Google Stitch MCP) — port stitch:443      │
│  Supabase (DB + docs) — mcp.supabase.com           │
│  Google Workspace (Gmail/Calendar/Drive/Sheets)    │
│    → google_workspace_mcp (port 8000)              │
│    → start_wsmcp.bat (Startup)                     │
└─────────────────────────────────────────────────────┘
```

## Quick Setup (Clone & Run)

### 1. Clone repo
```bash
git clone https://github.com/<your-username>/multi-agent-with-hermes.git
cd multi-agent-with-hermes
```

### 2. Cài Hermes Agent
```bash
# Cài hermes-agent từ upstream
pip install hermes-agent

# Hoặc dùng source (nếu có local checkout)
# git clone https://github.com/nousresearch/hermes-agent.git
# cd hermes-agent && pip install -e .
```

### 3. Cấu hình
```bash
# Copy config template
cp config/config.yaml.example config/config.yaml

# Chỉnh sửa config/config.yaml:
#   - Thay API keys (GOOGLE_OAUTH_CLIENT_ID/SECRET, Telegram token, etc.)
#   - Chỉnh HERMES_HOME path phù hợp với máy mới
#   - Cập nhật base_url/provider nếu cần
```

### 4. Cài MCP servers
```bash
# Google Workspace MCP
cd google_workspace_mcp
pip install -e .
uvx --from . workspace-mcp --transport streamable-http --port 8000
# Hoặc dùng: scripts/setup_google_workspace_mcp.sh
```

### 5. Install Gateway (Windows)
```powershell
# Chạy với quyền user thường (không cần admin)
hermes gateway install
```

### 6. Start
```bash
# Telegram gateway (default profile, auto-start on logon)
hermes gateway run

# Worker profile cụ thể
hermes gateway run --profile kevin
```

## File Structure

```
multi-agent-with-hermes/
├── README.md
├── config/
│   ├── config.yaml.example        # Main config (copy → config.yaml)
│   ├── SOUL.md                   # Hannie personality
│   └── MEMORY.md                 # Hannie memory
├── gateway-service/              # Autoheal chain (default profile)
│   ├── Hermes_Gateway_watchdog_launcher.vbs
│   ├── Hermes_Gateway_watchdog.py
│   ├── Hermes_Gateway_launcher.py
│   └── autoheal.enabled
├── startup/                     # Windows Startup folder
│   ├── Hermes_Gateway_watchdog_launcher.vbs
│   └── start_wsmcp.bat           # Google Workspace MCP auto-start
├── google_workspace_mcp/         # Google Workspace MCP server
│   ├── start_wsmcp.py            # Windows launcher script
│   ├── fastmcp.json
│   ├── fastmcp_server.py
│   ├── auth/                    # OAuth implementation
│   ├── gmail/                   # Gmail tools
│   ├── gcalendar/              # Calendar tools
│   ├── gdrive/                 # Drive tools
│   ├── gsheets/                # Sheets tools
│   └── ...
├── profiles/                    # Worker profile configs
│   ├── kevin/                  # CTO/infra
│   ├── sophia/                 # frontend/UI
│   ├── tracy/                  # content/marketing
│   ├── elsa/                  # insurance
│   └── michael/               # research/strategy
└── setup/
    ├── setup_windows.bat       # Windows setup script
    └── setup_mcp.sh            # MCP setup script
```

## Profiles

| Profile | Role | Trigger |
|---|---|---|
| default (Hannie) | COO/Orchestrator | Startup (logon) |
| kevin | CTO/DevOps | `hermes gateway run --profile kevin` |
| sophia | Frontend/UI | `hermes gateway run --profile sophia` |
| tracy | Content/Marketing | `hermes gateway run --profile tracy` |
| elsa | Insurance | `hermes gateway run --profile elsa` |
| michael | Research/Strategy | `hermes gateway run --profile michael` |

## MCP Servers

| MCP | Purpose | Config |
|---|---|---|
| Stitch | UI design generation | `config.yaml mcp_servers.stitch` |
| Supabase | Database + docs | `config.yaml mcp_servers.supabase` |
| Google Workspace | Gmail/Calendar/Drive/Sheets | port 8000, auto-start |

## Locked Files (không merge với upstream)

```
hermes_cli/gateway_windows.py  merge=ours
hermes_cli/uninstall.py        merge=ours
tools/checkpoint_manager.py    merge=ours
tools/env_probe.py             merge=ours
tools/environments/base.py     merge=ours
tools/environments/docker.py   merge=ours
tools/lazy_deps.py             merge=ours
```

## Google Workspace MCP - OAuth Setup

**Docs:** [docs/google-workspace-mcp-oauth-guide.md](docs/google-workspace-mcp-oauth-guide.md)

### Quick OAuth Setup (1 lần)

```bash
# 1. Tạo OAuth Client ở Google Cloud Console
#    https://console.cloud.google.com/apis/credentials

# 2. Tạo .env trong google_workspace_mcp repo
notepad %LOCALAPPDATA%\hermes\google_workspace_mcp\.env
#   GOOGLE_OAUTH_CLIENT_ID=YOUR_CLIENT_ID
#   GOOGLE_OAUTH_CLIENT_SECRET=YOUR_CLIENT_SECRET
#   OAUTHLIB_INSECURE_TRANSPORT=1

# 3. Chạy server lần đầu → browser login → tokens tự lưu
cd %LOCALAPPDATA%\hermes\google_workspace_mcp
python start_wsmcp.py

# 4. Verify
curl http://127.0.0.1:8000/

# 5. Copy vào Startup folder (auto-start khi logon)
copy startup\start_wsmcp.bat "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\"
```

### OAuth Flow

```
Credentials (.env)          Tokens (~/.google_workspace_mcp/credentials/)
┌──────────────────┐        ┌──────────────────────────┐
│ CLIENT_ID        │        │ access_token (1h)        │
│ CLIENT_SECRET    │ ──OAuth──▶ refresh_token (dài hạn) │
└──────────────────┘        └──────────────────────────┘
                                              │
                                   Server tự refresh khi hết hạn
                                   → Không cần login lại
```

## Notes

- **Secrets**: Credentials trong `google_workspace_mcp/` KHÔNG đưa lên repo (đã có `.gitignore`)
- **HERMES_HOME**: Mặc định `~/.hermes/` (default profile). Worker profiles dùng `~/.hermes/profiles/<name>/`
- **Startup**: Chỉ default profile chạy auto qua Startup folder. Worker profiles start manual
- **Windows**: Không dùng Scheduled Task (cần admin). Dùng Startup folder + VBS watchdog chain
