import hashlib
import io
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import tarfile
import tempfile

from . import release, service

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMOTE_BASE = "/tmp/felisros-batch"
REPORT_MARK = "FELISROS_REPORT "

REMOTE_CONTROL = """python3 - __CFG__ __PORT__ __VERS__ __SUDO__ <<'PY'
import json
import sys
import time
import urllib.request

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

cfg_path = sys.argv[1]
port = int(sys.argv[2])
versions_path = sys.argv[3] or None
sudo_path = sys.argv[4] if len(sys.argv) > 4 else ""
base = "http://127.0.0.1:%d" % port


def request(path, data=None):
    if data is None:
        req = urllib.request.Request(base + path)
    else:
        req = urllib.request.Request(
            base + path,
            data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def emit(report):
    print("FELISROS_REPORT " + json.dumps(report, ensure_ascii=False), flush=True)


try:
    config = json.load(open(cfg_path, encoding="utf-8"))
    started = time.time()
    if versions_path:
        manifest = json.load(open(versions_path, encoding="utf-8"))
        request("/api/versions/import", {"text": json.dumps(manifest, ensure_ascii=False)})
    body = dict(config)
    if sudo_path:
        try:
            with open(sudo_path, encoding="utf-8") as handle:
                body["sudo_password"] = handle.read().strip()
        except OSError:
            pass
    answer = request("/api/start", body)
    if not answer.get("ok"):
        reason = str(answer.get("message") or "安装未能启动")
        print("[felisros] " + reason, flush=True)
        emit({"ok": False, "reason": reason, "elapsed": round(time.time() - started, 1)})
        raise SystemExit(1)
    print("[felisros] 配置已下发，开始安装 " + str(config.get("version", "auto")), flush=True)
    seq = 0
    done = None
    while time.time() - started < 5400:
        feed = request("/api/events?since=%d" % seq)
        for event in feed.get("events", []):
            seq = event.get("seq", seq)
            kind = event.get("type")
            if kind == "log":
                print("[ros] " + str(event.get("text", "")), flush=True)
            elif kind == "step":
                print("[ros] * " + str(event.get("step", "")), flush=True)
            elif kind == "done":
                done = event
        if done:
            break
        time.sleep(1)
    result = (done or {}).get("result") or {}
    report = {
        "ok": bool((done or {}).get("ok")),
        "elapsed": round(time.time() - started, 1),
        "version": result.get("version", ""),
        "kind": result.get("kind", ""),
        "package": result.get("package", ""),
        "reason": result.get("reason", ""),
        "config_name": result.get("config_name", ""),
        "sources": [s.get("mirror", "") for s in result.get("sources", []) if isinstance(s, dict)],
    }
    if not done:
        report["ok"] = False
        report["reason"] = "安装超时"
    emit(report)
    raise SystemExit(0 if report["ok"] else 1)
except SystemExit:
    raise
except Exception as exc:
    emit({"ok": False, "elapsed": 0, "reason": "遥控失败: " + str(exc)})
    raise SystemExit(1)
PY
"""


REMOTE_UPDATE = """python3 - __SEED__ __PORT__ <<'PY'
import json
import sys
import time
import urllib.request

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

seed = sys.argv[1]
port = int(sys.argv[2])
base = "http://127.0.0.1:%d" % port
started = time.time()


def request(path, data=None):
    if data is None:
        req = urllib.request.Request(base + path)
    else:
        req = urllib.request.Request(
            base + path,
            data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def emit(report):
    print("FELISROS_REPORT " + json.dumps(report, ensure_ascii=False), flush=True)


def done(ok, version, reason):
    emit({
        "ok": ok,
        "version": version,
        "reason": reason,
        "elapsed": round(time.time() - started, 1),
        "kind": "update",
        "package": "felisros-linux.tar.gz",
    })


try:
    check = request("/api/update/check", {"seed": seed})
    if not check.get("available"):
        done(True, check.get("version", ""), "已是最新版本")
        raise SystemExit(0)
    applied = request("/api/update/apply", {
        "seed": check.get("seed") or seed,
        "sha256": check.get("sha256") or "",
        "restart": False,
    })
    if not applied.get("ok"):
        done(False, "", str(applied.get("reason") or "更新失败"))
        raise SystemExit(1)
    done(True, applied.get("version") or check.get("version", ""), "更新完成")
    raise SystemExit(0)
except SystemExit:
    raise
except Exception as exc:
    done(False, "", "更新失败: " + str(exc))
    raise SystemExit(1)
PY
"""


def parse_report(output):
    for line in reversed((output or "").splitlines()):
        if line.startswith(REPORT_MARK):
            try:
                item = json.loads(line[len(REPORT_MARK):])
            except ValueError:
                continue
            if isinstance(item, dict):
                return item
    return None


class RemoteError(Exception):
    pass


def redact(text, secrets):
    """把密码从日志里抹掉，但只匹配独立出现的整段。

    直接 str.replace 会把用户名里的同名前缀也误伤：密码 admin 时
    admin1@host 会变成 ******1@host，日志因此无法辨认。这里要求
    命中位置左右都不是字母数字，并且优先替换较长的密码。
    """
    safe = str(text or "")
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        safe = re.sub(
            r"(?<![0-9A-Za-z]){}(?![0-9A-Za-z])".format(re.escape(secret)), "******", safe
        )
    return safe


def package_archive():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name in ("app.py", "build.py", "core", "web", "examples", "packaging"):
            archive.add(os.path.join(ROOT, name), arcname=name)
    return buffer.getvalue()


def describe_arch(machine, dpkg_arch):
    text = "{} {}".format(machine or "", dpkg_arch or "").lower()
    if "x86_64" in text or "amd64" in text:
        return "amd64 (x86-64)"
    if "aarch64" in text or "arm64" in text:
        return "arm64 (ARM 64-bit)"
    if "armv7" in text or "armhf" in text:
        return "armhf (ARM 32-bit)"
    if "i386" in text or "i686" in text:
        return "i386 (32-bit x86)"
    return (machine or "unknown") + " / " + (dpkg_arch or "unknown")


class SshClient:
    def __init__(self, device, on_line=None):
        self.host = device.get("ip") or ""
        self.user = device.get("username") or "root"
        self.password = device.get("password") or ""
        self.sudo_password = device.get("sudo_password") or self.password
        self.port = int(device.get("port") or 22)
        self.on_line = on_line
        self.helper = None

    def emit(self, text, level="out"):
        if not text or not self.on_line:
            return
        self.on_line(redact(text, (self.password, self.sudo_password)), level)

    def _env(self):
        env = dict(os.environ)
        if not self.password:
            env["SSH_ASKPASS"] = ""
            env["SSH_ASKPASS_REQUIRE"] = ""
            return env
        if os.name == "nt":
            # Windows 的 ssh.exe 用 CreateProcessW 启动 SSH_ASKPASS，
            # shell 脚本会报 error:193，必须生成 .cmd 批处理；
            # 密码通过环境变量 + 延迟展开传递，避免特殊字符被 cmd 解析。
            handle, path = tempfile.mkstemp(prefix="felisros-ask-", suffix=".cmd")
            with os.fdopen(handle, "w", encoding="utf-8") as script:
                script.write(
                    "@echo off\r\n"
                    "@setlocal enabledelayedexpansion\r\n"
                    "@echo !FELISROS_ASKPASS!\r\n"
                )
            self.helper = path
            env["FELISROS_ASKPASS"] = self.password
        else:
            handle, path = tempfile.mkstemp(prefix="felisros-ask-", suffix=".sh")
            with os.fdopen(handle, "w", encoding="utf-8") as script:
                script.write("#!/bin/sh\nprintf '%s\\n' {}\n".format(shlex.quote(self.password)))
            os.chmod(path, 0o700)
            self.helper = path
        env["SSH_ASKPASS"] = path
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env["DISPLAY"] = env.get("DISPLAY") or ":0"
        return env

    def _parts(self, remote_cmd):
        parts = [
            "ssh",
            "-T",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=10",
            "-p",
            str(self.port),
            "{}@{}".format(self.user, self.host),
            remote_cmd,
        ]
        if not self.password:
            parts[1:1] = ["-o", "BatchMode=yes"]
        if self.password and shutil.which("setsid"):
            parts.insert(0, "setsid")
        return parts

    def run(self, remote_cmd, stdin=None, timeout=900):
        env = self._env()
        parts = self._parts(remote_cmd)
        self.emit("ssh {}@{} {}".format(self.user, self.host, remote_cmd[:180]), "cmd")
        try:
            options = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "env": env,
                "timeout": timeout,
            }
            if stdin is None:
                options["stdin"] = subprocess.DEVNULL
            else:
                options["input"] = stdin
            result = subprocess.run(parts, **options)
        except subprocess.TimeoutExpired:
            self.emit("SSH 命令超时", "err")
            return False, ""
        except (OSError, ValueError) as exc:
            self.emit("SSH 执行失败: {}".format(exc), "err")
            return False, ""
        finally:
            self.cleanup()
        output = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
        for line in output.splitlines():
            self.emit(line)
        return result.returncode == 0, output

    def upload(self, data, remote_path):
        command = "mkdir -p $(dirname {}) && cat > {}".format(
            shlex.quote(os.path.dirname(remote_path)), shlex.quote(remote_path)
        )
        ok, _ = self.run(command, stdin=data, timeout=180)
        return ok

    def cleanup(self):
        if self.helper and os.path.isfile(self.helper):
            try:
                os.unlink(self.helper)
            except OSError:
                pass
        self.helper = None

    def install_software(self, archive, port=8737, report=None):
        temp = "{}-{}".format(REMOTE_BASE, secrets.token_hex(3))
        try:
            self.emit("连接设备", "info")
            ok, output = self.run(
                "printf '%s|%s|%s|%s\\n' \"$(uname -s)\" \"$(uname -m)\" "
                "\"$(dpkg --print-architecture 2>/dev/null || echo unknown)\" \"$HOME\"",
                timeout=60,
            )
            if not ok:
                return {"ok": False, "reason": "无法连接设备"}
            parts = output.strip().split("|")
            system_name = parts[0] if parts else ""
            if re.search(r"MINGW|CYGWIN|MSYS|Windows|Darwin|FreeBSD", system_name, re.I):
                return {"ok": False, "reason": "目标不是 Linux，felisros 只部署到 Linux 设备"}
            machine = parts[1] if len(parts) > 1 else ""
            dpkg_arch = parts[2] if len(parts) > 2 else ""
            home = parts[3] if len(parts) > 3 else ""
            arch = describe_arch(machine, dpkg_arch)
            self.emit("架构: " + arch, "info")
            if not home:
                return {"ok": False, "reason": "无法获取用户目录"}

            self.emit("上传 felisros 安装包", "info")
            if not self.run("mkdir -p {}".format(shlex.quote(temp)), timeout=60)[0]:
                return {"ok": False, "reason": "无法建立远程目录"}
            if not self.upload(archive, temp + "/pkg.tar.gz"):
                return {"ok": False, "reason": "上传安装包失败"}
            package = shlex.quote(temp + "/pkg.tar.gz")
            expected = hashlib.sha256(archive).hexdigest()
            check = (
                "if command -v sha256sum >/dev/null 2>&1; then "
                "actual=$(sha256sum {package} | awk '{{print $1}}'); "
                "[ \"$actual\" = \"{expected}\" ] || "
                "{{ echo \"校验和不匹配: $actual\"; exit 1; }}; "
                "fi; "
                "tar tzf {package} > {listing}; "
                "if grep -Eq '(^/|(^|/)\\.\\.(/|$))' {listing}; then "
                "echo '安装包包含不安全路径'; exit 1; fi"
            ).format(package=package, expected=expected, listing=shlex.quote(temp + "/members.txt"))
            ok, output = self.run(check, timeout=180)
            if not ok:
                return {"ok": False, "reason": "安装包校验失败: " + output[-300:]}
            ok, _ = self.run(
                "tar xzf {0}/pkg.tar.gz -C {0}".format(shlex.quote(temp)), timeout=180
            )
            if not ok:
                return {"ok": False, "reason": "远程解包失败"}

            self.emit("安装 felisros 到用户目录", "info")
            ok, output = self.run(
                "cd {} && sh install.sh".format(shlex.quote(temp + "/felisros-linux")),
                timeout=900,
            )
            if not ok:
                return {"ok": False, "reason": "安装失败: " + output[-300:]}

            launcher = home + "/.local/bin/felisros"
            self.emit("在后台启动 felisros（不设置开机自启）", "info")
            ok, output = self.run(service.start_command(launcher, port), timeout=120)
            if not ok:
                return {"ok": False, "reason": "启动服务失败: " + output[-300:]}

            ok, output = self.run(
                "python3 -c \"import time,urllib.request,sys\n"
                "deadline=time.time()+25\n"
                "while True:\n"
                "    try:\n"
                "        urllib.request.urlopen('http://127.0.0.1:{}/api/env', timeout=3)\n"
                "        break\n"
                "    except Exception:\n"
                "        if time.time() >= deadline:\n"
                "            sys.exit(1)\n"
                "        time.sleep(2)\n\"".format(int(port)),
                timeout=60,
            )
            if not ok:
                _, diag = self.run(
                    "echo '[proc]'; ps -ef 2>/dev/null "
                    "| grep '[a]pp.py --no-browser' || echo '进程未在运行'; "
                    "echo '[log]'; tail -40 {} 2>/dev/null || echo '（无日志）'; "
                    "echo '[foreground]'; "
                    "timeout 6 {launcher} --no-browser --port {port} 2>&1 | tail -40".format(
                        shlex.quote(home + "/.local/share/felisros/app/log/app-run.log"),
                        launcher=shlex.quote(launcher),
                        port=int(port),
                    ),
                    timeout=60,
                )
                return {
                    "ok": False,
                    "reason": "安装完成但服务未响应: " + (diag[-600:] or "（无诊断输出）"),
                }

            result = {
                "ok": True,
                "reason": "安装完成，服务运行在 http://设备IP:{}".format(port),
                "arch": arch,
            }
            if report:
                report(result["reason"], "ok")
            return result
        finally:
            self.cleanup()
            self.run("rm -rf {}".format(shlex.quote(temp)), timeout=60)

    def update_software(self, seed, archive=None, port=8737, report=None):
        temp = "{}-{}".format(REMOTE_BASE, secrets.token_hex(3))
        try:
            ok, output = self.run(
                "printf '%s|%s|%s\\n' \"$(uname -s)\" \"$(uname -m)\" \"$HOME\"",
                timeout=60,
            )
            if not ok:
                return {"ok": False, "reason": "无法连接设备"}
            fields = output.strip().split("|")
            system_name = fields[0] if fields else ""
            if re.search(r"MINGW|CYGWIN|MSYS|Windows|Darwin|FreeBSD", system_name, re.I):
                return {"ok": False, "reason": "目标不是 Linux，felisros 只部署到 Linux 设备"}
            machine = fields[1] if len(fields) > 1 else ""
            arch = describe_arch(machine, "")
            home = fields[2] if len(fields) > 2 else ""
            if not home.startswith("/"):
                return {"ok": False, "reason": "无法获取用户目录"}

            installed, _ = self.run(
                "test -f {} && echo INSTALLED".format(
                    shlex.quote(home + "/.local/share/felisros/app/app.py")
                ),
                timeout=30,
            )
            if not installed:
                self.emit("设备尚未安装 felisros，改为直接分发当前版本", "info")
                if not archive:
                    return {"ok": False, "reason": "设备未安装 felisros，且没有可用的安装包"}
                first = self.install_software(archive, port)
                if not first.get("ok"):
                    return first
                first["kind"] = "update"
                first["version"] = first.get("version") or release.VERSION
                return first

            self.emit("设备已安装 felisros，通知它从更新源拉取", "info")
            command = (
                REMOTE_UPDATE.replace("__SEED__", shlex.quote(seed))
                .replace("__PORT__", str(int(port)))
            )
            ok, output = self.run(command, timeout=900)
            item = parse_report(output)
            if not item:
                tail = "\n".join(output.splitlines()[-3:])
                return {"ok": False, "reason": "未收到更新回传: " + tail}
            item["ok"] = bool(item.get("ok")) and ok
            item["arch"] = arch
            if not item.get("reason"):
                item["reason"] = "更新完成" if item["ok"] else "更新失败"
            if item.get("ok"):
                self.emit("更新已写入，正在重启设备上的服务", "info")
                self.run(
                    service.start_command(home + "/.local/bin/felisros", port), timeout=120
                )
            if report:
                report(item["reason"], "ok" if item["ok"] else "err")
            return item
        finally:
            self.cleanup()
            self.run("rm -rf {}".format(shlex.quote(temp)), timeout=60)

    def install_ros_remote(self, deploy, versions=None, port=8737, report=None):
        temp = "{}-{}".format(REMOTE_BASE, secrets.token_hex(3))
        try:
            ok, output = self.run('printf "%s" "$HOME"', timeout=60)
            home = output.strip().splitlines()[-1] if output.strip() else ""
            if not ok or not home.startswith("/"):
                return {"ok": False, "reason": "无法获取设备用户目录"}
            cfg = temp + "/deploy.json"
            versions_file = ""
            self.emit("上传 ROS 部署配置", "info")
            if not self.run("mkdir -p {}".format(shlex.quote(temp)), timeout=60)[0]:
                return {"ok": False, "reason": "无法建立远程目录"}
            payload = json.dumps(deploy or {}, ensure_ascii=False).encode("utf-8")
            if not self.upload(payload, cfg):
                return {"ok": False, "reason": "上传部署配置失败"}
            if versions:
                versions_file = temp + "/versions.json"
                if not self.upload(
                    json.dumps(versions, ensure_ascii=False).encode("utf-8"), versions_file
                ):
                    return {"ok": False, "reason": "上传版本清单失败"}

            sudo_file = ""
            if self.sudo_password:
                sudo_file = temp + "/sudo.txt"
                if not self.upload(self.sudo_password.encode("utf-8"), sudo_file):
                    return {"ok": False, "reason": "上传 sudo 凭据失败"}
                self.run("chmod 600 {}".format(shlex.quote(sudo_file)), timeout=30)

            self.emit("遥控 felisros 安装 ROS，请等待", "info")
            command = (
                REMOTE_CONTROL.replace("__CFG__", shlex.quote(cfg))
                .replace("__PORT__", str(int(port)))
                .replace("__VERS__", shlex.quote(versions_file))
                .replace("__SUDO__", shlex.quote(sudo_file))
            )
            ok, output = self.run(command, timeout=5400)
            item = parse_report(output)
            if not item:
                tail = "\n".join(output.splitlines()[-3:])
                return {"ok": False, "reason": "未收到安装回传: " + tail}
            item["ok"] = bool(item.get("ok")) and ok
            if not item.get("reason"):
                item["reason"] = "遥控安装完成" if item["ok"] else "遥控安装失败"
            if report:
                report(item["reason"], "ok" if item["ok"] else "err")
            return item
        finally:
            self.cleanup()
            self.run("rm -rf {}".format(shlex.quote(temp)), timeout=60)

    def install(self, deploy, versions=None, report=None):
        remote_dir = "{}-{}".format(REMOTE_BASE, secrets.token_hex(3))
        try:
            self.emit("连接设备", "info")
            ok, _ = self.run("mkdir -p {}".format(shlex.quote(remote_dir)), timeout=60)
            if not ok:
                return {"ok": False, "reason": "无法建立远程目录"}

            self.emit("上传 felisros 程序包", "info")
            if not self.upload(package_archive(), remote_dir + "/pkg.tgz"):
                return {"ok": False, "reason": "上传程序包失败"}
            ok, _ = self.run(
                "tar xzf {} -C {}".format(shlex.quote(remote_dir + "/pkg.tgz"), shlex.quote(remote_dir)),
                timeout=180,
            )
            if not ok:
                return {"ok": False, "reason": "远程解包失败"}

            payload = json.dumps(deploy, ensure_ascii=False).encode("utf-8")
            if not self.upload(payload, remote_dir + "/deploy.json"):
                return {"ok": False, "reason": "上传部署配置失败"}

            options = "--deploy {} --no-browser".format(shlex.quote(remote_dir + "/deploy.json"))
            if versions:
                if not self.upload(
                    json.dumps(versions, ensure_ascii=False).encode("utf-8"),
                    remote_dir + "/versions.json",
                ):
                    return {"ok": False, "reason": "上传版本清单失败"}
                options += " --versions " + shlex.quote(remote_dir + "/versions.json")

            self.emit("开始远程安装，可能需要几分钟", "info")
            command = "sudo -S -p '' python3 {} {}".format(
                shlex.quote(remote_dir + "/app.py"), options
            )
            ok, output = self.run(
                command,
                stdin=(self.sudo_password + "\n").encode("utf-8"),
                timeout=5400,
            )
            tail = "\n".join(output.splitlines()[-3:])
            result = {"ok": ok, "reason": "远程安装完成" if ok else ("远程安装失败: " + tail)}
            if report:
                report(result["reason"], "ok" if ok else "err")
            return result
        finally:
            self.cleanup()
            self.run("rm -rf {}".format(shlex.quote(remote_dir)), timeout=60)
