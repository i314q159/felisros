import os
import shlex
import signal
import subprocess
import time

PATTERN = "[a]pp.py --no-browser"
UNIT = "felisros.service"
UNIT_DIR = ".config/systemd/user"


def stop_command():
    return (
        "pkill -f '{pattern}' >/dev/null 2>&1 || true; "
        "if [ -f \"$HOME/{dir}/{unit}\" ]; then "
        "XDG_RUNTIME_DIR=\"${{XDG_RUNTIME_DIR:-/run/user/$(id -u)}}\" "
        "systemctl --user disable --now {unit} >/dev/null 2>&1 || true; "
        "rm -f \"$HOME/{dir}/{unit}\"; "
        "XDG_RUNTIME_DIR=\"${{XDG_RUNTIME_DIR:-/run/user/$(id -u)}}\" "
        "systemctl --user daemon-reload >/dev/null 2>&1 || true; "
        "fi; "
        "crontab -l 2>/dev/null | grep -v 'felisros --no-browser' | crontab - 2>/dev/null || true"
    ).format(pattern=PATTERN, dir=UNIT_DIR, unit=UNIT)


def start_command(launcher, port):
    wait = (
        "for _i in 1 2 3 4 5 6 7 8 9 10; do "
        "pgrep -f '{pattern}' >/dev/null 2>&1 || break; sleep 0.3; done; "
    ).format(pattern=PATTERN)
    start = (
        "if command -v setsid >/dev/null 2>&1; then "
        "setsid {launcher} --no-browser --port {port} </dev/null >/dev/null 2>&1 & "
        "else "
        "nohup {launcher} --no-browser --port {port} </dev/null >/dev/null 2>&1 & "
        "fi"
    ).format(launcher=shlex.quote(launcher), port=int(port))
    return "{stop}; {wait}{start}".format(stop=stop_command(), wait=wait, start=start)


def _foreground_pids():
    """找出直接运行 app.py 的前台实例（含不带 --no-browser 的启动）。"""
    pids = []
    self_pid = os.getpid()
    parent_pid = os.getppid()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid in (self_pid, parent_pid):
            continue
        try:
            with open("/proc/{}/cmdline".format(pid), "rb") as handle:
                raw = handle.read().decode("utf-8", "replace")
        except OSError:
            continue
        parts = [item for item in raw.split("\x00") if item]
        if not parts:
            continue
        script = None
        for item in parts:
            if item.endswith("app.py"):
                script = item
        if not script:
            continue
        if "--stop" in parts or "--check-update" in parts or "--apply-update" in parts:
            continue
        try:
            cwd = os.readlink("/proc/{}/cwd".format(pid))
        except OSError:
            cwd = ""
        if "felisros" not in script and "felisros" not in cwd:
            continue
        pids.append(pid)
    return pids


def _terminate(pids):
    stopped = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
        stopped.append(pid)
    deadline = time.time() + 5
    while time.time() < deadline:
        alive = []
        for pid in stopped:
            try:
                os.kill(pid, 0)
            except OSError:
                continue
            alive.append(pid)
        stopped = alive
        if not stopped:
            break
        time.sleep(0.2)
    for pid in stopped:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return stopped


def stop_local():
    if os.name == "nt":
        return False, "Windows 控制端没有本机 felisros 服务可停止"
    try:
        running = (
            subprocess.run(
                ["pgrep", "-f", PATTERN],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )
    except OSError:
        running = False
    try:
        subprocess.run(
            ["sh", "-c", stop_command()],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "停止失败: {}".format(exc)
    foreground = []
    try:
        foreground = _foreground_pids()
    except OSError:
        foreground = []
    if foreground:
        _terminate(foreground)
    if running or foreground:
        return True, "已停止正在运行的 felisros"
    return True, "没有正在运行的 felisros 服务"