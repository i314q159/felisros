import re

LOCK_PATHS = (
    "/var/lib/dpkg/lock-frontend "
    "/var/lib/dpkg/lock "
    "/var/lib/apt/lists/lock "
    "/var/cache/apt/archives/lock"
)

UNMET = ("未满足的依赖关系", "unmet dependencies", "but it is not installable")
BROKEN = ("--fix-broken install",)
FETCH = ("unable to fetch some archives", "无法下载", "404  not found", "404 not found")


class Apt:
    def __init__(self, runner):
        self.runner = runner

    def update(self, timeout=240):
        return self.runner.run("apt-get update", timeout=timeout, sudo=True)

    def busy(self):
        return self.runner.run(
            "fuser {} >/dev/null 2>&1".format(LOCK_PATHS), timeout=15, sudo=True, quiet=True
        ).code == 0

    def wait_locks(self, rounds=60, interval=5):
        for index in range(rounds):
            if not self.busy():
                return True
            if index == 0:
                self.runner.emit("其他程序(通常是系统自动更新)正在使用软件包管理器, 最多等待 5 分钟", "warn")
            if self.runner.wait(interval):
                return False
        return False

    def repair(self):
        if not self.wait_locks():
            return False
        self.runner.run("dpkg --configure -a", timeout=300, sudo=True)
        return True

    def search_base(self):
        from . import ros

        found = {}
        result = self.runner.run("apt-cache search ros-base", timeout=120)
        for line in result.out.splitlines():
            match = re.search(r"\bros-([a-z]+)-ros-base\b", line)
            if match:
                found[match.group(1)] = match.group(0)
        for package in ros.custom_base_packages():
            name = ros.version_for_package(package)
            if name:
                found[name] = package
        return found

    def install(self, packages, timeout=3600, tool="apt-get", extra=""):
        if isinstance(packages, str):
            packages = [packages]
        cmd = "{} install -y {}{}".format(tool, " ".join(packages), extra)
        return self.runner.run(cmd, timeout=timeout, sudo=True)

    def install_with_repair(self, packages, report=None):
        result = self.install(packages)
        if result.ok:
            return result

        if result.hit(*UNMET):
            self._note(report, "依赖关系冲突, 改用 aptitude 求解", "warn")
            self.install(["aptitude"], timeout=600)
            result = self.install(packages, tool="aptitude")
            if result.ok:
                return result

        if result.hit(*BROKEN):
            self._note(report, "检测到损坏的依赖, 先执行 --fix-broken 修复", "warn")
            self.runner.run("apt-get --fix-broken install -y", timeout=1200, sudo=True)
            result = self.install(packages)
            if result.ok:
                return result

        if result.hit(*FETCH):
            self._note(report, "部分软件包下载失败, 刷新索引后重试", "warn")
            self.update()
            result = self.install(packages, extra=" --fix-missing")
            if result.ok:
                return result

        if result.hit("404"):
            self._note(report, "镜像缺少软件包(404), 建议换一个 ROS 镜像源重试", "err")

        return result

    def _note(self, report, text, level):
        self.runner.emit(text, level)
        if report:
            report(text, level)
