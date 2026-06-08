import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ["HERMES_HOME"] = 'C:\\Users\\thanhtam\\AppData\\Local\\hermes'
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["HERMES_GATEWAY_DETACHED"] = "1"
os.environ["VIRTUAL_ENV"] = 'C:\\Users\\thanhtam\\AppData\\Local\\hermes\\hermes-agent\\venv'
os.environ["PYTHONPATH"] = 'C:\\Users\\thanhtam\\AppData\\Local\\hermes\\hermes-agent\\venv\\Lib\\site-packages' + os.pathsep + os.environ.get("PYTHONPATH", "")

sys.path.insert(0, 'C:\\Users\\thanhtam\\AppData\\Local\\hermes\\hermes-agent\\venv\\Lib\\site-packages')
sys.path.insert(0, 'C:\\Users\\thanhtam\\AppData\\Local\\hermes\\hermes-agent')
os.chdir('C:\\Users\\thanhtam\\AppData\\Local\\hermes\\hermes-agent')

from gateway.status import get_running_pid, is_gateway_runtime_lock_active, _get_gateway_lock_path

PID_PATH = Path('C:\\Users\\thanhtam\\AppData\\Local\\hermes\\gateway-service\\watchdog.pid')
ENABLED_PATH = Path('C:\\Users\\thanhtam\\AppData\\Local\\hermes\\gateway-service\\autoheal.enabled')
STATE_PATH = Path('C:\\Users\\thanhtam\\AppData\\Local\\hermes\\gateway-service\\Hermes_Gateway.state.json')
LOG_DIR = Path('C:\\Users\\thanhtam\\AppData\\Local\\hermes') / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = LOG_DIR / "gateway-watchdog.log"
LAUNCHER = 'C:\\Users\\thanhtam\\AppData\\Local\\hermes\\gateway-service\\Hermes_Gateway_launcher.py'
PYTHONW = 'C:\\Users\\thanhtam\\AppData\\Roaming\\uv\\python\\cpython-3.11-windows-x86_64-none\\pythonw.exe'
LAUNCHER_VBS = 'C:\\Users\\thanhtam\\AppData\\Local\\hermes\\gateway-service\\Hermes_Gateway_watchdog_launcher.vbs'


def _log(message: str) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
_log("watchdog started")
failures = 0
while True:
    if not ENABLED_PATH.exists():
        _log("autoheal disabled; watchdog exiting")
        break
    
    # Check OS-level lock first — this is the authoritative source
    lock_active = is_gateway_runtime_lock_active()
    running = get_running_pid()
    
    if running or lock_active:
        # Gateway is alive (either detected by PID or OS lock is held)
        failures = 0
        time.sleep(30.0)
        continue
    
    # Neither OS lock held nor PID detected — truly dead, safe to heal
    _log("gateway not running (OS lock free, no PID); triggering launcher")
    
    # Launch the Python launcher directly with base pythonw.exe. Avoid wscript/cmd
    # in the watchdog heal path so no transient console host can flash.
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(
            [PYTHONW, LAUNCHER],
            creationflags=flags,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        rc = proc.wait(timeout=30)
    except Exception as e:
        _log(f"launcher spawn failed: {e}")
        rc = 1
    
    if rc == 0:
        failures = 0
        _log("launcher triggered")
        time.sleep(8.0)
        continue
    failures += 1
    STATE_PATH.write_text(json.dumps({"failures": failures, "last_rc": rc}), encoding="utf-8")
    _log(f"launcher failed rc={rc} failures={failures}")
    time.sleep(min(60, 5 * failures))

try:
    PID_PATH.unlink()
except FileNotFoundError:
    pass
