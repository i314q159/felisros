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
    def __init__(self, on_line=None):
        self.on_line = on_line
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
        full = "sudo -n " + cmd if sudo else cmd
        if not quiet:
            self.emit("$ " + full, "cmd")
        return self._execute(full, timeout)

    def which(self, name):
        return shutil.which(name) is not None

    def _execute(self, cmd, timeout):
        lines = []
        try:
            self.process = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=self.env,
            )
        except OSError as exc:
            self.emit(str(exc), "err")
            return Result(127, str(exc))

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
            self.emit("sudo 需要密码: 请用 sudo 启动 felisros, 或先在终端执行 sudo -v 缓存凭证", "err")
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
