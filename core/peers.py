import json
import os
import shutil
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import capabilities, release

BEACON_PORT = 8738
MAGIC = b"FELISROS1 "
STALE_AFTER = 25


def normalize_url(value, default_port=8737):
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    if text.startswith("http://"):
        rest = text[len("http://") :]
        if ":" not in rest.split("/")[0]:
            host, tail = rest.split("/", 1) if "/" in rest else (rest, "")
            text = "http://{}:{}{}{}".format(host, default_port, "/" if tail else "", tail)
    return text.rstrip("/")


def fetch_release(url, timeout=4):
    base = normalize_url(url)
    if not base:
        raise ValueError("请填写更新源地址")
    request = urllib.request.Request(
        base + "/api/release", headers={"User-Agent": "felisros-peer/1"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        item = json.loads(response.read().decode("utf-8"))
    if item.get("schema") != release.SCHEMA or not item.get("version"):
        raise ValueError("更新源返回的清单格式不正确")
    item["base_url"] = base
    return item


def probe(ips, port=8737, timeout=0.8):
    found = {}
    targets = []
    for item in ips or []:
        ip = str(item).strip()
        if ip and ip not in targets:
            targets.append(ip)
    if not targets:
        return []

    def visit(ip):
        try:
            return ip, fetch_release("http://{}:{}".format(ip, int(port)), timeout)
        except Exception:
            return ip, None

    with ThreadPoolExecutor(max_workers=16) as pool:
        for future in as_completed([pool.submit(visit, ip) for ip in targets]):
            ip, item = future.result()
            if item:
                found[ip] = item
    result = []
    for ip in targets:
        if ip not in found:
            continue
        item = dict(found[ip])
        item["ip"] = ip
        item["port"] = int(port)
        item["last_seen"] = int(time.time())
        item["source"] = "probe"
        result.append(item)
    return result


class PeerBeacon:
    def __init__(self, app_port=8737):
        self.app_port = int(app_port)
        self.peers = {}
        self.lock = threading.Lock()
        self.running = False
        self.socket = None
        self.threads = []
        self.info = {}

    def _local_ips(self):
        found = set()
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if not ip.startswith("127."):
                    found.add(ip)
        except OSError:
            pass
        return found

    def _packet(self):
        if not self.info:
            base = "http://127.0.0.1:{}".format(self.app_port)
            self.info = release.local_manifest(base)
        item = dict(self.info)
        item["app_port"] = self.app_port
        item["role"] = capabilities.ROLE
        return json.dumps(item, ensure_ascii=False).encode("utf-8")

    def start(self):
        if self.running:
            return
        self.running = True
        self.local = self._local_ips()
        self.threads.append(threading.Thread(target=self._listen, daemon=True))
        self.threads.append(threading.Thread(target=self._announce, daemon=True))
        for thread in self.threads:
            thread.start()

    def stop(self):
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except OSError:
                pass
        self.socket = None

    def _listen(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", BEACON_PORT))
            sock.settimeout(1.0)
            self.socket = sock
        except OSError:
            return
        while self.running:
            try:
                data, address = sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data.startswith(MAGIC):
                continue
            try:
                item = json.loads(data[len(MAGIC) :].decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if item.get("schema") != release.SCHEMA or not item.get("version"):
                continue
            ip = address[0]
            if ip in self.local:
                continue
            item["ip"] = ip
            item["port"] = int(item.get("app_port") or 8737)
            item["last_seen"] = int(time.time())
            item["source"] = "beacon"
            with self.lock:
                self.peers[ip] = item

    def _targets(self):
        targets = ["255.255.255.255"]
        try:
            from . import discovery

            for item in discovery.default_networks():
                parts = item.get("cidr", "").split("/")
                if len(parts) == 2:
                    address = parts[0].rsplit(".", 1)
                    if len(address) == 2:
                        targets.append(address[0] + ".255")
        except Exception:
            pass
        return list(dict.fromkeys(targets))

    def _announce(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        data = self._packet()
        while self.running:
            for target in self._targets():
                try:
                    sock.sendto(data, (target, BEACON_PORT))
                except OSError:
                    pass
            time.sleep(3)
        sock.close()

    def add(self, items):
        with self.lock:
            for item in items or []:
                ip = item.get("ip")
                if ip:
                    item["last_seen"] = int(time.time())
                    self.peers[ip] = item

    def list(self):
        now = int(time.time())
        with self.lock:
            result = []
            for ip, item in list(self.peers.items()):
                if now - int(item.get("last_seen") or 0) > STALE_AFTER:
                    self.peers.pop(ip, None)
                    continue
                row = dict(item)
                row["age"] = now - int(item.get("last_seen") or 0)
                result.append(row)
            result.sort(key=lambda row: release.version_tuple(row.get("version")), reverse=True)
            return result


def check_update(seed="", current="", peers=None, timeout=5):
    current = current or release.VERSION
    candidates = []
    if seed:
        candidates.append(normalize_url(seed))
    for item in peers or []:
        base = item.get("base_url") or "http://{}:{}".format(item.get("ip"), item.get("port") or 8737)
        candidates.append(normalize_url(base))
    candidates = [item for item in dict.fromkeys(candidates) if item]
    best = None
    errors = []
    for base in candidates:
        try:
            manifest = fetch_release(base, timeout)
        except Exception as exc:
            errors.append("{}: {}".format(base, exc))
            continue
        if release.compare(manifest.get("version"), current) <= 0:
            continue
        if best is None or release.compare(manifest.get("version"), best.get("version")) > 0:
            best = manifest
    if not best:
        checked = len(candidates)
        return {
            "ok": True,
            "available": False,
            "version": current,
            "seed": "",
            "checked": checked,
            "message": "已检查 {} 个更新源，当前已是最新版本".format(checked)
            if checked
            else "未发现可访问的更新源，请手动填写地址",
            "errors": errors[:4],
        }
    return {
        "ok": True,
        "available": True,
        "version": best["version"],
        "current": current,
        "seed": best["base_url"],
        "sha256": best.get("sha256") or "",
        "size": best.get("size") or 0,
        "download_url": best.get("download_url")
        or best["base_url"] + "/download/" + release.PACKAGE_NAME,
        "install_url": best.get("install_url") or best["base_url"] + "/install.sh",
        "checked": len(candidates),
        "message": "发现新版本 " + best["version"],
    }


def apply_update(seed, expected="", restart=False, port=8737, report=None, timeout=180):
    capabilities.allow("local_update")
    manifest = fetch_release(seed)
    target_version = manifest.get("version") or ""
    if release.compare(target_version, release.VERSION) < 0:
        return {"ok": False, "reason": "更新源版本不比当前版本新"}
    url = manifest.get("download_url") or normalize_url(seed) + "/download/" + release.PACKAGE_NAME
    handle, temp_path = tempfile.mkstemp(prefix="felisros-update-", suffix=".tar.gz")
    os.close(handle)
    try:
        if report:
            report("从 " + normalize_url(seed) + " 下载更新包", "info")
        request = urllib.request.Request(url, headers={"User-Agent": "felisros-peer/1"})
        with urllib.request.urlopen(request, timeout=timeout) as response, open(
            temp_path, "wb"
        ) as stream:
            shutil.copyfileobj(response, stream)
        checksum = expected or manifest.get("sha256") or ""
        result = release.install_from(
            temp_path, checksum, restart=restart, port=port, report=report
        )
        result["seed"] = normalize_url(seed)
        result["target_version"] = target_version
        return result
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return {"ok": False, "reason": "更新失败: " + str(exc)}
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


class AutoUpdater:
    def __init__(self, report=None, port=8737):
        self.report = report or (lambda text, level="out": None)
        self.port = int(port)
        self.stop_event = threading.Event()
        self.thread = None
        self.settings = {"enabled": False, "interval": 300, "seed": ""}

    def configure(self, settings, peers=None):
        self.settings = release.save_auto(settings)
        self.stop_event.set()
        self.thread = None
        if self.settings["enabled"]:
            self.stop_event = threading.Event()
            self.thread = threading.Thread(
                target=self._loop, args=(peers or [],), daemon=True
            )
            self.thread.start()
        return self.settings

    def _loop(self, peers):
        while not self.stop_event.wait(max(5, self.settings["interval"])):
            try:
                capabilities.allow("local_update")
                found = check_update(
                    self.settings.get("seed") or "", release.VERSION, peers()
                )
                if not found.get("available"):
                    continue
                self.report("自动更新: " + found.get("message", ""), "info")
                apply_update(
                    found["seed"],
                    expected=found.get("sha256") or "",
                    restart=True,
                    port=self.port,
                    report=lambda text, level="out": self.report("自动更新: " + text, level),
                )
            except Exception as exc:
                self.report("自动更新失败: " + str(exc), "err")

    def snapshot(self):
        return {"settings": dict(self.settings), "running": bool(self.thread and self.thread.is_alive())}
