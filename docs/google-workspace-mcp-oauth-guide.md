# Google Workspace MCP - Hướng Dẫn OAuth Setup

## Tổng Quan

```
Google Workspace MCP dùng OAuth 2.0 để truy cập Gmail, Calendar, Drive, Sheets.
Credentials (CLIENT_ID/SECRET) ≠ Tokens (access/refresh).

Cài đặt 1 lần → Tokens refresh tự động → Không cần login lại.
```

## 2 Loại File Credentials

| File | Chứa | Nơi lưu |
|------|------|---------|
| `.env` | CLIENT_ID + CLIENT_SECRET | `google_workspace_mcp/.env` (repo) |
| `*.json` | access_token + refresh_token | `~/.google_workspace_mcp/credentials/` |

```
~/.google_workspace_mcp/
├── credentials/
│   ├── tamtruong.pk@gmail.com.json   ← tokens (tự tạo sau OAuth)
│   └── oauth_states.json             ← OAuth state tokens
└── .env                              ← CLIENT credentials (tạo tay)
```

## Setup Từng Bước

### Bước 1: Tạo Google OAuth Client (1 lần)

1. Vào [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. Tạo **OAuth 2.0 Client ID** (Desktop app hoặc Web application)
3. Copy **Client ID** và **Client Secret**

### Bước 2: Tạo `google_workspace_mcp/.env`

```bash
# Trong thư mục google_workspace_mcp repo
notepad .env
```

Nội dung:
```env
GOOGLE_OAUTH_CLIENT_ID=YOUR_CLIENT_ID.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-YOUR_SECRET
WORKSPACE_MCP_PORT=8000
WORKSPACE_MCP_HOST=127.0.0.1
OAUTHLIB_INSECURE_TRANSPORT=1
MCP_ENABLE_OAUTH21=1
```

### Bước 3: Chạy MCP Server

```bash
cd %LOCALAPPDATA%\hermes\google_workspace_mcp
python start_wsmcp.py
```

Server khởi động background trên port 8000.

### Bước 4: Trigger OAuth Login

```bash
hermes mcp login google_workspace
```

Hermes mở browser → user login Google → tokens tự lưu vào `~/.google_workspace_mcp/credentials/`

### Bước 5: Verify

```bash
curl http://127.0.0.1:8000/
hermes mcp list
```

Kết quả:
```json
{"status":"healthy","service":"workspace-mcp","version":"1.21.1"}
```

### Bước 6: Auto-start khi logon (Windows)

Copy file vào Startup folder:

```bash
# Copy startup script
copy startup\start_wsmcp.bat "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\"
```

Sau reboot, server tự khởi động — không cần login lại.

## Token Refresh Tự Động

```
Access token hết hạn → Server tự động dùng refresh_token lấy token mới
→ Không cần user tương tác → Không cần mở trình duyệt lại
```

Tokens có thời hạn:
- Access token: ~1 giờ
- Refresh token: Dài hạn (revoke được trong Google Account)

## Kiểm Tra Token

```bash
# Xem tokens đã lưu chưa
dir "%USERPROFILE%\.google_workspace_mcp\credentials"

# Tokens có cấu trúc:
# {
#   "token": "ya29....",
#   "refresh_token": "1//...",
#   "token_uri": "https://oauth2.googleapis.com/token",
#   "client_id": "...",
#   "expiry": "2026-06-08T..."
# }
```

## Troubleshooting

### Lỗi "invalid_grant" hoặc "Token expired"

→ Xóa token file và chạy lại server để OAuth lại:
```bash
del "%USERPROFILE%\.google_workspace_mcp\credentials\<email>.json"
python start_wsmcp.py
```

### Lỗi "Could not find credentials"

→ Kiểm tra `.env` có đúng vị trí không:
```bash
# .env phải nằm trong thư mục google_workspace_mcp repo
notepad C:\Users\<user>\AppData\Local\hermes\google_workspace_mcp\.env
```

### Server không start được

1. Kiểm tra port 8000 có đang bị chiếm không:
```bash
netstat -ano | findstr ":8000"
```

2. Kill process chiếm port:
```bash
taskkill /F /PID <PID>
```

## Quick Reference

| Command | Mô tả |
|---------|-------|
| `python start_wsmcp.py` | Start server (lần đầu → mở OAuth browser) |
| `curl http://127.0.0.1:8000/` | Check server health |
| `hermes mcp list` | Xem MCP tools trong Hermes |

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  start_wsmcp.bat (Startup folder)                   │
│    → pythonw start_wsmcp.py                         │
│        → uvx workspace-mcp --transport streamable-http │
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│  fastmcp_server.py (uvx)                           │
│    → load_dotenv() ← .env (CLIENT credentials)     │
│    → OAuth flow (lần đầu) → browser login          │
│    → save tokens → ~/.google_workspace_mcp/credentials/ │
│    → MCP tools: gmail, calendar, drive, sheets     │
└─────────────────────────────────────────────────────┘
```