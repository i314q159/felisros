import base64
import os
import re
import shlex
import tempfile

from . import ros

LIST_PATH = "/etc/apt/sources.list.d/felisros.list"
KEY_PATH = "/usr/share/keyrings/felisros-ros.gpg"
KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ros-archive-keyring.gpg")
APT_UA = "Debian APT-HTTP/1.3"
DEAD_CODES = ("403", "404", "410")
ROS_MARKERS = ("/ros2/", "/ros/ubuntu", "packages.ros.org/ros2", "packages.ros.org/ros")


def render(codename, arch, sources, key_path=KEY_PATH):
    # 用随包附带的 ROS 归档公钥做签名校验，不用 trusted=yes 跳过校验
    lines = [
        "deb [arch={} signed-by={}] {} {} main".format(arch, key_path, item["url"], codename)
        for item in sources
    ]
    return "\n".join(lines) + "\n"


def install_key(runner):
    """把内置的 ROS 归档公钥装到目标机，供 signed-by 校验使用。"""
    try:
        with open(KEY_FILE, "rb") as handle:
            blob = base64.b64encode(handle.read()).decode("ascii")
    except OSError:
        return False
    temp = "/tmp/felisros-ros-key.gpg"
    made = runner.run(
        "printf '%s' '{}' | base64 -d > {}".format(blob, temp), timeout=60, quiet=True
    )
    if not made.ok:
        return False
    installed = runner.run(
        "install -m 644 {} {}".format(temp, KEY_PATH), sudo=True, quiet=True
    )
    runner.run("rm -f " + temp, quiet=True)
    return installed.ok


def write(runner, codename, arch, sources):
    data = render(codename, arch, sources)
    handle = tempfile.NamedTemporaryFile("w", suffix=".list", delete=False, encoding="utf-8")
    try:
        handle.write(data)
        handle.close()
        # NamedTemporaryFile 是 0600，cp 会保留该权限；源文件必须 0644，
        # 否则非 root 的 apt-cache/apt 读不到它，会把 ROS 源当成不存在。
        # 用 sh -c 包住整条命令，否则 && 后的 chmod 会掉出 sudo 作用域。
        return runner.run(
            "sh -c 'cp {0} {1} && chmod 644 {1}'".format(handle.name, LIST_PATH), sudo=True
        ).ok
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def mirror_of(uri):
    plain = uri.replace("https://", "http://")
    for name, meta in ros.MIRRORS.items():
        for kind in (ros.ROS1, ros.ROS2):
            url = meta.get(kind)
            if url and plain.startswith(url.replace("https://", "http://")):
                return name
    return None


def _blocks(text):
    """把 ###FILE 分段后的配置文本拆成 (文件路径, 行列表)。"""
    path = ""
    lines = []
    for raw in text.splitlines():
        if raw.startswith("###FILE "):
            if path:
                yield path, lines
            path = raw[len("###FILE "):].strip()
            lines = []
        else:
            lines.append(raw)
    if path:
        yield path, lines


def _deb_entries(lines):
    for raw in lines:
        line = raw.strip()
        if not re.match(r"^deb(\s|$)", line):
            continue
        try:
            parts = shlex.split(line)
        except ValueError:
            continue
        rest = parts[1:]
        if rest and rest[0].startswith("["):
            rest = rest[1:]
        if len(rest) >= 2:
            yield rest[0], rest[1]


def _deb822_entries(lines):
    result = []
    fields = {}
    last = ""

    def flush():
        uris = (fields.get("uris") or "").split()
        suites = (fields.get("suites") or "").split()
        enabled = (fields.get("enabled") or "yes").strip().lower()
        if enabled in ("no", "false", "0"):
            return
        for uri in uris:
            for suite in suites:
                result.append((uri, suite))

    for raw in lines + [""]:
        line = raw.rstrip()
        if not line.strip():
            flush()
            fields = {}
            last = ""
            continue
        if line.startswith("#"):
            continue
        if line[:1].isspace():
            if last:
                fields[last] = fields.get(last, "") + " " + line.strip()
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            last = key.strip().lower()
            fields[last] = value.strip()
    return result


def _is_ros(uri):
    plain = (uri or "").strip().lower()
    return any(marker in plain for marker in ROS_MARKERS)


def existing(codename, text, own_path=LIST_PATH):
    """找出用户已有的、与当前发行版匹配的 ROS 软件源（忽略本工具自己写的文件）。"""
    found = []
    seen = set()
    for path, lines in _blocks(text):
        if os.path.normpath(path) == os.path.normpath(own_path):
            continue
        entries = _deb822_entries(lines) if path.endswith(".sources") else list(_deb_entries(lines))
        for uri, suite in entries:
            if not _is_ros(uri):
                continue
            if codename not in [item.strip() for item in suite.split(",")]:
                continue
            if uri in seen:
                continue
            seen.add(uri)
            found.append({"file": path, "url": uri, "mirror": mirror_of(uri)})
    return found


def existing_ros_sources(runner, codename):
    cmd = (
        "sh -c 'for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list "
        "/etc/apt/sources.list.d/*.sources; do [ -f \"$f\" ] || continue; "
        "echo \"###FILE $f\"; cat \"$f\"; done'"
    )
    return existing(codename, runner.run(cmd, timeout=30, quiet=True).out)


def drop_own(runner):
    if not runner.run("test -f " + LIST_PATH, quiet=True).ok:
        return False
    return runner.run("rm -f " + LIST_PATH, sudo=True, quiet=True).ok


def package_uri(runner, package):
    result = runner.run(
        "apt-get download --print-uris {} 2>/dev/null".format(package), timeout=90, quiet=True
    )
    match = re.search(r"'([^']+\.deb)'", result.out)
    return match.group(1) if match else None


def http_code(runner, url):
    if runner.which("curl"):
        cmd = "curl -sIL -A '{}' -o /dev/null -w '%{{http_code}}' --max-time 10 '{}'".format(
            APT_UA, url
        )
        result = runner.run(cmd, timeout=30, quiet=True)
        code = result.out.strip().splitlines()[-1] if result.out.strip() else ""
        return code[-3:]
    cmd = "wget --spider -S -U '{}' --timeout=10 --tries=1 '{}' 2>&1 | grep HTTP/".format(
        APT_UA, url
    )
    result = runner.run(cmd, timeout=30, quiet=True)
    codes = re.findall(r"HTTP/[\d.]+ (\d+)", result.out)
    return codes[-1] if codes else ""


def stale_mirrors(runner, available):
    stale = set()
    for version in available:
        uri = package_uri(runner, ros.base_package(version))
        if not uri:
            continue
        code = http_code(runner, uri)
        if code not in DEAD_CODES:
            continue
        name = mirror_of(uri)
        runner.emit("{} 源里 {} 的包已经失效(HTTP {})".format(name or "当前", version, code), "warn")
        if name:
            stale.add(name)
    return stale


def ensure(runner, apt, system, report=None, first=None, rounds=8):
    excluded = set()
    supported = ros.DISTRO_VERSIONS.get(system.codename) or set()

    # 机器上已经有 ROS 源时优先复用：既避免两套源重复告警，也不去动用户已有配置
    reused = existing_ros_sources(runner, system.codename)
    if reused:
        labels = "、".join(item["mirror"] or item["url"] for item in reused)
        runner.emit("已检测到现有 ROS 软件源: {}，优先复用".format(labels), "info")
        if drop_own(runner):
            runner.emit("已清除重复的 felisros 源文件", "info")
        if apt.update().ok:
            available = apt.search_base()
            if available and any(name in supported for name in available):
                stale = stale_mirrors(runner, available)
                if not stale:
                    if report:
                        report("复用已有 ROS 软件源: " + labels, "ok")
                    picked = [
                        {
                            "kind": ros.ROS1 if "/ros/ubuntu" in item["url"] else ros.ROS2,
                            "mirror": item["mirror"] or item["url"],
                            "url": item["url"],
                        }
                        for item in reused
                    ]
                    return picked, available
                excluded.update(stale)
        runner.emit("现有 ROS 软件源不可用，改用内置镜像", "warn")

    key_ready = False
    for _ in range(rounds):
        sources = ros.sources_for(system.codename, system.arch, first=first, excluded=excluded)
        if not sources:
            break
        if not key_ready:
            if not install_key(runner):
                runner.emit("安装 ROS 签名密钥失败", "err")
                break
            key_ready = True
        labels = "、".join("{} {}".format(item["kind"], item["mirror"]) for item in sources)
        runner.emit("写入 ROS 软件源: " + labels, "info")
        if not write(runner, system.codename, system.arch, sources):
            runner.emit("写入 ROS 软件源失败", "err")
            break
        if not apt.update().ok:
            excluded.update(item["mirror"] for item in sources)
            runner.emit("apt update 未通过, 换下一个镜像源", "warn")
            continue
        available = apt.search_base()
        if not available:
            excluded.update(item["mirror"] for item in sources)
            runner.emit("软件源里查不到 ROS 软件包, 换下一个镜像源", "warn")
            continue
        stale = stale_mirrors(runner, available)
        if stale:
            excluded.update(stale)
            if report:
                report("镜像 {} 的安装包已被删除, 自动换源重试".format("、".join(sorted(stale))), "warn")
            continue
        if report:
            report("ROS 软件源可用: " + labels, "ok")
        return sources, available
    return None, None
