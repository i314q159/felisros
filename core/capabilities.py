import os

WINDOWS = os.name == "nt"
ROLE = "control" if WINDOWS else "full"

CAPABILITIES = {
    "local_install": not WINDOWS,
    "local_update": not WINDOWS,
    "remote_install": True,
    "remote_update": True,
    "distribute": True,
    "generate_config": True,
    "self_update": not WINDOWS,
}

DENY_MESSAGES = {
    "local_install": "Windows 端是控制端，不允许在 Windows 本机安装 ROS；请通过局域网批量部署到 Linux 设备",
    "local_update": "Windows 端是控制端，不安装也不更新自身；可以给 Linux 设备分发更新",
    "self_update": "Windows 端是控制端，不安装也不更新自身；可以给 Linux 设备分发更新",
}


def manifest():
    return {
        "role": ROLE,
        "platform": platform_name(),
        "capabilities": dict(CAPABILITIES),
        "description": "控制端：只管理 Linux 设备，不在 Windows 本机安装或更新"
        if WINDOWS
        else "完整端：本机安装、设备互更新、批量部署全部可用",
    }


def platform_name():
    if WINDOWS:
        return "Windows"
    if os.name == "posix":
        return "Linux" if os.uname().sysname == "Linux" else os.uname().sysname
    return os.name


def allow(action):
    if CAPABILITIES.get(action, False):
        return True
    raise PermissionError(DENY_MESSAGES.get(action, "当前平台不支持此操作: " + str(action)))


def is_windows():
    return WINDOWS
