import threading
import time
import traceback

from . import ros, rosrepo, shellenv, sysrepo
from .apt import Apt
from .runner import Runner
from .system import is_root

STEPS = [
    {"id": "precheck", "title": "环境检查"},
    {"id": "syssource", "title": "系统源"},
    {"id": "rossource", "title": "ROS 源"},
    {"id": "install", "title": "安装"},
    {"id": "env", "title": "环境变量"},
    {"id": "verify", "title": "验证"},
]

MIN_FREE_GB = 2
MIN_DESKTOP_GB = 6
MAX_EVENTS = 4000


class EventBus:
    def __init__(self):
        self.lock = threading.Lock()
        self.events = []
        self.seq = 0

    def push(self, payload):
        with self.lock:
            self.seq += 1
            item = dict(payload, seq=self.seq, ts=round(time.time(), 3))
            self.events.append(item)
            if len(self.events) > MAX_EVENTS:
                self.events = self.events[-MAX_EVENTS // 2:]
            return item

    def since(self, seq):
        with self.lock:
            return [item for item in self.events if item["seq"] > seq]


def verify_commands(version, desktop):
    if ros.kind_of(version) == ros.ROS1:
        commands = ["roscore"]
        if desktop:
            commands.append("rosrun turtlesim turtlesim_node")
            commands.append("rosrun turtlesim turtle_teleop_key")
        return commands
    if desktop:
        return [
            "ros2 run turtlesim turtlesim_node",
            "ros2 run turtlesim turtle_teleop_key",
        ]
    return ["ros2 topic list"]


class Installer:
    def __init__(self, system):
        self.system = system
        self.bus = EventBus()
        self.lock = threading.Lock()
        self.thread = None
        self.runner = None
        self.apt = None
        self.sudo_password = ""
        self.state = self._idle()

    def _idle(self):
        return {
            "running": False,
            "finished": False,
            "ok": None,
            "percent": 0,
            "step": "",
            "message": "等待开始",
            "result": None,
            "steps": STEPS,
        }

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def log(self, text, level="out"):
        self.bus.push({"type": "log", "level": level, "text": text})

    def note(self, text, level="info"):
        self.log(text, level)

    def mark(self, step, status, text=""):
        self.bus.push({"type": "step", "step": step, "status": status, "text": text})
        with self.lock:
            self.state["step"] = step
            if text:
                self.state["message"] = text

    def progress(self, value, text=""):
        self.bus.push({"type": "progress", "value": value, "text": text})
        with self.lock:
            self.state["percent"] = value
            if text:
                self.state["message"] = text

    def start(self, config, sudo_password=""):
        with self.lock:
            if self.state["running"]:
                return False, "已经有安装任务在运行"
            self.sudo_password = sudo_password or ""
            self.bus = EventBus()
            self.state = self._idle()
            self.state["running"] = True
            self.state["message"] = "准备中"
        self.thread = threading.Thread(target=self._run, args=(config,), daemon=True)
        self.thread.start()
        return True, "已开始"

    def cancel(self):
        if self.runner:
            self.runner.cancel()
        self.bus.push({"type": "log", "level": "warn", "text": "收到取消请求，正在停止..."})

    def _run(self, config):
        self.runner = Runner(on_line=self.log, sudo_password=self.sudo_password)
        self.apt = Apt(self.runner)
        result = {"ok": False, "reason": "unknown"}
        try:
            result = self._flow(config)
        except Exception as exc:
            self.log("运行中出现异常: {}".format(exc), "err")
            for line in traceback.format_exc().splitlines():
                self.log(line, "err")
        finally:
            ok = bool(result.get("ok"))
            with self.lock:
                self.state["running"] = False
                self.state["finished"] = True
                self.state["ok"] = ok
                self.state["result"] = result
                self.state["message"] = result.get("reason") or ("安装完成" if ok else "未完成")
                if ok:
                    self.state["percent"] = 100
            self.bus.push({"type": "done", "ok": ok, "result": result})

    def _stopped(self):
        return self.runner.cancelled.is_set()

    def _ensure_sudo(self):
        """提前确认 sudo 可用，避免没填密码时把每个镜像源都重试一遍。"""
        if not self.runner.which("sudo"):
            if is_root():
                return True, ""
            return False, "系统里没有 sudo 命令, 请以 root 身份运行 felisros"
        probe = self.runner.run("true", timeout=60, sudo=True, quiet=True)
        if probe.ok:
            if not self.sudo_password:
                self.log("sudo 免密可用", "ok")
            else:
                self.log("sudo 密码校验通过", "ok")
            return True, ""
        if probe.hit("not in the sudoers file", "不在 sudoers"):
            return False, "当前用户没有 sudo 权限, 请换用管理员账号或以 root 运行"
        if self.sudo_password:
            return False, "sudo 密码不正确, 请重新填写本机的 sudo 密码"
        return False, "安装 ROS 需要 sudo 密码, 请在安装页面填写本机 sudo 密码"

    def _flow(self, config):
        mode = config.get("mode") or "auto"
        result = {"ok": False, "mode": mode}
        self.log("felisros 开始工作", "info")

        self.mark("precheck", "run", "检查系统与磁盘")
        self.progress(3)
        if not ros.supported(self.system.codename):
            self.log(
                "当前系统 {} ({}) 不在支持列表内".format(self.system.pretty, self.system.codename), "err"
            )
            self.mark("precheck", "fail", "系统不受支持")
            result["reason"] = "系统 {} 暂不支持".format(self.system.codename)
            return result

        free = self.system.free_gb or 0
        if free < MIN_FREE_GB:
            self.log("磁盘剩余 {:.1f}GB, 不足以安装 ROS".format(free), "err")
            self.mark("precheck", "fail", "磁盘空间不足")
            result["reason"] = "磁盘剩余不足 {}GB".format(MIN_FREE_GB)
            return result

        wanted_kind = config.get("kind") or "auto"
        if wanted_kind == "auto":
            desktop = free >= MIN_DESKTOP_GB
            if not desktop:
                self.log("磁盘剩余 {:.1f}GB, 自动改为基础版".format(free), "warn")
        else:
            desktop = wanted_kind == "desktop"
            if desktop and free < MIN_DESKTOP_GB:
                self.log("磁盘剩余 {:.1f}GB, 桌面版可能装不下".format(free), "warn")

        result["desktop"] = desktop
        if config.get("name"):
            result["config_name"] = config["name"]
        extras = config.get("extra_packages") or []
        self.log(
            "系统: {} {} ({}) 架构 {} 剩余 {:.1f}GB".format(
                self.system.pretty, self.system.version, self.system.codename, self.system.arch, free
            ),
            "ok",
        )
        sudo_ok, sudo_reason = self._ensure_sudo()
        if not sudo_ok:
            self.log(sudo_reason, "err")
            self.mark("precheck", "fail", "sudo 不可用")
            result["reason"] = sudo_reason
            return result
        self.apt.repair()
        self.mark("precheck", "ok", "环境检查通过")
        if self._stopped():
            return self._cancelled(result)

        if config.get("change_system_source", True):
            self.mark("syssource", "run", "测速并切换系统软件源")
            self.progress(8)
            source = sysrepo.switch(self.runner, self.apt, self.system, self.note)
            result["system_source"] = source
            self.mark("syssource", "ok" if source else "warn", "已切换" if source else "保留原配置")
            if self._stopped():
                return self._cancelled(result)
        else:
            self.mark("syssource", "skip", "保留原系统源")
        self.progress(20)

        self.mark("rossource", "run", "写入 ROS 软件源并验证可用")
        first = config.get("mirror")
        first = None if first in (None, "", "auto") else first
        sources, available = rosrepo.ensure(
            self.runner, self.apt, self.system, self.note, first=first
        )
        if not sources or not available:
            self.mark("rossource", "fail", "没有可用的 ROS 软件源")
            result["reason"] = "无法找到可用的 ROS 软件源"
            return result
        result["sources"] = sources
        self.mark("rossource", "ok", "、".join(item["mirror"] for item in sources))
        if self._stopped():
            return self._cancelled(result)
        self.progress(42)

        wanted = config.get("version") or "auto"
        version = wanted if wanted in available else ros.pick_available(self.system.codename, available)
        if not version:
            self.mark("install", "fail", "没有可安装的 ROS 版本")
            result["reason"] = "软件源里没有可安装的 ROS 版本"
            return result
        if wanted not in ("auto", "") and wanted != version:
            self.log("你选择的 {} 当前不可用, 改为安装 {}".format(wanted, version), "warn")
        result["version"] = version
        result["kind"] = "desktop" if desktop else "base"

        package = ros.desktop_package(version) if desktop else ros.base_package(version)
        result["package"] = package
        self.mark("install", "run", "正在安装 " + package)
        self.progress(48, "下载并安装 " + package)
        installed = self.apt.install_with_repair([package], report=self.note)
        self.progress(80)
        if installed.ok:
            deps = ros.dependencies(version)
            if deps:
                self.log("补充安装开发依赖: " + " ".join(deps), "info")
                self.apt.install(deps, timeout=1800)
            if extras:
                self.log("按配置文件安装额外软件包: " + " ".join(extras), "info")
                extra_result = self.apt.install(extras, timeout=1800)
                result["extra_packages"] = extras
                result["extra_ok"] = extra_result.ok
                if not extra_result.ok:
                    self.log("额外软件包安装失败, 继续验证 ROS 本体", "warn")
        elif extras:
            result["extra_packages"] = extras
            result["extra_ok"] = False
        self.progress(88)
        self.mark("install", "ok" if installed.ok else "fail", package)
        if self._stopped():
            return self._cancelled(result)

        self.mark("env", "run", "写入 shell 配置")
        shellenv.configure(self.runner, version, self.note)
        self.mark("env", "ok", "新终端自动生效")
        self.progress(95)

        self.mark("verify", "run", "检查安装结果")
        ok = ros.verify(version)
        self.mark("verify", "ok" if ok else "fail", "/opt/ros/{}/setup.bash".format(version))
        self.progress(100 if ok else 96)

        result["ok"] = ok
        result["commands"] = verify_commands(version, desktop)
        result["reason"] = "安装完成" if ok else "安装后未找到 /opt/ros/{}/setup.bash".format(version)
        if ok:
            self.log("搞定, 新开一个终端就能用 {} 了".format(ros.describe(version)), "ok")
        return result

    def _cancelled(self, result):
        self.log("已取消本次安装", "warn")
        result["ok"] = False
        result["reason"] = "已取消"
        return result
