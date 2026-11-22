import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

from . import capabilities

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_FILE = "felisros.toml"
SCHEMA = "felisros/release/v1"
PACKAGE_NAME = "felisros-linux.tar.gz"
DIST_DIR = os.path.join(ROOT, "dist")
PACKAGE_PATH = os.path.join(DIST_DIR, PACKAGE_NAME)

COPY_FILES = ("app.py", "README.md", "build.py")
COPY_DIRS = ("core", "web", "examples", "packaging")
ROOT_FILES = ("app.py", "README.md", "build.py", "requirements.txt", VERSION_FILE, "install.sh", "uninstall.sh", "felisros.desktop")


def _version_from_text(text):
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip()
            continue
        if section != "app" or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == "version":
            return value.split("#", 1)[0].strip().strip("\"'")
    return ""


def _version_from_toml(path):
    """读取 felisros.toml 中 [app] version；Python 3.11+ 用 tomllib，旧版用轻量解析。"""
    try:
        with open(path, "rb") as handle:
            text = handle.read().decode("utf-8", "replace")
    except OSError:
        return ""
    if sys.version_info >= (3, 11):
        try:
            import tomllib
            return str(tomllib.loads(text).get("app", {}).get("version", "")) or _version_from_text(text)
        except (ValueError, TypeError):
            pass
    return _version_from_text(text)


VERSION = _version_from_toml(os.path.join(ROOT, VERSION_FILE)) or "0.0.0"
ROOT_DIRS = ("core", "web", "examples", "packaging", "bin")

_DIGEST_CACHE = {"key": "", "value": ""}
_BUILD_LOCK = threading.Lock()


def version_tuple(value):
    parts = []
    for item in str(value or "0").strip().split("."):
        digits = "".join(ch for ch in item if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def compare(left, right):
    a = version_tuple(left)
    b = version_tuple(right)
    length = max(len(a), len(b))
    a += (0,) * (length - len(a))
    b += (0,) * (length - len(b))
    if a > b:
        return 1
    if a < b:
        return -1
    return 0


def sha256(path):
    handle = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            handle.update(chunk)
    return handle.hexdigest()


def package_digest(path=None):
    target = path or PACKAGE_PATH
    if not os.path.isfile(target):
        return ""
    stat = os.stat(target)
    key = "{}:{}:{}".format(target, stat.st_size, int(stat.st_mtime))
    if _DIGEST_CACHE["key"] == key:
        return _DIGEST_CACHE["value"]
    value = sha256(target)
    _DIGEST_CACHE["key"] = key
    _DIGEST_CACHE["value"] = value
    return value


def ensure_package(force=False):
    with _BUILD_LOCK:
        sys.path.insert(0, ROOT)
        try:
            import build

            if force or build.needs_build():
                build.build()
        finally:
            sys.path.pop(0)
        if os.path.isfile(PACKAGE_PATH) and os.path.getsize(PACKAGE_PATH) > 0:
            return PACKAGE_PATH
        return ""


def local_manifest(base_url="", build_package=True):
    path = ensure_package() if build_package else PACKAGE_PATH
    digest = package_digest(path) if path else ""
    item = {
        "schema": SCHEMA,
        "version": VERSION,
        "package": PACKAGE_NAME,
        "sha256": digest,
        "size": os.path.getsize(path) if path and os.path.isfile(path) else 0,
        "role": capabilities.ROLE,
        "capabilities": capabilities.CAPABILITIES,
        "checked_at": int(time.time()),
    }
    if base_url:
        base = base_url.rstrip("/")
        item["download_url"] = base + "/download/" + PACKAGE_NAME
        item["install_url"] = base + "/install.sh"
    return item


def verify(path, expected):
    if not expected:
        return True
    return sha256(path) == str(expected).strip().lower()


def _safe_extract(archive, target):
    root = os.path.abspath(target)
    for member in archive.getmembers():
        name = member.name.replace("\\", "/")
        parts = [part for part in name.split("/") if part not in ("", ".", "..")]
        if name.startswith("/") or ":" in name.split("/")[0] or not parts:
            raise ValueError("安装包包含不安全路径，已拒绝更新")
    try:
        archive.extractall(target, filter="data")
    except TypeError:
        archive.extractall(target)


def update_config_path():
    from . import config

    return os.path.join(config.config_dir(), "update.json")


def load_auto():
    path = update_config_path()
    if not os.path.isfile(path):
        return {"enabled": False, "interval": 300, "seed": ""}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"enabled": False, "interval": 300, "seed": ""}
    if not isinstance(data, dict):
        return {"enabled": False, "interval": 300, "seed": ""}
    return {
        "enabled": bool(data.get("enabled")),
        "interval": max(60, min(3600, int(data.get("interval") or 300))),
        "seed": str(data.get("seed") or ""),
    }


def save_auto(settings):
    path = update_config_path()
    payload = {
        "enabled": bool(settings.get("enabled")),
        "interval": max(60, min(3600, int(settings.get("interval") or 300))),
        "seed": str(settings.get("seed") or ""),
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return payload


def _copy_root(source, target):
    inner = os.path.join(source, "app")
    if os.path.isdir(inner):
        source = inner
    os.makedirs(target, exist_ok=True)
    for name in ROOT_FILES:
        src = os.path.join(source, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(target, name))
    for name in ROOT_DIRS:
        src = os.path.join(source, name)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(target, name), dirs_exist_ok=True)
    src_dist = os.path.join(source, "dist")
    if os.path.isdir(src_dist):
        shutil.copytree(src_dist, os.path.join(target, "dist"), dirs_exist_ok=True)


def install_from(path, expected="", restart=False, port=8737, report=None):
    capabilities.allow("local_update")
    if not verify(path, expected):
        raise ValueError("安装包 SHA256 校验失败，已拒绝更新")
    parent = os.path.dirname(ROOT)
    if not os.access(parent, os.W_OK):
        raise PermissionError("程序目录不可写，无法自更新")
    staging = tempfile.mkdtemp(prefix="felisros-stage-", dir=parent)
    backup = ""
    try:
        with tarfile.open(path, "r:gz") as archive:
            _safe_extract(archive, staging)
        source = os.path.join(staging, "felisros-linux")
        if not os.path.isfile(os.path.join(source, "app", "app.py")) and not os.path.isfile(
            os.path.join(source, "app.py")
        ):
            raise ValueError("安装包结构不正确，缺少 felisros-linux/app/app.py")
        target = tempfile.mkdtemp(prefix="felisros-next-", dir=parent)
        os.rmdir(target)
        _copy_root(source, target)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = ROOT + ".backup-" + stamp
        os.rename(ROOT, backup)
        try:
            os.rename(target, ROOT)
        except OSError:
            os.rename(backup, ROOT)
            backup = ""
            raise
        _clean_backups(parent)
        result = {
            "ok": True,
            "version": read_version(ROOT),
            "from": VERSION,
            "backup": backup,
            "message": "更新已写入，正在重启服务",
        }
        if restart:
            threading.Timer(1.0, restart_service, args=(ROOT, port, True)).start()
        if report:
            report(result["message"], "ok")
        return result
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _clean_backups(parent):
    try:
        prefix = os.path.basename(ROOT) + ".backup-"
        names = [name for name in os.listdir(parent) if name.startswith(prefix)]
        names.sort(reverse=True)
        for name in names[3:]:
            shutil.rmtree(os.path.join(parent, name), ignore_errors=True)
    except OSError:
        pass


def read_version(app_root):
    return _version_from_toml(os.path.join(app_root, VERSION_FILE)) or VERSION


def restart_service(app_root, port=8737, exit_current=False):
    try:
        active = subprocess.run(
            ["systemctl", "--user", "is-active", "felisros.service"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if active.returncode == 0:
            subprocess.run(
                ["systemctl", "--user", "restart", "felisros.service"],
                capture_output=True,
                timeout=10,
            )
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        argv = [
            sys.executable,
            os.path.join(app_root, "app.py"),
            "--no-browser",
            "--port",
            str(port),
        ]
        command = argv
        if exit_current and os.name != "nt":
            # 先让当前进程退出再启动新实例，否则新实例抢不到端口
            command = [
                "sh",
                "-c",
                "sleep 1.5; exec " + " ".join(shlex.quote(part) for part in argv),
            ]
        subprocess.Popen(
            command,
            cwd=app_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        if exit_current:
            time.sleep(0.3)
            os._exit(0)
        return True
    except OSError:
        return False
