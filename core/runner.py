import os
import shutil
import subprocess
import threading


class Result:
    def __init__(self, code, out=""):
        self.code = code
        self.out = out

    @property
    def ok(self):
        return self.code == 0

    def hit(self, *needles):
        text = self.out.lower()
        return any(needle.lower() in text for needle in needles)


class Runner:
    def __init__(self, on_line=None, sudo_password=""):
        self.on_line = on_line
        self.sudo_password = sudo_password or ""
        self.cancelled = threading.Event()
        self.process = None
        self.env = dict(
            os.environ,
            DEBIAN_FRONTEND="noninteractive",
            LANG="C.UTF-8",
            LC_ALL="C.UTF-8",
            PYTHONIOENCODING="utf-8",
            APT_LISTCHANGES_FRONTEND="none",
        )

    def emit(self, text, level="out"):
        if text and self.on_line:
            self.on_line(text, level)

    def run(self, cmd, timeout=300, sudo=False, quiet=False):
        if self.cancelled.is_set():
            return Result(130, "cancelled")
        feed = ""
        if sudo and self.sudo_password:
            # 无 tty 的服务进程无法复用 SSH 会话的 sudo 时间戳，
            # 只能用 -S 从 stdin 读密码（每次都会刷新时间戳）。
            full = "sudo -S -p '' " + cmd
            feed = self.sudo_password + "\n"
        elif sudo:
            full = "sudo -n " + cmd
        else:
            full = cmd
        if not quiet:
            self.emit("$ " + full, "cmd")
        return self._execute(full, timeout, feed)

    def which(self, name):
        return shutil.which(name) is not None

    def _execute(self, cmd, timeout, feed=""):
        lines = []
        try:
            self.process = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE if feed else subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=self.env,
            )
        except OSError as exc:
            self.emit(str(exc), "err")
            return Result(127, str(exc))

        if feed and self.process.stdin:
            try:
                self.process.stdin.write(feed)
                self.process.stdin.flush()
                self.process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        def pump():
            for raw in self.process.stdout:
                line = raw.rstrip("\n")
                lines.append(line)
                self.emit(line, "out")

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.emit("命令超时, 已强制结束: " + cmd, "warn")
            self.process.kill()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        reader.join(timeout=5)
        code = self.process.returncode if self.process.returncode is not None else 1
        self.process = None
        output = "\n".join(lines)
        if code != 0 and ("password is required" in output or "需要密码" in output):
            self.emit("sudo 需要密码: 请在批量部署里填写该设备的 Sudo 密码", "err")
        elif code != 0 and ("incorrect password" in output or "Sorry, try again" in output):
            self.emit("sudo 密码错误: 请检查该设备的 Sudo 密码", "err")
        return Result(code, output)

    def cancel(self):
        self.cancelled.set()
        proc = self.process
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def wait(self, seconds):
        return self.cancelled.wait(seconds)
