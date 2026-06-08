import os, subprocess, secrets, sys

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
UVX_BIN = r"C:\Users\THANHTAM\AppData\Local\hermes\bin\uvx"
PORT = "8000"

key = secrets.token_hex(32)
env = dict(os.environ)
env.update({
    'GOOGLE_OAUTH_CLIENT_ID': 'YOUR_GOOGLE_CLIENT_ID',
    'GOOGLE_OAUTH_CLIENT_SECRET': 'YOUR_GOOGLE_CLIENT_SECRET',
    'GOOGLE_OAUTH_REDIRECT_URI': f'http://localhost:{PORT}/oauth2callback',
    'WORKSPACE_MCP_PORT': PORT,
    'WORKSPACE_MCP_HOST': '127.0.0.1',
    'OAUTHLIB_INSECURE_TRANSPORT': '1',
    'MCP_ENABLE_OAUTH21': '1',
    'FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY': key,
})

cmd = [
    UVX_BIN,
    '--from', 'file:///C:/Users/thanhtam/google_workspace_mcp',
    'workspace-mcp',
    '--transport', 'streamable-http',
    '--tools', 'drive', 'calendar', 'sheets', 'gmail',
]

proc = subprocess.Popen(
    cmd,
    env=env,
    cwd=REPO_DIR,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
)
print(f'Started PID: {proc.pid}')
print(f'Health: curl http://127.0.0.1:{PORT}/')