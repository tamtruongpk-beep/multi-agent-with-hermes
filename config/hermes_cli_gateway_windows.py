"""Windows gateway service backend (Startup-folder + Python watchdog).

This mirrors the contract exposed by ``launchd_install`` / ``launchd_start`` /
``launchd_status`` etc. on macOS and ``systemd_install`` / ``systemd_start`` on
Linux. On Windows it uses a Startup-folder ``.cmd`` launcher that spawns a
VBScript helper, which in turn launches a Python watchdog process. The
watchdog auto-heals the gateway every 30 s when it crashes — no Scheduled
Task, no administrator privileges needed.

Design notes
------------
* ``gateway install`` writes three files into ``<HERMES_HOME>/gateway-service/``:
    - ``<task_name>_watchdog_launcher.vbs`` — VBS helper that sets env vars and
      launches the watchdog Python script with ``pythonw.exe`` (no console).
    - ``<task_name>_watchdog.py`` — Python watchdog. Checks every 30 s whether the
      gateway is alive (OS lock + PID). Spawns the launcher when dead.
    - ``<task_name>_launcher.py`` — Python launcher. Guards against double-start,
      logs stdout/stderr to ``gateway-stdio.log``, then spawns the gateway with
      ``CREATE_NO_WINDOW`` flags.
  plus one Startup-folder entry:
    - ``<HERMES_HOME>/../Roaming/Microsoft/Windows/Start Menu/Programs/Startup/<task_name>.cmd``
      — thin ``.cmd`` that calls the VBS launcher. Runs at every Windows login.
* The watchdog reads ``gateway-service/autoheal.enabled`` — delete that file
  to disable auto-heal without uninstalling.
* Status = "is the startup .cmd present?" + "is there a gateway process
  running?" + "is the OS lock held?".
* All of this is Windows-only. ``import`` paths are still safe on POSIX but
  the functions raise if called on non-Windows.
"""

from __future__ import annotations

import ctypes
import locale
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Short timeouts: schtasks occasionally wedges and we don't want to hang forever.
_SCHTASKS_TIMEOUT_S = 15
_SCHTASKS_NO_OUTPUT_TIMEOUT_S = 30
# Patterns in schtasks stderr that mean "fall back to the Startup folder".
_FALLBACK_PATTERNS = re.compile(
    r"(access is denied|acceso denegado|přístup byl odepřen|schtasks timed out|schtasks produced no output)",
    re.IGNORECASE,
)
_ACCESS_DENIED_PATTERN = re.compile(r"(access is denied|acceso denegado)", re.IGNORECASE)

_TASK_NAME_DEFAULT = "Hermes_Gateway"
_TASK_DESCRIPTION = "Hermes Agent Gateway - Messaging Platform Integration"


def _schtasks_encoding() -> str:
    """Best-effort console encoding for decoding ``schtasks.exe`` output.

    On localized Windows (e.g. Chinese), ``schtasks`` emits text in the OEM/ANSI
    code page rather than UTF-8. Decoding with the wrong codec raised
    ``UnicodeDecodeError`` inside ``subprocess``' reader threads. Prefer the
    locale's preferred encoding and fall back to UTF-8.
    """
    try:
        return locale.getpreferredencoding(False) or "utf-8"
    except Exception:
        return "utf-8"


# ---------------------------------------------------------------------------
# Platform guard
# ---------------------------------------------------------------------------

def _assert_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("gateway_windows is Windows-only")


# ---------------------------------------------------------------------------
# Quoting helpers (two DIFFERENT parsers — do not mix)
# ---------------------------------------------------------------------------

def _quote_cmd_script_arg(value: str) -> str:
    """Quote a single argument for use INSIDE a .cmd file, for cmd.exe parsing.

    cmd.exe splits on spaces/tabs outside of double quotes. Embedded quotes
    are doubled. We also refuse line breaks because they'd terminate the
    logical command line mid-script.
    """
    if "\r" in value or "\n" in value:
        raise ValueError(f"refusing to quote value containing newline: {value!r}")
    if not value:
        return '""'
    if not re.search(r'[ \t"]', value):
        return value
    return '"' + value.replace('"', '""') + '"'


def _quote_schtasks_arg(value: str) -> str:
    """Quote a single argument for schtasks.exe's /TR parser.

    Schtasks uses a different quoting convention than cmd.exe: embedded
    quotes are backslash-escaped, and the whole thing is wrapped in double
    quotes if it contains whitespace or quotes.
    """
    if not re.search(r'[ \t"]', value):
        return value
    return '"' + value.replace('"', '\\"') + '"'


# ---------------------------------------------------------------------------
# schtasks.exe wrapper
# ---------------------------------------------------------------------------

def _exec_schtasks(args: list[str]) -> tuple[int, str, str]:
    """Run ``schtasks.exe`` with a hard timeout. Return (code, stdout, stderr).

    If schtasks wedges, returns code=124 with a synthetic stderr string —
    same convention OpenClaw uses, so the fallback detection regex matches.
    """
    _assert_windows()
    schtasks = shutil.which("schtasks")
    if schtasks is None:
        return (1, "", "schtasks.exe not found on PATH")
    try:
        proc = subprocess.run(
            [schtasks, *args],
            capture_output=True,
            text=True,
            # Localized Windows emits schtasks output in the console code page,
            # not UTF-8. Decode with the locale encoding and replace undecodable
            # bytes so a non-UTF-8 status line never surfaces a UnicodeDecodeError
            # traceback from subprocess' reader threads (issue #38172).
            encoding=_schtasks_encoding(),
            errors="replace",
            timeout=_SCHTASKS_TIMEOUT_S,
            # CREATE_NO_WINDOW avoids a flashing console window when the CLI
            # is itself hosted in a TUI. See tools/browser_tool.py for the
            # same pattern and the windows-subprocess-sigint-storm.md ref.
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        return (proc.returncode, proc.stdout or "", proc.stderr or "")
    except subprocess.TimeoutExpired:
        return (124, "", f"schtasks timed out after {_SCHTASKS_TIMEOUT_S}s")
    except OSError as e:
        return (1, "", f"schtasks invocation failed: {e}")


def _should_fall_back(code: int, detail: str) -> bool:
    return code == 124 or bool(_FALLBACK_PATTERNS.search(detail or ""))


def _is_access_denied(detail: str) -> bool:
    return bool(_ACCESS_DENIED_PATTERN.search(detail or ""))


def _is_running_as_admin() -> bool:
    """Return True when the current Windows process is elevated."""
    _assert_windows()
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _current_profile_cli_args() -> list[str]:
    """Return CLI args that preserve the current Hermes profile."""
    from hermes_cli.gateway import _profile_arg

    profile_arg = _profile_arg()
    return shlex.split(profile_arg) if profile_arg else []


def _launch_elevated_gateway_command(command: str, extra_args: list[str] | None = None) -> bool:
    """Launch an elevated gateway subcommand via UAC and return True on handoff.

    Use pythonw.exe for the elevated child so approving UAC does not leave a
    second elevated console window sitting open after the handoff. All operator
    decisions are already collected in the parent shell before this point.
    """
    _assert_windows()
    args = ["-m", "hermes_cli.main", *_current_profile_cli_args(), "gateway", command]
    if extra_args:
        args.extend(extra_args)
    params = subprocess.list2cmdline(args)
    cwd = str(Path(__file__).resolve().parent.parent)
    elevated_python = _derive_venv_pythonw(sys.executable)
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            elevated_python,
            params,
            cwd,
            0,  # SW_HIDE: pythonw child should not create a visible console.
        )
    except Exception as exc:
        print(f"⚠ Could not launch elevated gateway {command} prompt: {exc}")
        return False
    if result <= 32:
        print(f"⚠ Elevated gateway {command} prompt was not started (ShellExecuteW={result})")
        return False
    return True


def _launch_elevated_install(
    force: bool = False,
    *,
    start_now: bool | None = None,
    start_on_login: bool | None = None,
) -> bool:
    """Launch an elevated gateway install via UAC and return True on handoff."""
    old_start_now = os.environ.get("HERMES_GATEWAY_INSTALL_START_NOW")
    old_start_on_login = os.environ.get("HERMES_GATEWAY_INSTALL_START_ON_LOGIN")
    old_handoff = os.environ.get("HERMES_GATEWAY_ELEVATED_HANDOFF")
    try:
        if start_now is not None:
            os.environ["HERMES_GATEWAY_INSTALL_START_NOW"] = "1" if start_now else "0"
        if start_on_login is not None:
            os.environ["HERMES_GATEWAY_INSTALL_START_ON_LOGIN"] = "1" if start_on_login else "0"
        os.environ["HERMES_GATEWAY_ELEVATED_HANDOFF"] = "1"
        extra_args = ["--elevated-handoff"]
        if force:
            extra_args.append("--force")
        if start_now is not None:
            extra_args.append("--start-now" if start_now else "--no-start-now")
        if start_on_login is not None:
            extra_args.append("--start-on-login" if start_on_login else "--no-start-on-login")
        return _launch_elevated_gateway_command("install", extra_args)
    finally:
        for key, old in (
            ("HERMES_GATEWAY_INSTALL_START_NOW", old_start_now),
            ("HERMES_GATEWAY_INSTALL_START_ON_LOGIN", old_start_on_login),
            ("HERMES_GATEWAY_ELEVATED_HANDOFF", old_handoff),
        ):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def _launch_elevated_uninstall() -> bool:
    """Launch an elevated gateway uninstall via UAC and return True on handoff."""
    return _launch_elevated_gateway_command("uninstall")


# ---------------------------------------------------------------------------
# Paths: where we stash our task script and where Startup lives
# ---------------------------------------------------------------------------

def get_task_name() -> str:
    """Scheduled Task name, scoped per profile.

    Default profile: ``Hermes_Gateway``
    Named profile X: ``Hermes_Gateway_<X>``
    """
    _assert_windows()
    # Local import to avoid circular module initialization during hermes_cli boot.
    from hermes_cli.gateway import _profile_suffix

    suffix = _profile_suffix()
    if not suffix:
        return _TASK_NAME_DEFAULT
    return f"{_TASK_NAME_DEFAULT}_{suffix}"


def _sanitize_filename(value: str) -> str:
    """Remove characters illegal in Windows filenames."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)


def get_task_script_path() -> Path:
    """The generated ``gateway.cmd`` wrapper that the schtasks entry invokes.

    Lives under ``%LOCALAPPDATA%\\hermes\\gateway-service\\<task_name>.cmd``
    (or ``<HERMES_HOME>/gateway-service/<task_name>.cmd`` so per-profile
    Hermes installs stay self-contained).
    """
    _assert_windows()
    from hermes_cli.config import get_hermes_home

    script_dir = Path(get_hermes_home()) / "gateway-service"
    script_dir.mkdir(parents=True, exist_ok=True)
    return script_dir / f"{_sanitize_filename(get_task_name())}.cmd"


def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    userprofile = os.environ.get("USERPROFILE", "").strip() or os.environ.get("HOME", "").strip()
    if not userprofile:
        raise RuntimeError("neither APPDATA nor USERPROFILE is set — cannot resolve Startup folder")
    return (
        Path(userprofile)
        / "AppData"
        / "Roaming"
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )


def get_startup_entry_path() -> Path:
    _assert_windows()
    return _startup_dir() / f"{_sanitize_filename(get_task_name())}.cmd"


# ---------------------------------------------------------------------------
# Stable working directory
# ---------------------------------------------------------------------------

def _stable_gateway_working_dir(project_root: Path) -> str:
    """Return a stable cwd for detached/startup gateway runs.

    Mirror the POSIX service invariant: anchor at ``HERMES_HOME`` whenever it
    exists so Scheduled Task / Startup launches do not fail at the ``cd`` step
    after a transient checkout or worktree is moved away. Fall back to the
    source checkout only if ``HERMES_HOME`` cannot be resolved yet.
    """
    from hermes_cli.config import get_hermes_home

    try:
        home = get_hermes_home()
        if home and Path(home).is_dir():
            return str(Path(home).resolve())
    except Exception:
        pass
    return str(project_root)


# ---------------------------------------------------------------------------
# Script rendering
# ---------------------------------------------------------------------------

def _gateway_service_dir() -> Path:
    """<HERMES_HOME>/gateway-service/ — where all generated service files live."""
    _assert_windows()
    from hermes_cli.config import get_hermes_home
    d = Path(get_hermes_home()) / "gateway-service"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_default_profile() -> bool:
    """True when the current HERMES_HOME is the root (not under profiles/)."""
    return _get_profile_from_hermes_home() == ""


def _build_vbs_launcher(service_dir: Path, task_name: str) -> str:
    """VBS helper: sets env vars and launches the watchdog Python script."""
    pythonw = _vbs_escape(_derive_venv_pythonw(sys.executable or ""))
    watchdog_py = _vbs_escape(
        str(service_dir / f"{_sanitize_filename(task_name)}_watchdog.py")
    )
    hermes_parent = str(Path(get_hermes_home()) / "..").replace("/", "\\")
    hermes_parent_escaped = _vbs_escape(hermes_parent)
    venv = _vbs_escape(
        str(Path(get_hermes_home()) / "hermes-agent" / "venv")
    )
    venv_site = _vbs_escape(
        str(Path(get_hermes_home()) / "hermes-agent" / "venv" / "Lib" / "site-packages")
    )
    profile_val = _vbs_escape(_get_profile_from_hermes_home())
    lines = [
        'Set objShell = CreateObject("Wscript.Shell")',
        'Set env = objShell.Environment("PROCESS")',
        f'env("HERMES_HOME") = "{hermes_parent_escaped}"',
        'env("PYTHONIOENCODING") = "utf-8"',
        'env("HERMES_GATEWAY_DETACHED") = "1"',
        f'env("PYTHONPATH") = "{venv_site}"',
        f'env("VIRTUAL_ENV") = "{venv}"',
        f'env("HERMES_PROFILE") = "{profile_val}"',
        f'cmd = chr(34) & "{pythonw}" & chr(34) & " " & chr(34) & "{watchdog_py}" & chr(34)',
        "objShell.Run cmd, 0, False",
    ]
    return "\r\n".join(lines) + "\r\n"


def _build_vbs_direct_launcher(service_dir: Path, task_name: str) -> str:
    """VBS helper for worker profiles: sets env vars and launches the gateway directly (no watchdog)."""
    pythonw = _vbs_escape(_derive_venv_pythonw(sys.executable or ""))
    hermes_home = str(Path(get_hermes_home()).resolve())
    hermes_parent = str(Path(hermes_home) / "..").replace("/", "\\")
    hermes_parent_escaped = _vbs_escape(hermes_parent)
    venv = _vbs_escape(str(Path(hermes_home) / "hermes-agent" / "venv"))
    venv_site = _vbs_escape(str(Path(hermes_home) / "hermes-agent" / "venv" / "Lib" / "site-packages"))
    profile_val = _vbs_escape(_get_profile_from_hermes_home())
    profile_arg = _profile_arg(str(Path(hermes_home)))
    profile_arg_vbs = " " + _vbs_escape(profile_arg) + " " if profile_arg else " "
    lines = [
        'Set objShell = CreateObject("Wscript.Shell")',
        'Set env = objShell.Environment("PROCESS")',
        f'env("HERMES_HOME") = "{hermes_parent_escaped}"',
        'env("PYTHONIOENCODING") = "utf-8"',
        'env("HERMES_GATEWAY_DETACHED") = "1"',
        f'env("PYTHONPATH") = "{venv_site}"',
        f'env("VIRTUAL_ENV") = "{venv}"',
        f'env("HERMES_PROFILE") = "{profile_val}"',
        f'cmd = chr(34) & "{pythonw}" & chr(34) & " -m hermes_cli.main{profile_arg_vbs}gateway run --replace"',
        "objShell.Run cmd, 0, False",
    ]
    return "\r\n".join(lines) + "\r\n"


def _vbs_escape(s: str) -> str:
    """Escape a string for use inside a VBS double-quoted literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _get_profile_from_hermes_home() -> str:
    """Return the Hermes profile name from HERMES_HOME, or '' for default."""
    from hermes_cli.config import get_hermes_home

    home = Path(get_hermes_home())
    profiles = home.parent / "profiles"
    if profiles not in home.parents:
        return ""
    try:
        return home.relative_to(profiles).parts[0]
    except (ValueError, IndexError):
        return ""


def _write_service_files() -> Path:
    """Write all three service files. Return the VBS launcher path."""
    _assert_windows()
    from hermes_cli.config import get_hermes_home
    from hermes_cli.gateway import _profile_arg

    task_name = get_task_name()
    service_dir = _gateway_service_dir()
    hermes_home = str(Path(get_hermes_home()).resolve())

    # Dynamic segments
    profile_str = _get_profile_from_hermes_home()
    vbs_v = _build_vbs_launcher(service_dir, task_name)
    vbs_path = service_dir / f"{_sanitize_filename(task_name)}_watchdog_launcher.vbs"
    vbs_path.write_text(vbs_v, encoding="utf-8")

    # --- Watchdog Python ---------------------------------------------------
    wd_path = service_dir / f"{_sanitize_filename(task_name)}_watchdog.py"
    wd_lines = [
        "import json",
        "import os",
        "import subprocess",
        "import sys",
        "import time",
        "from pathlib import Path",
        "",
        f"HERMES_HOME = r'{hermes_home}'",
        f'PROFILE = "{profile_str}"',
        f"SERVICE_DIR = r'{service_dir}'",
        "LOG_DIR = Path(HERMES_HOME) / 'logs'",
        "LOG_DIR.mkdir(parents=True, exist_ok=True)",
        "LOG_PATH = LOG_DIR / 'gateway-watchdog.log'",
        "PID_PATH = Path(SERVICE_DIR) / 'watchdog.pid'",
        "ENABLED_PATH = Path(SERVICE_DIR) / 'autoheal.enabled'",
        "STATE_PATH = Path(SERVICE_DIR) / 'Hermes_Gateway.state.json'",
        f"LAUNCHER = r'{service_dir / f'{_sanitize_filename(task_name)}_launcher.py'}'",
        "PYTHONW = r'" + _derive_venv_pythonw(sys.executable or "") + "'",
        "",
        "def _log(msg):",
        "    ts = time.strftime('%Y-%m-%d %H:%M:%S')",
        "    with LOG_PATH.open('a', encoding='utf-8') as fh:",
        "        fh.write(f'{ts} {msg}\\n')",
        "",
        "PID_PATH.write_text(str(os.getpid()), encoding='utf-8')",
        "_log('watchdog started')",
        "ENABLED_PATH.touch()",
        "",
        "failures = 0",
        "while True:",
        "    if not ENABLED_PATH.exists():",
        "        _log('autoheal disabled; watchdog exiting')",
        "        break",
        "",
        "    try:",
        f"        sys.path.insert(0, r'{hermes_home}/hermes-agent/venv/Lib/site-packages')",
        f"        sys.path.insert(0, r'{hermes_home}/hermes-agent')",
        f"        os.chdir(r'{hermes_home}/hermes-agent')",
        "        from gateway.status import get_running_pid, is_gateway_runtime_lock_active",
        "        running = get_running_pid()",
        "        lock_active = is_gateway_runtime_lock_active()",
        "    except Exception:",
        "        running = None",
        "        lock_active = False",
        "",
        "    if running or lock_active:",
        "        failures = 0",
        "        time.sleep(30.0)",
        "        continue",
        "",
        "    _log('gateway not running; triggering launcher')",
        "    flags = 0x00000008 | 0x00000200 | 0x08000000",
        "    try:",
        "        proc = subprocess.Popen(",
        "            [PYTHONW, LAUNCHER],",
        "            creationflags=flags,",
        "            close_fds=True,",
        "            stdin=subprocess.DEVNULL,",
        "            stdout=subprocess.DEVNULL,",
        "            stderr=subprocess.DEVNULL,",
        "        )",
        "        rc = proc.wait(timeout=30)",
        "    except Exception as e:",
        "        _log(f'launcher spawn failed: {e}')",
        "        rc = 1",
        "",
        "    if rc == 0:",
        "        failures = 0",
        "        _log('launcher triggered ok')",
        "        time.sleep(8.0)",
        "        continue",
        "    failures += 1",
        "    STATE_PATH.write_text(json.dumps({'failures': failures, 'last_rc': rc}), encoding='utf-8')",
        "    _log(f'launcher failed rc={rc} failures={failures}')",
        "    time.sleep(min(60, 5 * failures))",
        "",
        "try:",
        "    PID_PATH.unlink()",
        "except FileNotFoundError:",
        "    pass",
    ]
    wd_path.write_text("\n".join(wd_lines), encoding="utf-8")

    # --- Launcher Python --------------------------------------------------
    ln_path = service_dir / f"{_sanitize_filename(task_name)}_launcher.py"
    pythonw_path = _derive_venv_pythonw(sys.executable or "")
    ln_lines = [
        "import os",
        "import subprocess",
        "import sys",
        "import time",
        "from pathlib import Path",
        "",
        f"HERMES_HOME = r'{hermes_home}'",
        "os.environ['HERMES_HOME'] = HERMES_HOME",
        "os.environ['PYTHONIOENCODING'] = 'utf-8'",
        "os.environ['HERMES_GATEWAY_DETACHED'] = '1'",
        "",
        f"VENV_SITE = Path(HERMES_HOME) / 'hermes-agent' / 'venv' / 'Lib' / 'site-packages'",
        "sys.path.insert(0, str(VENV_SITE))",
        f"sys.path.insert(0, str(Path(HERMES_HOME) / 'hermes-agent'))",
        f"os.chdir(str(Path(HERMES_HOME) / 'hermes-agent'))",
        "",
        "from gateway.status import get_running_pid, is_gateway_runtime_lock_active",
        "",
        "def _log(msg):",
        "    ts = time.strftime('%Y-%m-%d %H:%M:%S')",
        "    log_dir = Path(HERMES_HOME) / 'logs'",
        "    log_dir.mkdir(parents=True, exist_ok=True)",
        "    with (log_dir / 'gateway-launcher.log').open('a', encoding='utf-8') as fh:",
        "        fh.write(f'{ts} {msg}\\n')",
        "",
        "existing = get_running_pid()",
        "lock_active = is_gateway_runtime_lock_active()",
        "if existing or lock_active:",
        "    _log(f'already running pid={existing} lock_active={lock_active}')",
        "    raise SystemExit(0)",
        "",
        f"PYTHONW = r'{pythonw_path}'",
        "argv = [PYTHONW, '-m', 'hermes_cli.main', 'gateway', 'run', '--replace']",
        "env = dict(os.environ)",
        f"env['VIRTUAL_ENV'] = str(Path(HERMES_HOME) / 'hermes-agent' / 'venv')",
        f"env['PYTHONPATH'] = str(VENV_SITE) + os.pathsep + env.get('PYTHONPATH', '')",
        "flags = 0x00000008 | 0x00000200 | 0x08000000",
        "log_dir = Path(HERMES_HOME) / 'logs'",
        "log_dir.mkdir(parents=True, exist_ok=True)",
        "stdio_path = log_dir / 'gateway-stdio.log'",
        "with open(str(stdio_path), 'ab', buffering=0) as log_fh:",
        "    proc = subprocess.Popen(",
        "        argv,",
        "        env=env,",
        "        creationflags=flags,",
        "        close_fds=True,",
        "        stdin=subprocess.DEVNULL,",
        "        stdout=log_fh,",
        "        stderr=log_fh,",
        "    )",
        "    _log(f'gateway spawned pid={proc.pid}')",
        "    rc = proc.wait()",
        "    _log(f'gateway exited rc={rc}')",
    ]
    ln_path.write_text("\n".join(ln_lines), encoding="utf-8")

    # --- autoheal.enabled -------------------------------------------------
    (service_dir / "autoheal.enabled").touch()

    return vbs_path


def get_task_script_path() -> Path:
    """No longer used; kept for API compatibility."""
    return _gateway_service_dir() / f"{_sanitize_filename(get_task_name())}.cmd"


def _build_gateway_cmd_script(
    python_path: str,
    working_dir: str,
    hermes_home: str,
    profile_arg: str,
) -> str:
    """Deprecated. Kept for API compat only — install path no longer uses .cmd."""
    return ""


def _build_startup_launcher(vbs_path: Path) -> str:
    """Startup-folder .cmd: calls the VBS watchdog launcher silently."""
    quoted_vbs = _quote_cmd_script_arg(str(vbs_path))
    lines = [
        "@echo off",
        f"rem {_TASK_DESCRIPTION}",
        f"if not exist {quoted_vbs} exit /b 0",
        f'start "" /min cmd.exe /d /c {quoted_vbs}',
    ]
    return "\r\n".join(lines) + "\r\n"


def _install_startup_entry(vbs_path: Path) -> Path:
    """Write the Startup-folder launcher. Returns its path."""
    entry = get_startup_entry_path()
    entry.parent.mkdir(parents=True, exist_ok=True)
    tmp = entry.with_suffix(".tmp")
    tmp.write_text(_build_startup_launcher(vbs_path), encoding="utf-8", newline="")
    tmp.replace(entry)
    return entry


def _install_scheduled_task(task_name: str, script_path: Path) -> tuple[bool, str]:
    """Create or replace the Scheduled Task. Returns (success, detail).

    Always recreate instead of ``/Change``. Older Hermes builds and failed
    experiments may have left repeat/restart settings on the task; ``/Change``
    preserves those stale triggers and can make the gateway relaunch every
    minute. Delete+create gives us a clean ONLOGON task every install.
    """
    quoted_script = _quote_schtasks_arg(str(script_path))

    delete_code, delete_out, delete_err = _exec_schtasks(["/Delete", "/F", "/TN", task_name])
    delete_detail = (delete_err or delete_out or "").strip()
    if delete_code != 0 and delete_detail and "cannot find" not in delete_detail.lower():
        if _is_access_denied(delete_detail):
            return (False, f"schtasks /Delete failed (code {delete_code}): {delete_detail}")
        # Non-fatal: /Create /F below may still replace it. Keep the detail in
        # the final error if creation also fails.
    # password" variant; if that fails, retry without /RU /NP /IT.
    base = [
        "/Create",
        "/F",
        "/SC",
        "ONLOGON",
        "/RL",
        "LIMITED",
        "/TN",
        task_name,
        "/TR",
        quoted_script,
    ]
    user = _resolve_task_user()
    variants = []
    if user:
        variants.append([*base, "/RU", user, "/NP", "/IT"])
    variants.append(base)

    last_code = 1
    last_err = ""
    for argv in variants:
        code, out, err = _exec_schtasks(argv)
        if code == 0:
            return (True, f"Created Scheduled Task {task_name!r}")
        last_code, last_err = code, (err or out or "")
    if delete_detail and "cannot find" not in delete_detail.lower():
        last_err = f"{last_err.strip()} (delete detail: {delete_detail})"
    return (False, f"schtasks /Create failed (code {last_code}): {last_err.strip()}")




def _install_startup_entry(script_path: Path) -> Path:
    """Write the Startup-folder fallback launcher. Returns its path."""
    entry = get_startup_entry_path()
    entry.parent.mkdir(parents=True, exist_ok=True)
    tmp = entry.with_suffix(".tmp")
    tmp.write_text(_build_startup_launcher(script_path), encoding="utf-8", newline="")
    tmp.replace(entry)
    return entry


def _derive_venv_pythonw(python_exe: str) -> str:
    """Given a ``python.exe`` path, return the sibling ``pythonw.exe`` if present.

    ``pythonw.exe`` is the console-less variant. Using it for detached
    daemons means there's no console handle to inherit from the spawning
    shell, which is what lets the gateway survive a parent-shell exit on
    Windows. Falls back to the original ``python.exe`` if the ``w`` variant
    isn't there — caller must still set CREATE_NO_WINDOW in that case.
    """
    p = Path(python_exe)
    candidate = p.with_name(p.stem + "w" + p.suffix)
    if candidate.exists():
        return str(candidate)
    return python_exe


def _read_pyvenv_cfg(venv_dir: Path) -> dict[str, str]:
    cfg_path = venv_dir / "pyvenv.cfg"
    try:
        lines = cfg_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    parsed: dict[str, str] = {}
    for raw in lines:
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        parsed[key.strip().lower()] = value.strip()
    return parsed


def _resolve_detached_python(python_exe: str) -> tuple[str, Path, list[str]]:
    """Return (windowed_python, venv_dir, extra_pythonpath) for detached runs.

    uv-created Windows venv launchers are special: ``venv\\Scripts\\pythonw.exe``
    starts hidden, but then respawns the base interpreter as console
    ``python.exe``.  That child opens a visible Windows Terminal tab.  For uv
    venvs, use the base ``pythonw.exe`` directly and put the repo + venv
    site-packages on ``PYTHONPATH`` so imports still resolve without the venv
    launcher.
    """
    p = Path(python_exe)
    venv_dir = p.parent.parent
    windowed = _derive_venv_pythonw(python_exe)

    cfg = _read_pyvenv_cfg(venv_dir)
    home = cfg.get("home", "")
    if "uv" in cfg and home:
        base_pythonw = Path(home) / "pythonw.exe"
        site_packages = venv_dir / "Lib" / "site-packages"
        if base_pythonw.exists() and site_packages.exists():
            return (str(base_pythonw), venv_dir, [str(site_packages)])

    return (windowed, venv_dir, [])


def _prepend_pythonpath(env_overlay: dict[str, str], entries: list[str]) -> None:
    clean_entries = [entry for entry in entries if entry]
    if not clean_entries:
        return
    existing = os.environ.get("PYTHONPATH", "")
    if existing:
        clean_entries.append(existing)
    env_overlay["PYTHONPATH"] = os.pathsep.join(clean_entries)


def _build_gateway_argv() -> tuple[list[str], str, dict[str, str]]:
    """Build (argv, working_dir, env_overlay) for the gateway subprocess.

    Same logical command as what gateway.cmd runs, but assembled as a
    native argv for direct ``subprocess.Popen`` invocation — no cmd.exe
    layer in between.
    """
    _assert_windows()
    from hermes_cli.config import get_hermes_home
    from hermes_cli.gateway import (
        PROJECT_ROOT,
        _profile_arg,
        get_python_path,
    )

    python_exe, venv_dir, extra_pythonpath = _resolve_detached_python(get_python_path())
    project_root = str(PROJECT_ROOT)
    working_dir = _stable_gateway_working_dir(PROJECT_ROOT)
    hermes_home = str(Path(get_hermes_home()).resolve())
    profile_arg = _profile_arg(hermes_home)

    argv = [python_exe, "-m", "hermes_cli.main"]
    if profile_arg:
        argv.extend(profile_arg.split())
    argv.extend(["gateway", "run"])

    env_overlay = {
        "HERMES_HOME": hermes_home,
        "PYTHONIOENCODING": "utf-8",
        "HERMES_GATEWAY_DETACHED": "1",
        "VIRTUAL_ENV": str(venv_dir),
    }
    _prepend_pythonpath(env_overlay, [project_root, *extra_pythonpath] if extra_pythonpath else [project_root])
    return argv, working_dir, env_overlay


def _spawn_detached(script_path: Path | None = None) -> int:
    """Launch the gateway as a fully detached background process.

    We spawn ``pythonw.exe -m hermes_cli.main gateway run``
    directly — NOT through a cmd.exe shim — because on Windows a cmd.exe
    child inherits the parent session's console handle and tends to get
    reaped when the spawning shell exits. pythonw.exe has no console, and
    combined with DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP |
    CREATE_NO_WINDOW + DEVNULL stdio + a fresh env, the resulting process
    is independent of whichever shell started it.

    Arg ``script_path`` is accepted for API symmetry with older callers
    but ignored — we don't need it now that we go direct.

    Returns the spawned PID so callers can verify the process actually
    came up.
    """
    _assert_windows()
    argv, working_dir, env_overlay = _build_gateway_argv()

    # Inherit PATH etc. from the current env, overlay our required vars.
    env = {**os.environ, **env_overlay}

    # DETACHED_PROCESS        0x00000008  — no console attached to child
    # CREATE_NEW_PROCESS_GROUP 0x00000200 — child gets its own group, won't
    #                                       receive Ctrl+C from our group
    # CREATE_NO_WINDOW         0x08000000 — belt-and-braces no-console flag
    # CREATE_BREAKAWAY_FROM_JOB 0x01000000 — escape any job object the
    #                                       parent is in (prevents parent-
    #                                       job teardown from reaping us;
    #                                       some Windows Terminal versions
    #                                       wrap their children in a job).
    flags = 0x00000008 | 0x00000200 | 0x08000000 | 0x01000000

    # Redirect any stray stdout/stderr output to a sidecar log. Python's
    # logging module writes to gateway.log through a FileHandler, so the
    # real gateway logs still land there — this just captures anything
    # that goes to print() or native stderr.
    from hermes_cli.config import get_hermes_home

    log_dir = Path(get_hermes_home()) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stray_log = log_dir / "gateway-stdio.log"

    try:
        with open(stray_log, "ab", buffering=0) as log_fh:
            proc = subprocess.Popen(
                argv,
                cwd=working_dir,
                env=env,
                creationflags=flags,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=log_fh,
            )
    except OSError:
        # CREATE_BREAKAWAY_FROM_JOB can fail with "access denied" when the
        # parent's job object doesn't permit breakaway (some Windows
        # Terminal configs). Retry without the breakaway flag — in most
        # setups pythonw.exe + DETACHED_PROCESS is enough on its own.
        flags_no_breakaway = flags & ~0x01000000
        with open(stray_log, "ab", buffering=0) as log_fh:
            proc = subprocess.Popen(
                argv,
                cwd=working_dir,
                env=env,
                creationflags=flags_no_breakaway,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=log_fh,
            )
    except OSError:
        # CREATE_BREAKAWAY_FROM_JOB can fail with "access denied" when the
        # parent's job object doesn't permit breakaway (some Windows
        # Terminal configs). Retry without the breakaway flag — in most
        # setups pythonw.exe + DETACHED_PROCESS is enough on its own.
        flags_no_breakaway = flags & ~0x01000000
        with open(stray_log, "ab", buffering=0) as log_fh:
            proc = subprocess.Popen(
                argv,
                cwd=working_dir,
                env=env,
                creationflags=flags_no_breakaway,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=log_fh,
            )
    return proc.pid
    entry = _install_startup_entry(vbs_path)
    print(f"✓ Installed Windows login item: {entry}")
    print(f"  VBS launcher: {vbs_path}")

    from hermes_cli.gateway import find_gateway_pids, _profile_arg

    running_pids = list(find_gateway_pids())
    if running_pids:
        print(f"✓ Gateway already running")
    elif start_now:
        pid = _spawn_detached()
        _report_gateway_start(f"direct spawn (PID {pid})")
    else:
        profile_arg = _profile_arg()
        start_cmd = f"hermes {profile_arg} gateway start" if profile_arg else "hermes gateway start"
        print("ℹ Startup item installed; gateway not started now.")
        print(f"  Start manually with: {start_cmd}")
    _print_next_steps()


def install(
    force: bool = False,
    *,
    start_now: bool | None = None,
    start_on_login: bool | None = None,
    elevated_handoff: bool = False,
) -> None:
    """Install gateway auto-start via Startup folder.

    - Default profile: Startup .cmd → VBS → watchdog → launcher → gateway.
      Watchdog auto-heals every 30 s (delete autoheal.enabled to disable).
    - Worker profiles: Startup .cmd → VBS → gateway directly (no watchdog).

    No Scheduled Task, no administrator privileges needed.
    Idempotent: re-running rewrites all service files and the Startup entry.
    """
    _assert_windows()
    del force, elevated_handoff  # unused on Windows

    from hermes_cli.setup import prompt_yes_no
    from hermes_cli.gateway import find_gateway_pids

    is_default = _is_default_profile()
    running = list(find_gateway_pids())
    if start_now is None:
        if running:
            print(f"✓ Gateway already running (PIDs: {running})")
            start_now = False
        else:
            start_now = prompt_yes_no("Start the gateway now?", True)
    elif running:
        print(f"✓ Gateway already running (PIDs: {running})")
        start_now = False

    service_dir = _gateway_service_dir()
    task_name = get_task_name()
    task_sanitized = _sanitize_filename(task_name)

    if is_default:
        # === Default profile: full watchdog chain ===
        print("Writing service files to <HERMES_HOME>/gateway-service/ ...")
        vbs_path = _write_service_files()
        print(f"  ✓ VBS launcher:  {vbs_path}")
        wd_path = service_dir / f"{task_sanitized}_watchdog.py"
        ln_path = service_dir / f"{task_sanitized}_launcher.py"
        print(f"  ✓ Watchdog:      {wd_path}")
        print(f"  ✓ Launcher:       {ln_path}")
        print("Installing Startup folder entry ...")
        entry = _install_startup_entry(vbs_path)
        print(f"  ✓ Startup entry:  {entry}")
        print()
        print("✓ Gateway auto-start installed (Startup folder + watchdog).")
        print("  Next login: Startup .cmd → VBS → watchdog → launcher → gateway")
        print("  Autoheal: watchdog checks every 30 s; delete autoheal.enabled to disable")
    else:
        # === Worker profile: direct gateway launch (no watchdog) ===
        print(f"Writing direct launcher for worker profile '{_get_profile_from_hermes_home()}' ...")
        vbs_content = _build_vbs_direct_launcher(service_dir, task_name)
        vbs_path = service_dir / f"{task_sanitized}_direct_launcher.vbs"
        vbs_path.write_text(vbs_content, encoding="utf-8")
        print(f"  ✓ VBS launcher:  {vbs_path}")
        print("Installing Startup folder entry ...")
        entry = _install_startup_entry(vbs_path)
        print(f"  ✓ Startup entry:  {entry}")
        print()
        print(f"✓ Worker profile gateway auto-start installed.")
        print(f"  Next login: Startup .cmd → VBS → gateway (profile: {_get_profile_from_hermes_home()})")
        print("  No watchdog on worker profiles.")

    if start_now and not running:
        pid = _spawn_detached()
        _report_gateway_start(f"direct spawn (PID {pid})")
    elif not start_now and not running:
        print("ℹ Gateway not started now. Start manually with: hermes gateway start")

    _print_next_steps()


def _wait_for_gateway_ready(timeout_s: float = 6.0, interval_s: float = 0.4) -> list[int]:
    """Poll for a live gateway process for up to ``timeout_s`` seconds.

    Returns the list of PIDs found. Empty list means nothing came up in
    time — the caller should surface that to the user as a failed start.
    """
    from hermes_cli.gateway import find_gateway_pids

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pids = list(find_gateway_pids())
        if pids:
            return pids
        time.sleep(interval_s)
    return []


def _report_gateway_start(via: str) -> None:
    pids = _wait_for_gateway_ready()
    if pids:
        print(f"✓ Gateway started via {via} ")
    else:
        print(f"⚠ Launched gateway via {via}, but no process detected after 6s.")
        print("  Check the log for startup errors:")
        from hermes_cli.config import get_hermes_home
        print(f"    type {Path(get_hermes_home()).resolve()}\\logs\\gateway.log")
        print(f"    type {Path(get_hermes_home()).resolve()}\\logs\\gateway-stdio.log")


def _print_next_steps() -> None:
    from hermes_cli.config import get_hermes_home

    hermes_home = Path(get_hermes_home()).resolve()
    print()
    print("Next steps:")
    print("  hermes gateway status                      # Check status")
    print(f"  type {hermes_home}\\logs\\gateway.log       # View logs")


def uninstall() -> None:
    """Remove the Startup-folder entry and all gateway-service/ files."""
    _assert_windows()
    task_name = get_task_name()
    service_dir = _gateway_service_dir()
    startup_entry = get_startup_entry_path()

    # Startup folder entry
    try:
        startup_entry.unlink()
        print(f"✓ Removed Startup entry: {startup_entry}")
    except FileNotFoundError:
        print(f"  (Startup entry not found — already clean)")

    # All gateway-service files for this profile
    for pattern in [
        f"{_sanitize_filename(task_name)}_watchdog_launcher.vbs",
        f"{_sanitize_filename(task_name)}_watchdog.py",
        f"{_sanitize_filename(task_name)}_launcher.py",
        f"{_sanitize_filename(task_name)}_direct_launcher.vbs",
        "autoheal.enabled",
        "watchdog.pid",
        "Hermes_Gateway.state.json",
        "Hermes_Gateway.xml",
    ]:
        try:
            (service_dir / pattern).unlink()
            print(f"✓ Removed: {service_dir / pattern}")
        except FileNotFoundError:
            pass

    # Old .cmd wrapper (if any from previous install)
    try:
        (service_dir / f"{_sanitize_filename(task_name)}.cmd").unlink()
        print(f"✓ Removed legacy .cmd wrapper")
    except FileNotFoundError:
        pass

    print("✓ Uninstall complete.")


# ---------------------------------------------------------------------------
# Status / start / stop / restart
# ---------------------------------------------------------------------------

def is_task_registered() -> bool:
    code, _out, _err = _exec_schtasks(["/Query", "/TN", get_task_name()])
    return code == 0


def is_startup_entry_installed() -> bool:
    return get_startup_entry_path().exists()


def is_installed() -> bool:
    """True when the Startup folder entry is present."""
    return is_startup_entry_installed()


def query_task_status() -> dict[str, str]:
    """Return watchdog/gateway status info from local files."""
    from hermes_cli.config import get_hermes_home

    home = Path(get_hermes_home())
    service_dir = home / "gateway-service"
    watchdog_log = home / "logs" / "gateway-watchdog.log"
    state_file = service_dir / "Hermes_Gateway.state.json"
    watchdog_pid = service_dir / "watchdog.pid"
    autoheal = service_dir / "autoheal.enabled"

    info: dict[str, str] = {}
    if autoheal.exists():
        info["autoheal"] = "enabled"
    else:
        info["autoheal"] = "disabled"
    if watchdog_pid.exists():
        info["watchdog_pid"] = watchdog_pid.read_text(encoding="utf-8").strip()
    if state_file.exists():
        import json
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            info["launcher_failures"] = str(state.get("failures", 0))
        except Exception:
            pass
    if watchdog_log.exists():
        try:
            lines = watchdog_log.read_text(encoding="utf-8").strip().splitlines()
            if lines:
                info["watchdog_last_log"] = lines[-1]
        except Exception:
            pass
    return info


def _gateway_pids() -> list[int]:
    """Reuse the cross-platform PID scanner in gateway.py."""
    from hermes_cli.gateway import find_gateway_pids

    return list(find_gateway_pids())


def _print_deep_probes() -> None:
    """Print PASS/FAIL per individual probe of gateway liveness.

    The default ``status`` output collapses several signals into one
    ✓ / ✗ line, which is great when they agree and confusing when they
    don't. The deep-probe block shows each underlying check independently
    so the user can see exactly which signal is wrong.

    Probes:
      [1] PID file present
      [2] Lock file present and held by some process
      [3] gateway.status.get_running_pid() returns a PID
      [4] _pid_exists(pid) — OS confirms the process is alive
      [5] gateway_state.json exists and parses (and is fresh-ish)
      [6] Last lifecycle event in gateway-exit-diag.log
    """
    import json
    from datetime import datetime, timezone

    from hermes_cli.config import get_hermes_home

    home = Path(get_hermes_home()).resolve()
    pid_path = home / "gateway.pid"
    lock_path = home / "gateway.lock"
    state_path = home / "gateway_state.json"
    diag_path = home / "logs" / "gateway-exit-diag.log"

    print()
    print("Deep probes:")

    def _mark(ok: bool) -> str:
        return "PASS" if ok else "FAIL"

    # [1] PID file
    pid_exists = pid_path.exists()
    pid_value: int | None = None
    if pid_exists:
        try:
            data = json.loads(pid_path.read_text(encoding="utf-8"))
            pid_value = int(data.get("pid")) if data.get("pid") is not None else None
            print(f"  [1] {_mark(True):4s}  PID file present: {pid_path} (pid={pid_value})")
        except Exception as exc:
            print(f"  [1] {_mark(False):4s}  PID file present but unreadable: {exc}")
    else:
        print(f"  [1] {_mark(False):4s}  PID file missing: {pid_path}")

    # [2] Lock file present + held
    lock_held = False
    lock_present = lock_path.exists()
    if lock_present:
        try:
            from gateway.status import is_gateway_runtime_lock_active

            lock_held = is_gateway_runtime_lock_active(lock_path)
            print(f"  [2] {_mark(lock_held):4s}  Lock file held by a live process: {lock_path}")
        except Exception as exc:
            print(f"  [2] {_mark(False):4s}  Could not probe lock: {exc}")
    else:
        print(f"  [2] {_mark(False):4s}  Lock file missing: {lock_path}")

    # [3] get_running_pid()
    running_pid: int | None = None
    try:
        from gateway.status import get_running_pid

        running_pid = get_running_pid(cleanup_stale=False)
        print(f"  [3] {_mark(running_pid is not None):4s}  get_running_pid() => {running_pid}")
    except Exception as exc:
        print(f"  [3] {_mark(False):4s}  get_running_pid() raised: {exc!r}")

    # [4] _pid_exists() on the probed PID
    candidate_pid = running_pid if running_pid is not None else pid_value
    if candidate_pid is not None:
        try:
            from gateway.status import _pid_exists

            alive = bool(_pid_exists(candidate_pid))
            print(f"  [4] {_mark(alive):4s}  _pid_exists({candidate_pid}) => {alive}")
        except Exception as exc:
            print(f"  [4] {_mark(False):4s}  _pid_exists raised: {exc!r}")
    else:
        print(f"  [4] {_mark(False):4s}  No candidate PID to verify")

    # [5] runtime status file
    if state_path.exists():
        try:
            state_data = json.loads(state_path.read_text(encoding="utf-8"))
            gateway_state = state_data.get("gateway_state")
            updated_at = state_data.get("updated_at")
            age_str = ""
            if updated_at:
                try:
                    updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    age_seconds = int((now - updated_dt).total_seconds())
                    age_str = f" (updated {age_seconds}s ago)"
                except Exception:
                    pass
            ok = gateway_state == "running"
            print(f"  [5] {_mark(ok):4s}  gateway_state.json state={gateway_state!r}{age_str}")
        except Exception as exc:
            print(f"  [5] {_mark(False):4s}  gateway_state.json present but unreadable: {exc}")
    else:
        print(f"  [5] {_mark(False):4s}  gateway_state.json missing: {state_path}")

    # [6] Last lifecycle event from the exit-diag log
    if diag_path.exists():
        try:
            with open(diag_path, "rb") as fh:
                # Read last ~4KB; one event is well under 500 bytes.
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 4096))
                tail = fh.read().decode("utf-8", errors="replace").splitlines()
            last_event = next((ln for ln in reversed(tail) if ln.strip()), "")
            if last_event:
                try:
                    event = json.loads(last_event)
                    tag = event.get("tag", "?")
                    pid = event.get("pid", "?")
                    ts = event.get("ts", "?")
                    healthy = tag in ("gateway.start",)
                    print(f"  [6] {_mark(healthy):4s}  Last lifecycle event: tag={tag} pid={pid} ts={ts}")
                except Exception:
                    print(f"  [6] {_mark(False):4s}  Last lifecycle line not JSON: {last_event[:120]}")
            else:
                print(f"  [6] {_mark(False):4s}  exit-diag log empty: {diag_path}")
        except Exception as exc:
            print(f"  [6] {_mark(False):4s}  exit-diag log unreadable: {exc}")
    else:
        print(f"  [6] {_mark(False):4s}  exit-diag log missing: {diag_path}")


def status(deep: bool = False) -> None:
    """Print a status report for the Windows gateway service."""
    _assert_windows()
    task_name = get_task_name()
    startup_installed = is_startup_entry_installed()
    pids = _gateway_pids()

    if startup_installed:
        print(f"✓ Windows login item installed: {get_startup_entry_path()}")
    else:
        print("✗ Gateway service not installed (no Startup folder entry)")

    if pids:
        print(f"✓ Gateway process running (PIDs: {pids})")
    else:
        print("✗ No gateway process detected")

    info = query_task_status()
    if info:
        for key, val in info.items():
            print(f"  {key}: {val}")

    if deep:
        print()
        print(f"  Task name:        {task_name}")
        print(f"  Service dir:     {_gateway_service_dir()}")
        print(f"  Startup entry:  {get_startup_entry_path()}")
        _print_deep_probes()

    if not startup_installed and not pids:
        print()
        print("To install:")
        print("  hermes gateway install")


def start() -> None:
    """Start the gateway.

    Always attempts to spawn the gateway directly, even if no Startup-folder
    entry is registered.  Use ``install`` to set up persistent auto-start
    on login; use ``start`` / ``stop`` for manual on/off without requiring
    the service.
    """
    _assert_windows()
    running_pids = _gateway_pids()
    if running_pids:
        print(f"✓ Gateway already running (PIDs: {running_pids})")
        return

    # Direct spawn — works regardless of whether a service entry is registered.
    pid = _spawn_detached()
    _report_gateway_start(f"direct spawn (PID {pid})")


def _drain_gateway_pid(pid: int, drain_timeout: float) -> bool:
    """Write the planned-stop marker and wait for the gateway PID to exit.

    Windows cannot deliver POSIX signals to a Python asyncio loop
    (``loop.add_signal_handler`` raises NotImplementedError), so writing
    the marker is the ONLY way to ask a running gateway to drain
    in-flight agents and persist ``resume_pending`` before exit. The
    gateway's planned-stop watcher thread (gateway/run.py) polls for
    the marker and drives the same shutdown path the SIGTERM handler
    would have on POSIX.

    Returns True if the PID exited within the timeout, False if it
    didn't (caller should escalate to schtasks /End + taskkill).
    """
    if pid <= 0:
        return False
    try:
        from gateway.status import write_planned_stop_marker, _pid_exists
    except ImportError:
        return False

    try:
        write_planned_stop_marker(pid)
    except Exception:
        # Best-effort: if the marker can't be written, we have no choice
        # but to fall through to a hard kill.  Caller decides escalation.
        pass

    deadline = time.monotonic() + max(drain_timeout, 1.0)
    while time.monotonic() < deadline:
        if not _pid_exists(pid):
            return True
        time.sleep(0.5)
    return False


def stop() -> None:
    """Stop the gateway.

    Writes the planned-stop marker first so the gateway can drain
    in-flight agents and persist ``resume_pending`` before exit (the
    gateway's marker-watcher thread picks this up — Windows asyncio
    can't deliver SIGTERM to the loop, so the marker is our only IPC).
    Then escalates: ``schtasks /End`` (kills the scheduled-task tree)
    + ``kill_gateway_processes(force=True)`` for any strays.
    """
    _assert_windows()
    from hermes_cli.gateway import kill_gateway_processes, _get_restart_drain_timeout
    from gateway.status import get_running_pid

    # Phase 1: ask the running gateway (if any) to drain itself by writing
    # the planned-stop marker, then wait briefly for it to exit cleanly.
    # On clean exit, sessions land with resume_pending=True and the next
    # boot will auto-resume them.
    pid = get_running_pid()
    drained = False
    if pid is not None:
        try:
            drain_timeout = float(_get_restart_drain_timeout() or 30.0)
        except Exception:
            drain_timeout = 30.0
        drained = _drain_gateway_pid(pid, drain_timeout)

    stopped_any = drained
    if drained:
        pass  # clean drain succeeded

    # Phase 3: hard-kill any strays.  When drain succeeded this is a no-op;
    # when drain timed out this is the escalation that ensures the PID
    # actually exits.  Use force=True on Windows so taskkill /T /F walks
    # the descendant tree (browser helpers, etc.).
    killed = kill_gateway_processes(all_profiles=False, force=not drained)
    if killed:
        stopped_any = True
        print(f"✓ Killed {killed} gateway process(es)")
    if stopped_any:
        if drained:
            print("✓ Gateway stopped (drained cleanly)")
        else:
            print("✓ Gateway stopped")
    else:
        print("✗ No gateway was running")


def restart() -> None:
    """Stop the gateway then start it again."""
    _assert_windows()
    stop()
    # Give Windows a moment to release the listening port.
    time.sleep(1.0)
    start()
