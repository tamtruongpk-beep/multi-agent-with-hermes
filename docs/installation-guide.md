# Multi-Agent with Hermes — Cài Đặt Từ Đầu

## Mục Tiêu

Clone repo → chạy 1 lệnh → có đầy đủ: Hermes gateway (Telegram) + MCP (Gmail/Calendar/Drive/Sheets) + auto-start khi logon.

## Yêu Cầu

- Windows 10/11
- Git installed
- Python 3.11+ (uv có sẵn sau khi cài Hermes)

---

## Bước 1: Clone Repo

```powershell
git clone https://github.com/tamtruongpk-beep/multi-agent-with-hermes.git
cd multi-agent-with-hermes
```

## Bước 2: Cài Hermes Agent

```powershell
# Cách 1: PowerShell (1 dòng — khuyên dùng)
iex (irm https://raw.githubusercontent.com/tamtruongpk-beep/multi-agent-with-hermes/main/setup/install.ps1)

# Cách 2: Thủ công
pip install hermes-agent
```

> `install.ps1` tự động:
> - Cài Hermes Agent (pull upstream install script)
> - Clone google_workspace_mcp repo
> - Copy gateway service files
> - Tạo Startup entries
> - Patch hardcoded paths cho user hiện tại

## Bước 3: Điền Secrets

Sau khi install xong, điền credentials vào 2 file:

### 3a. Hermes config — `%LOCALAPPDATA%\hermes\.env`

```env
# Telegram
TELEGRAM_BOT_TOKEN=your_telegram_bot_token

# Model providers (chọn ít nhất 1)
OPENAI_API_KEY=sk-...
OPENROUTER_API_KEY=sk-or-...
GEMINI_API_KEY=...

# Tavily search
TAVILY_API_KEY=...

# Mem0 memory
MEM0_API_KEY=...
```

### 3b. Hermes config — `%LOCALAPPDATA%\hermes\config.yaml`

```yaml
# Provider + model
provider: openai   # hoặc openai / openrouter / anthropic / gemini
model: gpt-4o
base_url: https://api.openai.com/v1   # bỏ qua nếu dùng OpenAI trực tiếp
api_key: ${OPENAI_API_KEY}           # đọc từ .env

# Telegram
telegram:
  bot_token: ${TELEGRAM_BOT_TOKEN}
```

### 3c. Google OAuth — `%LOCALAPPDATA%\hermes\google_workspace_mcp\.env`

Tạo OAuth Client ở [Google Cloud Console](https://console.cloud.google.com/apis/credentials) trước:

```env
GOOGLE_OAUTH_CLIENT_ID=your_client_id.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-...
OAUTHLIB_INSECURE_TRANSPORT=1
```

## Bước 4: OAuth Login (1 lần)

```powershell
# Chạy MCP server
cd %LOCALAPPDATA%\hermes\google_workspace_mcp
python start_wsmcp.py

# Trigger OAuth — mở browser để login Google
hermes mcp login google_workspace
```

Browser mở → login → tokens tự lưu vào `%USERPROFILE%\.google_workspace_mcp\credentials\`

## Bước 5: Verify

```powershell
# MCP server healthy
curl http://127.0.0.1:8000/

# Hermes thấy MCP tools
hermes mcp list
```

Kết quả mong đợi:
```
{"status":"healthy","service":"workspace-mcp","version":"1.21.1"}
```

```
MCP Servers:
  google_workspace   http://127.0.0.1:8000/mcp   ✓ enabled
  stitch             https://stitch.googleapis... ✓ enabled
  supabase           https://mcp.supabase.com/... ✓ enabled
```

## Bước 6: Cài Gateway

```powershell
hermes gateway install
```

> Không cần admin. Không dùng Scheduled Task.
> Dùng Startup folder + VBS watchdog chain.

## Bước 7: Khởi Động

```powershell
# Sau reboot — gateway + MCP tự chạy (Startup folder)
# Hoặc chạy ngay:
hermes gateway run
```

---

## File Structure Sau Cài Đặt

```
%LOCALAPPDATA%\hermes\
├── .env                          ← API keys (Telegram, Tavily, etc.)
├── config.yaml                   ← provider, model, Telegram
├── gateway-service\              ← autoheal chain
│   ├── Hermes_Gateway_watchdog_launcher.vbs
│   ├── Hermes_Gateway_watchdog.py
│   └── Hermes_Gateway_launcher.py
├── google_workspace_mcp\
│   ├── .env                      ← OAuth CLIENT_ID/SECRET
│   ├── start_wsmcp.py
│   └── fastmcp_server.py
├── profiles\                     ← worker profiles (kevin, sophia, etc.)
└── SOUL.md, MEMORY.md            ← Hannie personality

%USERPROFILE%\.google_workspace_mcp\
└── credentials\
    └── <email>.json             ← OAuth tokens (access + refresh)

%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\
├── Hermes_Gateway_watchdog_launcher.vbs  ← gateway auto-start
└── start_wsmcp.bat                       ← MCP auto-start
```

---

## Startup Flow (Sau Reboot)

```
1. Windows logon
2. Startup folder:
   ├── Hermes_Gateway_watchdog_launcher.vbs
   │     → watchdog (30s health check) → launcher → gateway
   └── start_wsmcp.bat
         → pythonw start_wsmcp.py → MCP server port 8000
3. Gateway connected Telegram ✓
4. MCP server healthy ✓
5. Tokens auto-refresh khi hết hạn ✓
```

---

## Troubleshooting

### Gateway không start

```powershell
hermes gateway status
hermes gateway logs
```

### MCP server offline

```powershell
# Check port
netstat -ano | findstr ":8000"

# Restart
cd %LOCALAPPDATA%\hermes\google_workspace_mcp
python start_wsmcp.py
```

### OAuth hết hạn

```powershell
# Xóa token cũ
del %USERPROFILE%\.google_workspace_mcp\credentials\<email>.json

# Login lại
hermes mcp login google_workspace
```

### Hermes không thấy MCP tools

```powershell
hermes mcp list
# Đảm bảo google_workspace = enabled
```

---

## Ghi Chú

| Chủ đề | Docs |
|--------|------|
| OAuth chi tiết | `docs/google-workspace-mcp-oauth-guide.md` |
| Gateway service internals | `docs/gateway-service-internals.md` (nếu có) |
| Profiles (kevin, sophia, etc.) | `docs/profiles-guide.md` (nếu có) |