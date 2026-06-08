import os, subprocess, sys

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
HERMES_HOME = os.environ.get("HERMES_HOME", os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes"))
UVX_BIN = os.path.join(HERMES_HOME, "bin", "uvx")

cmd = [
    UVX_BIN,
    "--from", REPO_DIR,
    "workspace-mcp",
    "--transport", "streamable-http",
    "--tools", "drive", "calendar", "sheets", "gmail",
]

proc = subprocess.Popen(
    cmd,
    cwd=REPO_DIR,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
)
print(f"Started PID: {proc.pid}")
