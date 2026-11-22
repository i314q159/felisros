import glob
import os
import platform
import shutil
import subprocess

ARCH_TABLE = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv8l": "arm64",
    "armv7l": "armhf",
    "armv6l": "armhf",
    "i686": "i386",
    "i386": "i386",
}

CONTROL_PROFILE = {
    "distro": "ubuntu",
    "version": "24.04",
    "codename": "noble",
}


def parse_key_values(path):
    data = {}
    if not os.path.exists(path):
        return data
    with open(path, encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def normalize_arch(machine):
    return ARCH_TABLE.get(machine.lower(), machine.lower())


def installed_ros():
    return sorted(path.split("/")[3] for path in glob.glob("/opt/ros/*/setup.bash"))


def is_root():
    return hasattr(os, "geteuid") and os.geteuid() == 0


def sudo_state():
    """本机 sudo 现状：root 直接可用、免密可用，还是必须填密码。"""
    if is_root():
        return {"root": True, "available": True, "passwordless": True}
    if not shutil.which("sudo"):
        return {"root": False, "available": False, "passwordless": False}
    try:
        probe = subprocess.run(
            ["sudo", "-n", "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        passwordless = probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        passwordless = False
    return {"root": False, "available": True, "passwordless": passwordless}


def free_space_gb(path="/"):
    try:
        return round(shutil.disk_usage(path).free / 1024 ** 3, 1)
    except OSError:
        return None


class SystemInfo:
    def __init__(self):
        self.control_host = platform.system() != "Linux"
        if self.control_host:
            self.distro = CONTROL_PROFILE["distro"]
            self.pretty = "{} 控制端（局域网部署）".format(platform.system())
            self.version = CONTROL_PROFILE["version"]
            self.codename = CONTROL_PROFILE["codename"]
            self.arch = normalize_arch(platform.machine())
            self.kernel = ""
            self.free_gb = None
            self.ros_versions = []
            return

        release = parse_key_values("/etc/os-release")
        self.distro = (release.get("ID") or "").lower()
        self.pretty = release.get("PRETTY_NAME") or self.distro
        self.version = release.get("VERSION_ID") or ""
        self.codename = (
            release.get("VERSION_CODENAME")
            or release.get("UBUNTU_CODENAME")
            or release.get("DEBIAN_CODENAME")
            or parse_key_values("/etc/lsb-release").get("DISTRIB_CODENAME")
            or ""
        )
        self.arch = normalize_arch(platform.machine())
        self.kernel = platform.release()
        self.free_gb = free_space_gb()
        self.ros_versions = installed_ros()

    def to_dict(self):
        return {
            "control_host": self.control_host,
            "distro": self.distro,
            "pretty": self.pretty,
            "version": self.version,
            "codename": self.codename,
            "arch": self.arch,
            "kernel": self.kernel,
            "free_gb": self.free_gb,
            "ros_versions": self.ros_versions,
        }
