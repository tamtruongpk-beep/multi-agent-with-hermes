import os
import subprocess
import sys
import time
from pathlib import Path

os.environ["HERMES_HOME"] = 'C:\\Users\\thanhtam\\AppData\\Local\\hermes'
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["HERMES_GATEWAY_DETACHED"] = "1"

sys.path.insert(0, 'C:\\Users\\thanhtam\\AppData\\Local\\hermes\\hermes-agent\\venv\\Lib\\site-packages')
sys.path.insert(0, 'C:\\Users\\thanhtam\\AppData\\Local\\hermes\\hermes-agent')
os.chdir('C:\\Users\\thanhtam\\AppData\\Local\\hermes\\hermes-agent')

from gateway.status import get_running_pid, is_gateway_runtime_lock_active


def _log(message: str) -> None:
    log_dir = Path('C:\\Users\\thanhtam\\AppData\\Local\\hermes') / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "gateway-launcher.log").open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


existing = get_running_pid()
lock_active = is_gateway_runtime_lock_active()
if existing or lock_active:
    _log(f"already running pid={existing} lock_active={lock_active}")
    raise SystemExit(0)

argv = ['C:\\Users\\thanhtam\\AppData\\Roaming\\uv\\python\\cpython-3.11-windows-x86_64-none\\pythonw.exe', '-m', 'hermes_cli.main', 'gateway', 'run', '--replace']
env = dict(os.environ)
env["VIRTUAL_ENV"] = 'C:\\Users\\thanhtam\\AppData\\Local\\hermes\\hermes-agent\\venv'
env["PYTHONPATH"] = 'C:\\Users\\thanhtam\\AppData\\Local\\hermes\\hermes-agent\\venv\\Lib\\site-packages' + os.pathsep + env.get("PYTHONPATH", "")
flags = 0x00000008 | 0x00000200 | 0x08000000

log_dir = Path('C:\\Users\\thanhtam\\AppData\\Local\\hermes') / "logs"
log_dir.mkdir(parents=True, exist_ok=True)
stdio_path = log_dir / "gateway-stdio.log"
with stdio_path.open("ab", buffering=0) as log_fh:
    proc = subprocess.Popen(
        argv,
        cwd='C:\\Users\\thanhtam\\AppData\\Local\\hermes\\hermes-agent',
        env=env,
        creationflags=flags,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=log_fh,
        stderr=log_fh,
    )

for _ in range(90):
    current = get_running_pid()
    lock_active = is_gateway_runtime_lock_active()
    if current or lock_active:
        _log(f"started pid={current} lock_active={lock_active} via child={proc.pid}")
        raise SystemExit(0)
    time.sleep(0.2)

_log(f"start failed child={proc.pid}")
raise SystemExit(1)
