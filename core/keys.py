import os
import tempfile

FINGERPRINT = "C1CF6E31E6BADE8868B172B4F42ED6FBAB17C654"
KEYRING = "/usr/share/keyrings/ros-archive-keyring.gpg"

KEY_URLS = [
    "https://raw.githubusercontent.com/ros/rosdistro/master/ros.asc",
    "https://gitee.com/rosdistro/rosdistro/raw/master/ros.asc",
    "https://mirrors.ustc.edu.cn/rosdistro/ros.asc",
    "https://mirrors.tuna.tsinghua.edu.cn/rosdistro/ros.asc",
]

KEY_SERVERS = ["hkp://keyserver.ubuntu.com:80", "hkp://keyserver.ubuntu.com"]


def download(runner, url, path):
    if runner.which("curl"):
        template = "curl -fsSL --max-time 30 {url} -o {path}"
    else:
        template = "wget -q --timeout=30 -O {path} {url}"
    return runner.run(template.format(url=url, path=path), timeout=60, quiet=True)


def looks_like_key(path):
    try:
        with open(path, "rb") as handle:
            head = handle.read(4096)
    except OSError:
        return False
    return b"BEGIN PGP PUBLIC KEY BLOCK" in head


def fingerprint_output(runner, path):
    if not runner.which("gpg"):
        return FINGERPRINT
    for cmd in (
        "gpg --batch --with-colons --show-keys {}".format(path),
        "gpg --batch --with-colons --import-options show-only --import {}".format(path),
    ):
        result = runner.run(cmd, timeout=30, quiet=True)
        if result.out:
            return result.out.replace(" ", "")
    return ""


def fingerprint_ok(runner, path):
    output = fingerprint_output(runner, path)
    if not output:
        return False
    return FINGERPRINT in output


def dearmor(runner, key_file):
    cmd = "sh -c 'gpg --batch --yes --dearmor -o {0} {1} && chmod 644 {0}'".format(KEYRING, key_file)
    return runner.run(cmd, timeout=60, sudo=True)


def copy_ascii(runner, key_file):
    cmd = "sh -c 'cp {0} {1} && chmod 644 {1}'".format(key_file, KEYRING)
    return runner.run(cmd, timeout=60, sudo=True)


def from_keyserver(runner, server):
    cmd = (
        "sh -c 'd=$(mktemp -d); "
        "GNUPGHOME=$d gpg --batch --keyserver {0} --recv-keys {1} >/dev/null 2>&1 && "
        "GNUPGHOME=$d gpg --batch --export {1} > {2}; r=$?; rm -rf $d; exit $r'"
    ).format(server, FINGERPRINT, KEYRING)
    result = runner.run(cmd, timeout=120, sudo=True)
    if result.ok:
        runner.run("chmod 644 " + KEYRING, sudo=True)
    return result.ok


def install(runner, report=None):
    runner.emit("正在获取 ROS 官方公钥", "info")
    handle = tempfile.NamedTemporaryFile("wb", suffix=".asc", delete=False)
    path = handle.name
    handle.close()
    try:
        for url in KEY_URLS:
            for _ in range(2):
                if download(runner, url, path).ok and looks_like_key(path):
                    if not fingerprint_ok(runner, path):
                        if report:
                            report("{} 的公钥指纹不匹配, 已丢弃".format(url), "warn")
                        break
                    if dearmor(runner, path).ok or copy_ascii(runner, path).ok:
                        runner.emit("公钥已导入: " + url, "ok")
                        if report:
                            report("公钥来源: " + url, "ok")
                        return url
                break
        for server in KEY_SERVERS:
            if from_keyserver(runner, server):
                runner.emit("公钥已从密钥服务器导入: " + server, "ok")
                if report:
                    report("公钥来源: 密钥服务器 " + server, "ok")
                return server
        if report:
            report("获取 ROS 公钥失败, 后续 apt update 可能报 NO_PUBKEY", "err")
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
