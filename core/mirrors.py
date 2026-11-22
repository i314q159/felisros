import os
import tempfile
import time
import urllib.error
import urllib.request

APT_UA = "Debian APT-HTTP/1.3"

UBUNTU_MIRRORS = [
    ("ustc", "中科大", "https://mirrors.ustc.edu.cn/ubuntu"),
    ("tsinghua", "清华 TUNA", "https://mirrors.tuna.tsinghua.edu.cn/ubuntu"),
    ("huawei", "华为云", "https://repo.huaweicloud.com/ubuntu"),
    ("aliyun", "阿里云", "https://mirrors.aliyun.com/ubuntu"),
    ("netease", "网易", "https://mirrors.163.com/ubuntu"),
]

UBUNTU_PORTS_MIRRORS = [
    ("ustc", "中科大", "https://mirrors.ustc.edu.cn/ubuntu-ports"),
    ("tsinghua", "清华 TUNA", "https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports"),
    ("huawei", "华为云", "https://repo.huaweicloud.com/ubuntu-ports"),
    ("aliyun", "阿里云", "https://mirrors.aliyun.com/ubuntu-ports"),
]

UBUNTU_OLD_MIRRORS = [
    ("tsinghua", "清华旧版归档", "https://mirrors.tuna.tsinghua.edu.cn/ubuntu-old-releases"),
    ("ustc", "中科大旧版归档", "https://mirrors.ustc.edu.cn/ubuntu-old-releases"),
]

DEBIAN_MIRRORS = [
    ("ustc", "中科大", "https://mirrors.ustc.edu.cn/debian"),
    ("tsinghua", "清华 TUNA", "https://mirrors.tuna.tsinghua.edu.cn/debian"),
    ("huawei", "华为云", "https://repo.huaweicloud.com/debian"),
    ("aliyun", "阿里云", "https://mirrors.aliyun.com/debian"),
]

SYSTEM_LIST = "/etc/apt/sources.list"
DEB822_LIST = "/etc/apt/sources.list.d/ubuntu.sources"
BACKUP_SUFFIX = ".felisros.bak"


def is_debian(system):
    return system.distro.startswith("debian")


def candidates(system):
    if is_debian(system):
        return DEBIAN_MIRRORS
    if system.arch == "amd64":
        return UBUNTU_MIRRORS
    return UBUNTU_PORTS_MIRRORS


def measure(url, codename, timeout=4):
    request = urllib.request.Request(
        "{}/dists/{}/Release".format(url, codename), headers={"User-Agent": APT_UA}
    )
    start = time.time()
    size = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            while time.time() - start < timeout:
                chunk = response.read(16384)
                if not chunk:
                    break
                size += len(chunk)
    except urllib.error.HTTPError as exc:
        return 0, "missing" if exc.code == 404 else "fail"
    except Exception:
        return 0, "fail"
    if size == 0:
        return 0, "fail"
    return int(size / 1024.0 / max(time.time() - start, 0.001)), "ok"


def rank(system, mirrors=None):
    results = []
    for name, label, url in mirrors or candidates(system):
        speed, status = measure(url, system.codename)
        if status == "fail":
            speed, status = measure(url, system.codename)
        results.append(
            {"name": name, "label": label, "url": url, "speed": speed, "status": status}
        )
    results.sort(key=lambda item: (-item["speed"], item["label"]))
    return results


def uses_deb822(system):
    return not is_debian(system) and os.path.exists(DEB822_LIST)


def render(system, url):
    codename = system.codename
    if is_debian(system):
        components = "main contrib non-free non-free-firmware"
        return "\n".join(
            "deb {0} {1} {2}".format(url, suite, components)
            for suite in (
                codename,
                codename + "-updates",
                codename + "-backports",
                codename + "-security",
            )
        ) + "\n"
    if uses_deb822(system):
        return "\n".join(
            [
                "Types: deb",
                "URIs: " + url,
                "Suites: {0} {0}-updates {0}-backports {0}-security".format(codename),
                "Components: main restricted universe multiverse",
                "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg",
            ]
        ) + "\n"
    components = "main restricted universe multiverse"
    return "\n".join(
        "deb {0} {1} {2}".format(url, suite, components)
        for suite in (
            codename,
            codename + "-updates",
            codename + "-backports",
            codename + "-security",
        )
    ) + "\n"


def backup(runner, path):
    if not os.path.exists(path):
        return None
    target = path + BACKUP_SUFFIX
    if os.path.exists(target):
        return target
    result = runner.run("cp {} {}".format(path, target), sudo=True)
    return target if result.ok else None


def write(runner, system, url):
    target = DEB822_LIST if uses_deb822(system) else SYSTEM_LIST
    data = render(system, url)
    handle = tempfile.NamedTemporaryFile("w", suffix=".list", delete=False, encoding="utf-8")
    try:
        handle.write(data)
        handle.close()
        backup(runner, target)
        result = runner.run(
            "sh -c 'cp {0} {1} && chmod 644 {1}'".format(handle.name, target), sudo=True
        )
        return result.ok
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
