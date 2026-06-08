# Multi-Agent with Hermes

Cấu hình multi-agent system dùng Hermes Agent với Telegram làm home channel, MCP servers cho Google Workspace, và auto-start khi logon.

## Cài Đặt A-Z

👉 **[Hướng dẫn cài đặt chi tiết](docs/installation-guide.md)**

TL;DR:
```powershell
# 1 dòng cài đầy đủ
iex (irm https://bit.ly/hermes-multi-agent)
```

## Architecture

```
┌─────────────────────────────────────────────────────┐
│ TELEGRAM                              │
│  Tam Truong DM · Tam Truong & Co (group)           │
└──────────┬──────────────────────────────────────────┘
           │ webhook / polling
┌──────────▼──────────────────────────────────────────┐
│  HANNIE (default profile) — Gateway + Autoheal      │
│  Startup → VBS → watchdog (30s) → launcher → gw    │
└──────────┬──────────────────────────────────────────┘
           │ dispatcher / kanban
┌──────────▼──────────────────────────────────────────┐
│  WORKER PROFILES (on-demand)                        │
│  Kevin (CTO) · Sophia (UI) · Tracy (content)       │
│  Elsa (insurance) · Michael (research)              │
└─────────────────────────────────────────────────────┘

┌── MCP Servers ─────────────────────────────────────┐
│  Google Workspace — Gmail/Calendar/Drive/Sheets    │
│    → port 8000, auto-start via Startup folder      │
│  Stitch — UI design generation                     │
│  Supabase — Database + docs                        │
└─────────────────────────────────────────────────────┘
```

## File Structure

```
multi-agent-with-hermes/
├── README.md
├── docs/
│   ├── installation-guide.md       ← Cài đặt A-Z
│   └── google-workspace-mcp-oauth-guide.md
├── config/                        ← Hermes config templates
│   ├── config.yaml.example
│   ├── SOUL.md, MEMORY.md
│   └── profiles/                  ← worker profile configs
├── gateway-service/               ← autoheal chain (default profile)
│   ├── Hermes_Gateway_watchdog_launcher.vbs
│   ├── Hermes_Gateway_watchdog.py
│   ├── Hermes_Gateway_launcher.py
│   └── autoheal.enabled
├── startup/                       ← Windows Startup folder
│   ├── Hermes_Gateway_watchdog_launcher.vbs
│   └── start_wsmcp.bat            ← Google Workspace MCP
├── google_workspace_mcp/          ← MCP server repo (git submodule pattern)
│   ├── .env.example               ← OAuth credentials template
│   └── start_wsmcp.py
└── setup/
    ├── install.ps1                ← 1-dòng installer (PowerShell)
    └── setup_windows.bat          ← manual setup
```

## Profiles

| Profile | Role | Trigger |
|---------|------|---------|
| default (Hannie) | COO/Orchestrator | Startup (logon) |
| kevin | CTO/DevOps | `hermes gateway run --profile kevin` |
| sophia | Frontend/UI | `hermes gateway run --profile sophia` |
| tracy | Content/Marketing | `hermes gateway run --profile tracy` |
| elsa | Insurance | `hermes gateway run --profile elsa` |
| michael | Research/Strategy | `hermes gateway run --profile michael` |

## MCP Servers

| MCP | Purpose | Config |
|-----|---------|--------|
| Google Workspace | Gmail/Calendar/Drive/Sheets | port 8000, auto-start |
| Stitch | UI design generation | `config.yaml mcp_servers.stitch` |
| Supabase | Database + docs | `config.yaml mcp_servers.supabase` |

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

## Notes

- **Secrets**: Credentials trong `google_workspace_mcp/` KHÔNG đưa lên repo (đã có `.gitignore`)
- **HERMES_HOME**: Mặc định `~/.hermes/` (default profile). Worker profiles dùng `~/.hermes/profiles/<name>/`
- **Startup**: Chỉ default profile chạy auto qua Startup folder. Worker profiles start manual
- **Windows**: Không dùng Scheduled Task (cần admin). Dùng Startup folder + VBS watchdog chain