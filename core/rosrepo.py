import os
import re
import tempfile

from . import ros

LIST_PATH = "/etc/apt/sources.list.d/felisros.list"
APT_UA = "Debian APT-HTTP/1.3"
DEAD_CODES = ("403", "404", "410")


def render(codename, arch, sources):
    lines = [
        "deb [arch={} trusted=yes] {} {} main".format(arch, item["url"], codename)
        for item in sources
    ]
    return "\n".join(lines) + "\n"


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
    for _ in range(rounds):
        sources = ros.sources_for(system.codename, system.arch, first=first, excluded=excluded)
        if not sources:
            break
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
