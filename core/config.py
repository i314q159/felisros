import copy
import ipaddress
import json
import os
import re

from . import ros
from .discovery import MAX_HOSTS, normalize_networks

VERSION_SCHEMA = "felisros/versions/v1"
DEPLOY_SCHEMA = "felisros/deploy/v1"
FLEET_SCHEMA = "felisros/fleet/v1"
CREDENTIALS_SCHEMA = "felisros/credentials/v1"

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
CODENAME_RE = re.compile(r"^[a-z][a-z0-9]{1,19}$")
PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._-]{0,79}$")
URL_RE = re.compile(r"^https?://[A-Za-z0-9._~:/?#@!$&()*+,;=%-]+$")
USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,31}$")
ENV_RE = re.compile(r"^\$\{([A-Z_][A-Z0-9_]*)\}$")

MEMORY = {"manifest": None, "fleet": None, "presets": None}
PRESET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


def _preset_name(value):
    text = _text(value, "预设名称", 40)
    if not PRESET_NAME_RE.fullmatch(text):
        raise ConfigError("预设名称只能用字母、数字、下划线和连字符")
    return text


def _preset_profile(raw):
    if not isinstance(raw, dict):
        raise ConfigError("凭据预设必须是对象")
    password = _text(raw.get("password") or "", "预设密码", 256)
    return {
        "name": _preset_name(raw.get("name")),
        "username": _user(raw.get("username") or "root", "预设用户名"),
        "password": password,
    }


def parse_presets(data):
    if not isinstance(data, dict):
        raise ConfigError("凭据预设文件必须是 JSON 对象")
    raw_profiles = data.get("profiles") or []
    if not isinstance(raw_profiles, list):
        raise ConfigError("profiles 必须是数组")
    profiles = []
    seen = set()
    for raw in raw_profiles:
        item = _preset_profile(raw)
        if item["name"] in seen:
            raise ConfigError("预设名称重复: " + item["name"])
        seen.add(item["name"])
        profiles.append(item)
    default = data.get("default") or ""
    if default and default not in seen:
        raise ConfigError("默认预设不存在: " + str(default))
    return {"schema": CREDENTIALS_SCHEMA, "default": default, "profiles": profiles}


def presets_path():
    return os.path.join(config_dir(), "presets.json")


def load_presets():
    path = presets_path()
    if not os.path.isfile(path):
        return empty_presets()
    try:
        return parse_presets(read_json(path))
    except ConfigError:
        return empty_presets()


def empty_presets():
    return {"schema": CREDENTIALS_SCHEMA, "default": "", "profiles": []}


def save_presets(data):
    parsed = parse_presets(data)
    MEMORY["presets"] = parsed
    path = presets_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(parsed, handle, ensure_ascii=False, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def upsert_preset(name, username, password, make_default=False):
    data = load_presets()
    profiles = [item for item in data["profiles"] if item["name"] != name]
    profiles.append(_preset_profile({"name": name, "username": username, "password": password}))
    default = name if make_default or not data["default"] else data["default"]
    return save_presets({"default": default, "profiles": profiles})


def delete_preset(name):
    data = load_presets()
    profiles = [item for item in data["profiles"] if item["name"] != name]
    default = "" if data["default"] == name else data["default"]
    return save_presets({"default": default, "profiles": profiles})


def resolve_preset(name):
    data = load_presets()
    for item in data["profiles"]:
        if item["name"] == name:
            password, _ = _secret(item["password"], "预设密码")
            return {"name": item["name"], "username": item["username"], "password": password}
    return None


def redact_presets(data):
    return {
        "schema": data.get("schema") or CREDENTIALS_SCHEMA,
        "default": data.get("default") or "",
        "profiles": [
            {
                "name": item["name"],
                "username": item["username"],
                "password": "",
                "has_password": bool(item["password"]),
            }
            for item in data.get("profiles") or []
        ],
    }


class ConfigError(ValueError):
    pass


def read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        raise ConfigError("读取文件失败: {}".format(exc))
    except ValueError as exc:
        raise ConfigError("JSON 格式错误: {}".format(exc))


def _text(value, field, limit=80):
    if not isinstance(value, str):
        raise ConfigError("{} 必须是文本".format(field))
    if len(value) > limit:
        raise ConfigError("{} 过长".format(field))
    return value


def _name(value, field):
    if not isinstance(value, str) or not NAME_RE.fullmatch(value):
        raise ConfigError("{} 只能用小写字母、数字和连字符: {!r}".format(field, value))
    return value


def _codename(value):
    if not isinstance(value, str) or not CODENAME_RE.fullmatch(value):
        raise ConfigError("系统代号不合法: {!r}".format(value))
    return value


def _package(value, field):
    if not isinstance(value, str) or not PACKAGE_RE.fullmatch(value):
        raise ConfigError("{} 不是合法的软件包名: {!r}".format(field, value))
    return value


def _url(value, field):
    if not isinstance(value, str) or not URL_RE.fullmatch(value):
        raise ConfigError("{} 不是安全的 http(s) 地址: {!r}".format(field, value))
    return value


def _list(value, field):
    if not isinstance(value, list):
        raise ConfigError("{} 必须是数组".format(field))
    return value


def _status(value):
    if value in (None, "", "LTS", "lts", ros.LTS, 1):
        return ros.LTS
    if value in ("EOL", "eol", ros.EOL, 0):
        return ros.EOL
    raise ConfigError("版本状态只能是 LTS 或 EOL")


def parse_versions(data):
    if not isinstance(data, dict):
        raise ConfigError("版本清单必须是 JSON 对象")
    if data.get("schema") != VERSION_SCHEMA:
        raise ConfigError("版本清单 schema 必须是 " + VERSION_SCHEMA)
    label = _text(data.get("name") or "导入的版本清单", "清单名称")
    result = {"schema": VERSION_SCHEMA, "name": label, "versions": [], "mirrors": [], "support": {}}

    for raw in _list(data.get("versions", []), "versions"):
        if not isinstance(raw, dict):
            raise ConfigError("versions 里的每一项都必须是对象")
        raw_packages = raw.get("packages") or {}
        if not isinstance(raw_packages, dict):
            raise ConfigError("packages 必须是对象")
        packages = {}
        for key in ("desktop", "base"):
            if raw_packages.get(key):
                packages[key] = _package(raw_packages[key], "packages." + key)
        deps = [_package(item, "deps") for item in _list(raw.get("deps", []), "deps")]
        codenames = [_codename(item) for item in _list(raw.get("codenames", []), "codenames")]
        result["versions"].append(
            {
                "name": _name(raw.get("name"), "版本名"),
                "ros": "ROS1" if raw.get("ros") == "ROS1" else "ROS2",
                "status": _status(raw.get("status")),
                "codenames": codenames,
                "packages": packages,
                "deps": deps,
                "recommended": bool(raw.get("recommended")),
            }
        )

    for raw in _list(data.get("mirrors", []), "mirrors"):
        if not isinstance(raw, dict):
            raise ConfigError("mirrors 里的每一项都必须是对象")
        entry = {
            "name": _name(raw.get("name"), "镜像名称"),
            "label": _text(raw.get("label") or raw.get("name"), "镜像说明", 40),
        }
        for kind in (ros.ROS1, ros.ROS2):
            if raw.get(kind):
                entry[kind] = _url(raw[kind], kind + " 镜像地址")
        if ros.ROS1 not in entry and ros.ROS2 not in entry:
            raise ConfigError("镜像 {} 至少要填一个 ROS1 或 ROS2 地址".format(entry["name"]))
        result["mirrors"].append(entry)

    known = set(ros.MIRRORS) | {item["name"] for item in result["mirrors"]}
    for codename, names in (data.get("support") or {}).items():
        codename = _codename(codename)
        picked = []
        for name in _list(names, "support." + codename):
            name = _name(name, "support 镜像名")
            if name not in known:
                raise ConfigError("support 引用了未定义的镜像: " + name)
            picked.append(name)
        result["support"][codename] = picked

    return result


def parse_deploy(data):
    if not isinstance(data, dict):
        raise ConfigError("部署配置必须是 JSON 对象")
    if data.get("schema") != DEPLOY_SCHEMA:
        raise ConfigError("部署配置 schema 必须是 " + DEPLOY_SCHEMA)
    version = data.get("version") or "auto"
    if version != "auto":
        version = _name(version, "version")
    kind = data.get("kind") or "auto"
    if kind not in ("auto", "desktop", "base"):
        raise ConfigError("kind 只能是 auto、desktop 或 base")
    mirror = data.get("mirror") or "auto"
    if mirror != "auto":
        mirror = _name(mirror, "mirror")
    change = data.get("change_system_source", True)
    if not isinstance(change, bool):
        raise ConfigError("change_system_source 必须是 true 或 false")
    extras = [_package(item, "extra_packages") for item in _list(data.get("extra_packages", []), "extra_packages")]
    if len(extras) > 30:
        raise ConfigError("extra_packages 最多 30 个")
    return {
        "schema": DEPLOY_SCHEMA,
        "name": _text(data.get("name") or "felisros 部署配置", "配置名称"),
        "mode": "deploy",
        "version": version,
        "kind": kind,
        "mirror": mirror,
        "change_system_source": change,
        "extra_packages": extras,
    }


def versions_summary(manifest):
    lines = ["清单: " + manifest["name"]]
    lines.append("版本: " + ("、".join(item["name"] for item in manifest["versions"]) or "无"))
    lines.append("镜像: " + ("、".join(item["name"] for item in manifest["mirrors"]) or "无"))
    lines.append("支持映射: " + ("、".join(manifest["support"]) or "无"))
    return lines


def deploy_summary(config):
    return [
        "配置: " + config["name"],
        "ROS 版本: " + config["version"],
        "安装内容: " + {"auto": "自动", "desktop": "桌面版", "base": "基础版"}[config["kind"]],
        "ROS 镜像: " + config["mirror"],
        "系统源: " + ("换国内镜像" if config["change_system_source"] else "保持不变"),
        "额外软件包: " + ("、".join(config["extra_packages"]) or "无"),
    ]


def _user(value, field):
    text = _text(value, field, 32)
    if not USER_RE.fullmatch(text):
        raise ConfigError("{} 不是合法的用户名: {!r}".format(field, text))
    return text


def _secret(value, field):
    if value in (None, ""):
        return "", ""
    text = _text(value, field, 256)
    match = ENV_RE.fullmatch(text)
    if match:
        return os.environ.get(match.group(1), ""), text
    return text, ""


def _port(value, default):
    try:
        port = int(value)
    except (TypeError, ValueError):
        return default
    return port if 1 <= port <= 65535 else default


FLEET_TASKS = ("software", "ros", "rollout", "update", "update_rollout")


def parse_fleet(data):
    if not isinstance(data, dict):
        raise ConfigError("批量部署清单必须是 JSON 对象")
    if data.get("schema") != FLEET_SCHEMA:
        raise ConfigError("批量部署清单 schema 必须是 " + FLEET_SCHEMA)

    network = data.get("network") or "auto"
    if network != "auto":
        try:
            network = [str(net) for net in normalize_networks(network)]
        except ValueError as exc:
            raise ConfigError(str(exc))

    defaults = data.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ConfigError("defaults 必须是对象")
    default_password, default_password_ref = _secret(defaults.get("password"), "默认密码")
    default_sudo, default_sudo_ref = _secret(defaults.get("sudo_password"), "默认 sudo 密码")
    default_user = _user(defaults.get("username") or "root", "默认用户名")

    devices = []
    for raw in _list(data.get("devices", []), "devices"):
        if not isinstance(raw, dict):
            raise ConfigError("devices 里的每一项都必须是对象")
        try:
            address = ipaddress.ip_address(str(raw.get("ip") or ""))
        except ValueError:
            raise ConfigError("设备 IP 不合法: {!r}".format(raw.get("ip")))
        if address.version != 4:
            raise ConfigError("目前只支持 IPv4 设备")
        password, password_ref = _secret(raw.get("password"), "设备密码")
        sudo, sudo_ref = _secret(raw.get("sudo_password"), "设备 sudo 密码")
        devices.append(
            {
                "ip": str(address),
                "alias": _text(raw.get("alias") or "", "设备备注", 40),
                "username": _user(raw["username"], "设备用户名") if raw.get("username") else default_user,
                "password": password,
                "password_ref": password_ref,
                "sudo_password": sudo,
                "sudo_password_ref": sudo_ref,
                "selected": bool(raw.get("selected", True)),
            }
        )

    deploy = None
    if data.get("deploy"):
        deploy = parse_deploy(data["deploy"])
    versions = None
    if data.get("versions"):
        versions = parse_versions(data["versions"])

    return {
        "schema": FLEET_SCHEMA,
        "name": _text(data.get("name") or "felisros 批量部署", "清单名称"),
        "network": network,
        "defaults": {
            "username": default_user,
            "password": default_password,
            "password_ref": default_password_ref,
            "sudo_password": default_sudo,
            "sudo_password_ref": default_sudo_ref,
        },
        "devices": devices,
        "deploy": deploy,
        "versions": versions,
        "task": data.get("task") if data.get("task") in FLEET_TASKS else ("rollout" if deploy else ("ros" if versions else "software")),
        "update_seed": _text(data.get("update_seed") or "", "更新源地址", 200),
        "preset": _preset_name(data["preset"]) if data.get("preset") else "",
        "port": _port(data.get("port"), 8737),
        "autostart": bool(data.get("autostart", True)),
    }


def fleet_summary(fleet):
    network = fleet["network"]
    if isinstance(network, (list, tuple)):
        network = ", ".join(str(item) for item in network)
    lines = [
        "清单: " + fleet["name"],
        "网段: " + str(network),
        "设备: {} 台，选中 {} 台".format(
            len(fleet["devices"]), sum(1 for item in fleet["devices"] if item["selected"])
        ),
        "默认用户: " + fleet["defaults"]["username"],
        "默认密码: " + ("已设置" if fleet["defaults"]["password"] else "未设置"),
    ]
    if fleet["deploy"]:
        lines.append("部署版本: " + fleet["deploy"]["version"])
    if fleet.get("preset"):
        lines.append("凭据预设: " + fleet["preset"])
    if fleet.get("task") in ("update", "update_rollout"):
        lines.append("更新源: " + (fleet.get("update_seed") or "控制端本机"))
    return lines


def redact_fleet(fleet):
    result = copy.deepcopy(fleet)
    defaults = result["defaults"]
    defaults["has_password"] = bool(defaults["password"])
    defaults["has_sudo_password"] = bool(defaults["sudo_password"])
    defaults["password"] = ""
    defaults["sudo_password"] = ""
    for device in result["devices"]:
        device["has_password"] = bool(device["password"])
        device["has_sudo_password"] = bool(device["sudo_password"])
        device["password"] = ""
        device["sudo_password"] = ""
    return result


def config_dir():
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "felisros")


def versions_path():
    return os.path.join(config_dir(), "versions.json")


def current():
    return MEMORY["manifest"]


def apply(manifest):
    ros.reset_overlays()
    ros.register(manifest)
    MEMORY["manifest"] = manifest


def save(manifest):
    apply(manifest)
    path = versions_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    return path


def load():
    path = versions_path()
    if not os.path.isfile(path):
        return None
    try:
        manifest = parse_versions(read_json(path))
    except (ConfigError, OSError):
        return None
    apply(manifest)
    return manifest


def fleet_path():
    return os.path.join(config_dir(), "fleet.json")


def clear():
    MEMORY["manifest"] = None
    MEMORY["fleet"] = None
    ros.reset_overlays()
    path = versions_path()
    if os.path.isfile(path):
        os.unlink(path)
    return True


def save_fleet(fleet):
    MEMORY["fleet"] = fleet
    path = fleet_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(fleet, handle, ensure_ascii=False, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def load_fleet():
    path = fleet_path()
    if not os.path.isfile(path):
        return None
    try:
        fleet = parse_fleet(read_json(path))
    except (ConfigError, OSError):
        return None
    MEMORY["fleet"] = fleet
    return fleet


def current_fleet():
    return MEMORY["fleet"]


def clear_fleet():
    MEMORY["fleet"] = None
    path = fleet_path()
    if os.path.isfile(path):
        os.unlink(path)
    return True
