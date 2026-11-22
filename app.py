import argparse
import hashlib
import json
import mimetypes
import os
import re
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from core import capabilities, config, discovery, peers, release, ros
from core.config import ConfigError
from core.fleet import Fleet
from core.installer import STEPS, EventBus, Installer
from core.system import SystemInfo

# 强制 stdout/stderr 使用 UTF-8：目标机 locale 为 C/POSIX 时 Python 会退化为
# latin-1 编码，一旦 print 中文就抛 UnicodeEncodeError 直接令服务崩溃。
for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_ROOT = os.path.join(ROOT, "web")

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/gzip", ".gz")

PACKAGE_NAME = "felisros-linux.tar.gz"
DIST_PATH = os.path.join(ROOT, "dist", PACKAGE_NAME)
LOG_DIR = os.path.join(ROOT, "log")
OUT_LOG = os.path.join(LOG_DIR, "app-run.log")
ERR_LOG = os.path.join(LOG_DIR, "app-run.err.log")
_RUNTIME_LOGS_READY = False


class _TeeStream:
    def __init__(self, stream, path):
        self._stream = stream
        self._lock = threading.Lock()
        self._file = open(path, "a", encoding="utf-8", buffering=1)

    def write(self, text):
        with self._lock:
            written = self._stream.write(text)
            self._file.write(text)
            self._file.flush()
        return written

    def flush(self):
        with self._lock:
            self._stream.flush()
            self._file.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def setup_runtime_logs():
    global _RUNTIME_LOGS_READY
    if _RUNTIME_LOGS_READY:
        return
    _RUNTIME_LOGS_READY = True
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        out = _TeeStream(sys.stdout, OUT_LOG)
        err = _TeeStream(sys.stderr, ERR_LOG)
    except OSError:
        return
    sys.stdout = out
    sys.stderr = err


def package_digest():
    if not os.path.isfile(DIST_PATH):
        return ""
    handle = hashlib.sha256()
    with open(DIST_PATH, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            handle.update(chunk)
    return handle.hexdigest()


def lan_addresses():
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in found:
                found.append(ip)
    except OSError:
        pass
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("223.5.5.5", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if not ip.startswith("127.") and ip not in found:
            found.insert(0, ip)
    except OSError:
        pass
    return found


INSTALL_SCRIPT = """#!/bin/sh
set -eu
say() { printf '[felisros] %s\\n' "$*"; }
die() { printf '[felisros] 安装失败: %s\\n' "$*" >&2; exit 1; }

MACHINE=$(uname -m 2>/dev/null || true)
DPKGARCH=$(dpkg --print-architecture 2>/dev/null || true)
case "$MACHINE $DPKGARCH" in
  *x86_64*|*amd64*) ARCH="amd64 / x86-64" ;;
  *aarch64*|*arm64*) ARCH="arm64 / ARM 64-bit" ;;
  *armv7*|*armhf*) ARCH="armhf / ARM 32-bit" ;;
  *) ARCH="$MACHINE/$DPKGARCH (未识别，仍尝试安装)" ;;
esac
say "检测到架构: $ARCH"

command -v python3 >/dev/null 2>&1 || die "需要 python3，请先安装: sudo apt install python3"
command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 || die "需要 curl 或 wget"

TMP=$(mktemp -d "${TMPDIR:-/tmp}/felisros-install.XXXXXX")
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
PKG="$TMP/felisros-linux.tar.gz"
BASE="__BASE_URL__"
SUM="__SHA256__"

say "从 $BASE 下载安装包"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL --retry 3 --max-time 300 "$BASE/download/felisros-linux.tar.gz" -o "$PKG" || die "下载失败"
else
  wget -q -T 300 -O "$PKG" "$BASE/download/felisros-linux.tar.gz" || die "下载失败"
fi

if command -v sha256sum >/dev/null 2>&1; then
  ACTUAL=$(sha256sum "$PKG" | awk '{print $1}')
elif command -v shasum >/dev/null 2>&1; then
  ACTUAL=$(shasum -a 256 "$PKG" | awk '{print $1}')
else
  ACTUAL=""
fi
if [ -n "$SUM" ] && [ -n "$ACTUAL" ] && [ "$ACTUAL" != "$SUM" ]; then
  die "校验和不匹配，安装包可能已损坏"
fi
[ -z "$ACTUAL" ] && say "系统没有 sha256sum，跳过校验"

tar xzf "$PKG" -C "$TMP" || die "解包失败"
sh "$TMP/felisros-linux/install.sh" || die "安装脚本执行失败"
say "安装完成：felisros"
say "运行: ~/.local/bin/felisros"
"""


def install_script(base_url):
    return INSTALL_SCRIPT.replace("__BASE_URL__", base_url.rstrip("/")).replace(
        "__SHA256__", package_digest()
    )


def parse_text(payload):
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ConfigError("请先选择文件或粘贴 JSON 内容")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ConfigError("JSON 格式错误: {}".format(exc))


def deploy_template(system):
    return {
        "schema": config.DEPLOY_SCHEMA,
        "name": "felisros 部署配置",
        "version": ros.recommend(system.codename) or "auto",
        "kind": "auto",
        "mirror": "auto",
        "change_system_source": True,
        "extra_packages": [],
    }


def split_packages(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not isinstance(value, str):
        return []
    return [item for item in re.split(r"[\s,，;；]+", value.strip()) if item]


def build_config(payload):
    if not isinstance(payload, dict):
        raise ConfigError("生成参数必须是 JSON 对象")
    remote = bool(payload.get("remote"))
    version = str(payload.get("version") or "auto").strip() or "auto"
    kind = str(payload.get("kind") or "auto").strip() or "auto"
    mirror = str(payload.get("mirror") or "auto").strip() or "auto"
    change = payload.get("change_system_source", True)
    if not isinstance(change, bool):
        change = str(change).strip().lower() in ("true", "1", "yes", "on")

    raw_deploy = {
        "schema": config.DEPLOY_SCHEMA,
        "name": str(payload.get("name") or "felisros 部署配置").strip()
        or "felisros 部署配置",
        "version": version,
        "kind": kind,
        "mirror": mirror,
        "change_system_source": change,
        "extra_packages": split_packages(payload.get("extra_packages")),
    }
    deploy = config.parse_deploy(raw_deploy)

    if remote:
        name = (
            str(payload.get("name") or "felisros 批量部署").strip()
            or "felisros 批量部署"
        )
        defaults = payload.get("defaults") or {}
        if not isinstance(defaults, dict):
            raise ConfigError("默认凭据必须是对象")
        raw_devices = payload.get("devices") or []
        if not isinstance(raw_devices, list):
            raise ConfigError("设备列表必须是数组")
        devices = []
        for item in raw_devices:
            if not isinstance(item, dict) or not str(item.get("ip") or "").strip():
                continue
            devices.append(
                {
                    "ip": str(item["ip"]).strip(),
                    "alias": str(item.get("alias") or "").strip(),
                    "username": str(item.get("username") or "").strip(),
                    "password": str(item.get("password") or ""),
                    "sudo_password": str(item.get("sudo_password") or ""),
                    "selected": bool(item.get("selected", True)),
                }
            )
        raw_defaults = {
            "username": str(defaults.get("username") or "root").strip() or "root",
            "password": str(defaults.get("password") or ""),
            "sudo_password": str(defaults.get("sudo_password") or ""),
        }
        raw_fleet = {
            "schema": config.FLEET_SCHEMA,
            "name": name,
            "network": str(payload.get("network") or "auto").strip() or "auto",
            "defaults": raw_defaults,
            "devices": devices,
            "deploy": deploy,
            "preset": str(payload.get("preset") or "").strip(),
        }
        checked = config.parse_fleet(raw_fleet)
        raw_fleet["name"] = checked["name"]
        raw_fleet["network"] = checked["network"]
        try:
            workers = int(payload.get("workers") or 3)
        except (TypeError, ValueError):
            workers = 3
        raw_fleet["workers"] = max(1, min(99, workers))
        task = str(payload.get("task") or "rollout").strip()
        if task not in config.FLEET_TASKS:
            raise ConfigError("远程任务只能是 " + "、".join(config.FLEET_TASKS))
        raw_fleet["task"] = task
        raw_fleet["update_seed"] = str(payload.get("update_seed") or "").strip()
        raw_fleet["preset"] = str(payload.get("preset") or "").strip()
        try:
            port = int(payload.get("port") or 8737)
        except (TypeError, ValueError):
            port = 8737
        raw_fleet["port"] = max(1, min(65535, port))
        raw_fleet["autostart"] = bool(payload.get("autostart", True))
        summary = config.fleet_summary(checked)
        labels = {
            "software": "只分发 felisros 软件（启动 + 开机自启）",
            "ros": "只在目标机临时运行安装器安装 ROS",
            "rollout": "先分发 felisros 软件，再遥控安装 ROS 并回传结果",
            "update": "让目标机从更新源拉取 felisros 最新版本并重启服务",
            "update_rollout": "先更新 felisros，再遥控安装 ROS 并回传结果",
        }
        summary.append("远程任务: " + labels[task])
        if task in ("update", "update_rollout"):
            summary.append("更新源: " + (raw_fleet["update_seed"] or "控制端本机"))
        if raw_fleet["preset"]:
            summary.append("凭据预设: " + raw_fleet["preset"])
        summary.append("并发数量: {} 台".format(raw_fleet["workers"]))
        if task in ("software", "rollout", "update", "update_rollout"):
            summary.append("服务端口: {}".format(raw_fleet["port"]))
        return {
            "ok": True,
            "schema": config.FLEET_SCHEMA,
            "filename": "felisros-fleet.json",
            "text": json.dumps(raw_fleet, ensure_ascii=False, indent=2),
            "summary": summary,
        }

    return {
        "ok": True,
        "schema": config.DEPLOY_SCHEMA,
        "filename": "felisros-deploy.json",
        "text": json.dumps(deploy, ensure_ascii=False, indent=2),
        "summary": config.deploy_summary(deploy),
    }


class Handler(BaseHTTPRequestHandler):
    system = None
    installer = None
    fleet = None
    beacon = None
    updater = None
    update_bus = None
    lan_mode = False
    port = 8737
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        if not os.path.isfile(path):
            return self.send_json({"error": "not found"}, 404)
        with open(path, "rb") as handle:
            body = handle.read()
        self.send_response(200)
        self.send_header(
            "Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream"
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text, content_type):
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def api_lan(self):
        addresses = lan_addresses()
        host = addresses[0] if addresses else "127.0.0.1"
        base = "http://{}:{}".format(host, self.port)
        try:
            package = release.ensure_package()
        except (OSError, ValueError) as exc:
            print("自动打包失败: " + str(exc))
            package = ""
        self.send_json(
            {
                "addresses": addresses,
                "port": self.port,
                "url": base + "/",
                "download_url": base + "/download/" + PACKAGE_NAME,
                "install_url": base + "/install.sh",
                "command": "curl -fsSL {}/install.sh | sh".format(base),
                "checksum": package_digest() if package else "",
                "package_exists": bool(package),
                "lan_mode": self.lan_mode,
                "version": release.VERSION,
                "role": capabilities.ROLE,
                "release_url": base + "/api/release",
                "update_manual": "在已安装 felisros 的 Linux 设备上打开界面 → 设备互更新 → 选择更新源",
            }
        )

    def fail(self, exc):
        return self.send_json({"ok": False, "message": str(exc)}, 400)

    def denied(self, exc):
        return self.send_json({"ok": False, "message": str(exc)}, 403)

    def update_report(self, text, level="out"):
        self.update_bus.push({"type": "update_log", "text": str(text), "level": level})

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        if route == "/api/lan":
            return self.api_lan()
        if route == "/install.sh":
            host = (lan_addresses() or ["127.0.0.1"])[0]
            try:
                release.ensure_package()
            except (OSError, ValueError) as exc:
                print("自动打包失败: " + str(exc))
            return self.send_text(
                install_script("http://{}:{}".format(host, self.port)),
                "text/x-shellscript; charset=utf-8",
            )
        if route == "/download/" + PACKAGE_NAME:
            try:
                package = release.ensure_package()
            except (OSError, ValueError) as exc:
                print("自动打包失败: " + str(exc))
                package = ""
            if not package:
                return self.send_json(
                    {"error": "安装包自动打包失败，请检查源码后重试"}, 500
                )
            return self.send_file(package)
        if route == "/api/release":
            host = (lan_addresses() or ["127.0.0.1"])[0]
            return self.send_json(
                release.local_manifest("http://{}:{}".format(host, self.port))
            )
        if route == "/api/peers":
            return self.send_json(
                {
                    "version": release.VERSION,
                    "role": capabilities.ROLE,
                    "capabilities": capabilities.manifest(),
                    "peers": self.beacon.list() if self.beacon else [],
                    "auto": self.updater.snapshot() if self.updater else None,
                }
            )
        if route == "/api/update/events":
            since = parse_qs(parsed.query).get("since") or ["0"]
            try:
                seq = int(since[0])
            except ValueError:
                seq = 0
            return self.send_json(
                {
                    "events": self.update_bus.since(seq) if self.update_bus else [],
                    "version": release.VERSION,
                }
            )
        if route == "/api/presets":
            return self.send_json(config.redact_presets(config.load_presets()))
        if route == "/api/env":
            return self.api_env()
        if route == "/api/config":
            manifest = config.current()
            return self.send_json(
                {
                    "deploy": deploy_template(self.system),
                    "versions": config.versions_summary(manifest) if manifest else None,
                    "versions_path": config.versions_path(),
                }
            )
        if route == "/api/network":
            networks = discovery.default_networks()
            return self.send_json(
                {
                    "network": networks[0]["cidr"] if networks else "",
                    "networks": networks,
                }
            )
        if route == "/api/fleet":
            current = config.current_fleet()
            return self.send_json(
                {
                    "fleet": config.redact_fleet(current) if current else None,
                    "summary": config.fleet_summary(current) if current else None,
                    "state": self.fleet.snapshot(),
                    "path": config.fleet_path(),
                }
            )
        if route == "/api/fleet/report":
            state = self.fleet.snapshot()
            return self.send_json(
                {
                    "ok": True,
                    "task": state.get("task") or "",
                    "reports": state.get("reports") or [],
                    "ok_count": state["ok_count"],
                    "fail_count": state["fail_count"],
                }
            )
        if route == "/api/fleet/events":
            since = parse_qs(parsed.query).get("since") or ["0"]
            try:
                seq = int(since[0])
            except ValueError:
                seq = 0
            return self.send_json(
                {"events": self.fleet.bus.since(seq), "state": self.fleet.snapshot()}
            )
        if route == "/api/state":
            return self.send_json(self.installer.snapshot())
        if route == "/api/events":
            since = parse_qs(parsed.query).get("since") or ["0"]
            try:
                seq = int(since[0])
            except ValueError:
                seq = 0
            return self.send_json(
                {
                    "events": self.installer.bus.since(seq),
                    "state": self.installer.snapshot(),
                }
            )
        if route in ("/", "/index.html"):
            return self.send_file(os.path.join(WEB_ROOT, "index.html"))
        target = os.path.normpath(os.path.join(WEB_ROOT, route.lstrip("/")))
        if not target.startswith(WEB_ROOT):
            return self.send_json({"error": "forbidden"}, 403)
        return self.send_file(target)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            payload = {}
        route = parsed.path

        if route == "/api/peers/probe":
            ips = payload.get("ips") or []
            if not isinstance(ips, list):
                return self.fail(ConfigError("ips 必须是数组"))
            try:
                port = int(payload.get("port") or 8737)
            except (TypeError, ValueError):
                port = 8737
            found = peers.probe(ips, port)
            if self.beacon:
                self.beacon.add(found)
            return self.send_json(
                {"ok": True, "peers": self.beacon.list() if self.beacon else found}
            )

        if route == "/api/update/check":
            try:
                found = peers.check_update(
                    payload.get("seed") or "",
                    release.VERSION,
                    self.beacon.list() if self.beacon else [],
                )
            except (ValueError, OSError) as exc:
                return self.fail(exc)
            found["can_apply"] = capabilities.CAPABILITIES["local_update"]
            return self.send_json(found)

        if route == "/api/update/apply":
            if not capabilities.CAPABILITIES["local_update"]:
                return self.denied(
                    PermissionError(capabilities.DENY_MESSAGES["local_update"])
                )
            restart = payload.get("restart")
            if restart is None:
                restart = True
            self.update_report("开始从更新源拉取新版本", "info")
            result = peers.apply_update(
                payload.get("seed") or "",
                expected=payload.get("sha256") or "",
                restart=bool(restart),
                port=self.port,
                report=self.update_report,
            )
            self.update_report(
                result.get("message") or result.get("reason") or "更新结束",
                "ok" if result.get("ok") else "err",
            )
            return self.send_json(result, 200 if result.get("ok") else 400)

        if route == "/api/update/settings":
            if not capabilities.CAPABILITIES["local_update"]:
                return self.denied(
                    PermissionError(capabilities.DENY_MESSAGES["local_update"])
                )
            settings = self.updater.configure(
                payload,
                peers=lambda: self.beacon.list() if self.beacon else [],
            )
            return self.send_json({"ok": True, "settings": settings})

        if route == "/api/start":
            if not capabilities.CAPABILITIES["local_install"]:
                return self.denied(
                    PermissionError(capabilities.DENY_MESSAGES["local_install"])
                )
            try:
                body = dict(payload) if isinstance(payload, dict) else payload
                sudo_password = ""
                if isinstance(body, dict):
                    sudo_password = str(body.pop("sudo_password", "") or "")
                deploy = config.parse_deploy(body)
            except ConfigError as exc:
                return self.fail(exc)
            ok, message = self.installer.start(deploy, sudo_password)
            return self.send_json({"ok": ok, "message": message}, 200 if ok else 409)

        if route == "/api/cancel":
            self.installer.cancel()
            return self.send_json({"ok": True})

        if route == "/api/versions/preview":
            try:
                manifest = config.parse_versions(parse_text(payload))
            except ConfigError as exc:
                return self.fail(exc)
            return self.send_json(
                {"ok": True, "summary": config.versions_summary(manifest)}
            )

        if route == "/api/versions/import":
            try:
                manifest = config.parse_versions(parse_text(payload))
                path = config.save(manifest)
            except (ConfigError, OSError) as exc:
                return self.fail(exc)
            return self.send_json(
                {
                    "ok": True,
                    "summary": config.versions_summary(manifest),
                    "path": path,
                    "message": "版本清单已导入，重启后仍有效",
                }
            )

        if route == "/api/versions/clear":
            try:
                config.clear()
            except OSError as exc:
                return self.fail(exc)
            return self.send_json({"ok": True, "message": "已清除导入的版本清单"})

        if route == "/api/config/preview":
            try:
                deploy = config.parse_deploy(parse_text(payload))
            except ConfigError as exc:
                return self.fail(exc)
            return self.send_json(
                {"ok": True, "config": deploy, "summary": config.deploy_summary(deploy)}
            )

        if route == "/api/config/build":
            try:
                built = build_config(payload)
            except ConfigError as exc:
                return self.fail(exc)
            return self.send_json(built)

        if route == "/api/presets/save":
            try:
                password = payload.get("password") or ""
                if not password and payload.get("keep_password"):
                    current = config.load_presets()
                    for item in current.get("profiles") or []:
                        if item["name"] == (payload.get("name") or ""):
                            password = item["password"]
                            break
                config.upsert_preset(
                    payload.get("name") or "",
                    payload.get("username") or "",
                    password,
                    make_default=bool(payload.get("default")),
                )
            except (ConfigError, OSError) as exc:
                return self.fail(exc)
            return self.send_json(
                {
                    "ok": True,
                    "presets": config.redact_presets(config.load_presets()),
                    "message": "已保存凭据预设",
                }
            )

        if route == "/api/presets/delete":
            try:
                config.delete_preset(payload.get("name") or "")
            except (ConfigError, OSError) as exc:
                return self.fail(exc)
            return self.send_json(
                {
                    "ok": True,
                    "presets": config.redact_presets(config.load_presets()),
                    "message": "已删除凭据预设",
                }
            )

        if route == "/api/discover":
            logs = []
            try:
                requested = payload.get("networks")
                if requested is None:
                    requested = payload.get("network")
                devices = discovery.discover(requested, report=logs.append)
                if payload.get("linux_only"):
                    devices = [item for item in devices if item.get("linux")]
            except ValueError as exc:
                return self.fail(exc)
            return self.send_json({"ok": True, "devices": devices, "logs": logs})

        if route == "/api/fleet/import":
            try:
                fleet = config.parse_fleet(parse_text(payload))
                path = config.save_fleet(fleet)
            except (ConfigError, OSError) as exc:
                return self.fail(exc)
            return self.send_json(
                {
                    "ok": True,
                    "fleet": config.redact_fleet(fleet),
                    "summary": config.fleet_summary(fleet),
                    "path": path,
                    "message": "设备清单已导入",
                }
            )

        if route == "/api/fleet/save":
            try:
                fleet = config.parse_fleet(payload)
                path = config.save_fleet(fleet)
            except (ConfigError, OSError) as exc:
                return self.fail(exc)
            return self.send_json(
                {
                    "ok": True,
                    "fleet": config.redact_fleet(fleet),
                    "summary": config.fleet_summary(fleet),
                    "path": path,
                }
            )

        if route == "/api/fleet/clear":
            try:
                config.clear_fleet()
            except OSError as exc:
                return self.fail(exc)
            return self.send_json({"ok": True, "message": "已清除设备清单"})

        if route == "/api/fleet/start":
            try:
                stored = config.current_fleet()
                if payload.get("fleet"):
                    fleet = config.parse_fleet(payload["fleet"])
                    config.save_fleet(fleet)
                else:
                    fleet = stored
                if not fleet:
                    return self.fail(ConfigError("请先导入或保存设备清单"))
                preset_name = payload.get("preset") or fleet.get("preset") or ""
                if preset_name:
                    preset = config.resolve_preset(preset_name)
                    if preset:
                        fleet["defaults"]["username"] = preset["username"]
                        if preset["password"]:
                            fleet["defaults"]["password"] = preset["password"]
                        for item in fleet["devices"]:
                            item["username"] = preset["username"]
                            if not item.get("password") and preset["password"]:
                                item["password"] = preset["password"]
                            if not item.get("sudo_password") and preset["password"]:
                                item["sudo_password"] = preset["password"]
                task = payload.get("task") or fleet.get("task")
                try:
                    port = int(payload.get("port") or fleet.get("port") or 8737)
                except (TypeError, ValueError):
                    port = 8737
                seed = payload.get("update_seed") or fleet.get("update_seed") or ""
                if not seed and task in ("update", "update_rollout"):
                    host = (lan_addresses() or ["127.0.0.1"])[0]
                    seed = "http://{}:{}".format(host, self.port)
                ok, message = self.fleet.start(
                    fleet,
                    deploy=payload.get("deploy") or fleet.get("deploy"),
                    versions=payload.get("versions") or fleet.get("versions"),
                    workers=payload.get("workers") or 3,
                    task=task,
                    port=port,
                    autostart=payload.get("autostart", True) is not False,
                    update_seed=seed,
                )
            except (ConfigError, OSError) as exc:
                return self.fail(exc)
            return self.send_json({"ok": ok, "message": message}, 200 if ok else 409)

        if route == "/api/fleet/cancel":
            self.fleet.cancel()
            return self.send_json({"ok": True})

        return self.send_json({"error": "not found"}, 404)

    def api_env(self):
        system = self.system
        control = capabilities.ROLE == "control" or system.control_host
        guess = None if control else ros.DISTRO_VERSIONS.get(system.codename)
        recommended = None if control else ros.recommend(system.codename)
        catalog = [
            {
                "name": name,
                "ros": meta["ros"],
                "status": meta["status"],
                "recommended": name == recommended,
                "available": guess is None or name in guess,
            }
            for name, meta in ros.CATALOG.items()
        ]
        self.send_json(
            {
                "system": system.to_dict(),
                "supported": ros.supported(system.codename),
                "recommend": recommended,
                "catalog": catalog,
                "mirrors": ros.mirror_options(system.codename),
                "limits": {"min_free_gb": 2, "min_desktop_gb": 6},
                "steps": STEPS,
                "version": release.VERSION,
                "role": capabilities.ROLE,
                "capabilities": capabilities.manifest(),
            }
        )


def run_deploy(path, system):
    if not capabilities.CAPABILITIES["local_install"]:
        print(capabilities.DENY_MESSAGES["local_install"])
        return 2
    try:
        deploy = config.parse_deploy(config.read_json(path))
    except ConfigError as exc:
        print("配置错误: " + str(exc))
        return 2
    print("按配置文件部署: " + deploy["name"])
    for line in config.deploy_summary(deploy):
        print("  " + line)
    installer = Installer(system)
    since = 0
    try:
        ok, message = installer.start(deploy)
        if not ok:
            print(message)
            return 1
        while True:
            events = installer.bus.since(since)
            for event in events:
                since = event["seq"]
                if event["type"] == "log":
                    print("[" + event["level"] + "] " + event["text"])
                elif event["type"] == "step":
                    print("* " + event["status"] + " " + event["step"])
                elif event["type"] == "done":
                    result = event["result"]
                    print(
                        "结果: "
                        + (
                            "安装完成"
                            if event["ok"]
                            else "未完成 - " + str(result.get("reason"))
                        )
                    )
                    return 0 if event["ok"] else 1
            if not installer.snapshot()["running"] and not events:
                break
            time.sleep(0.1)
    except KeyboardInterrupt:
        installer.cancel()
        print("已取消")
        return 130
    return 1


def run_fleet(path, system, report_path=None):
    try:
        fleet = config.parse_fleet(config.read_json(path))
    except ConfigError as exc:
        print("设备清单错误: " + str(exc))
        return 2
    print("批量部署: " + fleet["name"])
    for line in config.fleet_summary(fleet):
        print("  " + line)
    manager = Fleet()
    seed = fleet.get("update_seed") or ""
    if not seed:
        host = (lan_addresses() or ["127.0.0.1"])[0]
        seed = "http://{}:{}".format(host, fleet.get("port") or 8737)
    ok, message = manager.start(fleet, update_seed=seed)
    if not ok:
        print(message)
        return 1
    since = 0
    try:
        while True:
            events = manager.bus.since(since)
            for event in events:
                since = event["seq"]
                if event["type"] == "fleet_log":
                    prefix = "[" + event["ip"] + "] " if event.get("ip") else ""
                    print(prefix + event["text"])
                elif event["type"] == "fleet_done":
                    print(
                        "结果: 成功 {} 台，失败 {} 台".format(
                            event["ok_count"], event["fail_count"]
                        )
                    )
                    reports = event.get("reports") or []
                    for item in reports:
                        print(
                            "  [{}] {} · {} · 用时 {} 秒 · {}".format(
                                item["ip"],
                                "成功" if item["ok"] else "失败",
                                item.get("version") or "-",
                                item.get("elapsed"),
                                item.get("reason") or "",
                            )
                        )
                    if report_path:
                        with open(report_path, "w", encoding="utf-8") as handle:
                            json.dump(
                                {
                                    "name": fleet["name"],
                                    "task": manager.task,
                                    "ok_count": event["ok_count"],
                                    "fail_count": event["fail_count"],
                                    "reports": reports,
                                },
                                handle,
                                ensure_ascii=False,
                                indent=2,
                            )
                        print("报告已写入: " + report_path)
                    return 0 if event["ok"] else 1
            if not manager.snapshot()["running"] and not events:
                break
            time.sleep(0.1)
    except KeyboardInterrupt:
        manager.cancel()
        print("已取消")
        return 130
    return 1


def main(argv=None):
    setup_runtime_logs()
    parser = argparse.ArgumentParser(description="felisros - ROS 图形化安装器")
    parser.add_argument(
        "--host", default="0.0.0.0", help="绑定地址，默认 0.0.0.0（监听所有网卡）"
    )
    parser.add_argument("--port", type=int, default=8737)
    parser.add_argument(
        "--no-browser", action="store_true", help="启动后不自动打开浏览器"
    )
    parser.add_argument(
        "--lan", action="store_true", help="兼容旧用法，等价于默认的 --host 0.0.0.0"
    )
    parser.add_argument("--deploy", metavar="FILE", help="按 JSON 配置文件无界面部署")
    parser.add_argument("--fleet", metavar="FILE", help="按设备清单 JSON 批量部署")
    parser.add_argument(
        "--report", metavar="FILE", help="批量部署结果报告写入 JSON 文件"
    )
    parser.add_argument("--versions", metavar="FILE", help="启动时导入版本清单 JSON")
    parser.add_argument(
        "--no-beacon", action="store_true", help="不向局域网广播本机更新源"
    )
    parser.add_argument(
        "--check-update", action="store_true", help="检查局域网是否有更新版本后退出"
    )
    parser.add_argument(
        "--apply-update", action="store_true", help="从更新源拉取并安装最新版本后退出"
    )
    parser.add_argument(
        "--update-seed", metavar="URL", help="更新源地址，如 http://192.168.1.20:8737"
    )
    parser.add_argument(
        "--no-restart", action="store_true", help="更新后不自动重启服务"
    )
    args = parser.parse_args(argv)

    if args.check_update or args.apply_update:
        settings = release.load_auto()
        seed = args.update_seed or settings.get("seed") or ""
        found = peers.check_update(seed, release.VERSION, [])
        print(json.dumps(found, ensure_ascii=False, indent=2))
        if args.apply_update:
            if not capabilities.CAPABILITIES["local_update"]:
                print(capabilities.DENY_MESSAGES["local_update"])
                return 2
            if not found.get("available"):
                return 1
            result = peers.apply_update(
                found["seed"],
                expected=found.get("sha256") or "",
                restart=not args.no_restart,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok") else 1
        return 0

    try:
        package = release.ensure_package()
        if package:
            print("安装包已就绪（检测到源码变更时自动重建）: " + package)
        else:
            print("警告: 安装包不可用，下载时将尝试重新打包")
    except (OSError, ValueError) as exc:
        print("自动打包失败: " + str(exc))

    system = SystemInfo()
    if args.versions:
        try:
            manifest = config.parse_versions(config.read_json(args.versions))
            path = config.save(manifest)
            print("已导入版本清单: " + manifest["name"] + " -> " + path)
        except (ConfigError, OSError) as exc:
            print("版本清单错误: " + str(exc))
            return 2
    else:
        saved = config.load()
        if saved:
            print("已载入版本清单: " + saved["name"])
    saved_fleet = config.load_fleet()
    if saved_fleet:
        print("已载入设备清单: " + saved_fleet["name"])

    if args.fleet:
        return run_fleet(args.fleet, system, args.report)

    if args.deploy:
        return run_deploy(args.deploy, system)

    Handler.system = system
    Handler.installer = Installer(system)
    Handler.fleet = Fleet()
    Handler.port = args.port
    Handler.update_bus = EventBus()
    Handler.beacon = peers.PeerBeacon(args.port)

    def push_update(text, level="out"):
        Handler.update_bus.push(
            {"type": "update_log", "text": str(text), "level": level}
        )

    Handler.updater = peers.AutoUpdater(push_update, port=args.port)
    if not args.no_beacon:
        Handler.beacon.start()
    saved_update = release.load_auto()
    if saved_update.get("enabled"):
        Handler.updater.configure(saved_update, peers=lambda: Handler.beacon.list())

    host = "0.0.0.0" if args.lan else args.host
    lan_mode = host == "0.0.0.0"
    Handler.lan_mode = lan_mode
    httpd = ThreadingHTTPServer((host, args.port), Handler)
    url = "http://{}:{}/".format("127.0.0.1" if lan_mode else host, args.port)
    print("felisros {} 已启动: {}".format(release.VERSION, url))
    print(
        "角色: {}   遥测: 无".format(
            "控制端（Windows，仅管理 Linux 设备）"
            if capabilities.is_windows()
            else "完整端（Linux）"
        )
    )
    if lan_mode:
        addresses = lan_addresses()
        if addresses:
            print("局域网分发地址:")
            for ip in addresses:
                print("  http://{}:{}/".format(ip, args.port))
                print(
                    "  一键安装: curl -fsSL http://{}:{}/install.sh | sh".format(
                        ip, args.port
                    )
                )
        else:
            print("未检测到局域网地址，其他设备可能无法访问")
        if not os.path.isfile(DIST_PATH):
            print("提示: 安装包暂不可用，下载时将自动尝试打包")
    print("按 Ctrl+C 结束")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    finally:
        httpd.server_close()
        Handler.beacon.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
