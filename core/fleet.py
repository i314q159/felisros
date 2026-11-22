import copy
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import release
from .installer import EventBus
from .remote import SshClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "dist", "felisros-linux.tar.gz")
TASKS = ("software", "ros", "rollout", "update", "update_rollout")


def task_label(task):
    if task == "software":
        return "分发 felisros 软件"
    if task == "update":
        return "更新设备上的 felisros 软件"
    if task == "update_rollout":
        return "更新 felisros 并遥控安装 ROS"
    if task == "rollout":
        return "分发软件并安装 ROS"
    return "安装 ROS"


def with_defaults(device, defaults):
    """设备行留空时回落到清单默认凭据（前端「统一登录信息」即默认凭据）。

    只在运行时合并，不回写清单，避免把统一密码固化到每台设备上。
    """
    merged = dict(device)
    if not merged.get("username"):
        merged["username"] = defaults.get("username") or "root"
    for key in ("password", "sudo_password"):
        if not merged.get(key) and not merged.get(key + "_ref"):
            merged[key] = defaults.get(key) or ""
    return merged


class Fleet:
    def __init__(self):
        self.bus = EventBus()
        self.lock = threading.Lock()
        self.cancel_flag = threading.Event()
        self.devices = []
        self.reports = []
        self.thread = None
        self.task = ""
        self.running = False
        self.finished = False
        self.ok_count = 0
        self.fail_count = 0

    def snapshot(self):
        with self.lock:
            return {
                "running": self.running,
                "finished": self.finished,
                "devices": copy.deepcopy(self.devices),
                "reports": copy.deepcopy(self.reports),
                "task": self.task,
                "ok_count": self.ok_count,
                "fail_count": self.fail_count,
            }

    def report(self):
        with self.lock:
            return copy.deepcopy(self.reports)

    def log(self, ip, text, level="out"):
        self.bus.push({"type": "fleet_log", "ip": ip, "text": text, "level": level})

    def device_event(self, ip, status, text="", stage=""):
        self.bus.push(
            {"type": "fleet_device", "ip": ip, "status": status, "text": text, "stage": stage}
        )

    def start(
        self,
        fleet_config,
        deploy=None,
        versions=None,
        workers=3,
        task=None,
        port=8737,
        autostart=True,
        update_seed="",
    ):
        with self.lock:
            if self.running:
                return False, "批量任务正在进行"
            task = task or fleet_config.get("task") or "rollout"
            if task not in TASKS:
                return False, "不支持的任务类型: {}".format(task)
            if task in ("ros", "rollout", "update_rollout") and not (deploy or fleet_config.get("deploy")):
                return False, "缺少 ROS 部署配置，请先导入配置文件"
            archive = None
            if task in ("software", "rollout", "update", "update_rollout"):
                try:
                    package = release.ensure_package()
                except (OSError, ValueError) as exc:
                    return False, "安装包自动打包失败: " + str(exc)
                if not package:
                    return False, "安装包自动打包失败，请检查源码后重试"
                with open(package, "rb") as handle:
                    archive = handle.read()
            devices = [
                dict(
                    with_defaults(item, fleet_config.get("defaults") or {}),
                    status="pending",
                    reason="",
                    stage="",
                    arch="",
                    version="",
                    elapsed=0,
                )
                for item in fleet_config["devices"]
                if item.get("selected")
            ]
            if not devices:
                return False, "没有选中的设备"
            self.devices = devices
            self.reports = []
            self.task = task
            self.bus = EventBus()
            self.cancel_flag = threading.Event()
            self.running = True
            self.finished = False
            self.ok_count = 0
            self.fail_count = 0
        self.thread = threading.Thread(
            target=self._run,
            args=(
                devices,
                deploy or fleet_config.get("deploy"),
                versions or fleet_config.get("versions"),
                workers,
                task,
                archive,
                port,
                autostart,
                update_seed or fleet_config.get("update_seed") or "",
            ),
            daemon=True,
        )
        self.thread.start()
        return True, "已开始"

    def cancel(self):
        self.cancel_flag.set()
        self.log("", "收到取消请求，正在结束批量任务", "warn")

    def _clean(self, text, device):
        safe = str(text or "")
        for key in ("password", "sudo_password"):
            secret = device.get(key)
            if secret:
                safe = safe.replace(secret, "******")
        return safe

    def _report(self, device, result, elapsed):
        return {
            "ip": device["ip"],
            "alias": device.get("alias") or "",
            "ok": bool(result.get("ok")),
            "reason": self._clean(result.get("reason") or "", device),
            "stage": device.get("stage") or "",
            "arch": device.get("arch") or "",
            "version": result.get("version") or device.get("version") or "",
            "kind": result.get("kind") or "",
            "package": result.get("package") or "",
            "elapsed": round(float(result.get("elapsed") or elapsed), 1),
            "sources": result.get("sources") or [],
        }

    def _execute(
        self, client, device, task, deploy, versions, archive, port, autostart, seed
    ):
        if task == "software":
            return client.install_software(archive, port, autostart), "软件分发完成"

        if task == "update":
            device["stage"] = "更新软件"
            self.device_event(device["ip"], "run", "从更新源拉取最新版本", "更新软件")
            result = client.update_software(seed, archive, port, autostart)
            device["arch"] = result.get("arch") or ""
            return result, "更新完成"

        if task == "update_rollout":
            device["stage"] = "更新软件"
            self.device_event(device["ip"], "run", "从更新源拉取最新版本", "更新软件")
            first = client.update_software(seed, archive, port, autostart)
            device["arch"] = first.get("arch") or ""
            if not first.get("ok"):
                return first, "软件更新失败"
            device["stage"] = "遥控安装 ROS"
            self.device_event(
                device["ip"], "run", "软件已更新，开始遥控安装 ROS", "遥控安装 ROS"
            )
            second = client.install_ros_remote(deploy, versions, port)
            merged = dict(second)
            merged["arch"] = second.get("arch") or device["arch"]
            merged["version"] = second.get("version") or first.get("version") or ""
            merged["elapsed"] = round(
                float(first.get("elapsed") or 0) + float(second.get("elapsed") or 0), 1
            ) or None
            if not second.get("ok"):
                merged["reason"] = "ROS 安装失败: " + str(second.get("reason") or "")
            return merged, "更新完成"

        if task == "rollout":
            device["stage"] = "分发软件"
            self.device_event(device["ip"], "run", "分发软件并设置开机自启", "分发软件")
            first, reason = client.install_software(archive, port, autostart), "软件分发完成"
            device["arch"] = first.get("arch") or ""
            if not first.get("ok"):
                return first, "软件分发失败"
            device["stage"] = "遥控安装 ROS"
            self.device_event(
                device["ip"], "run", "软件已就绪，开始遥控安装 ROS", "遥控安装 ROS"
            )
            second = client.install_ros_remote(deploy, versions, port)
            merged = dict(second)
            merged["arch"] = second.get("arch") or device["arch"]
            merged["elapsed"] = round(
                float(first.get("elapsed") or 0) + float(second.get("elapsed") or 0), 1
            ) or None
            if not second.get("ok"):
                merged["reason"] = "ROS 安装失败: " + str(second.get("reason") or "")
            return merged, reason

        return client.install(deploy, versions), "ROS 安装完成"

    def _run(
        self, devices, deploy, versions, workers, task, archive, port, autostart, seed
    ):
        if task in ("ros", "rollout", "update_rollout") and not deploy:
            self.log("", "缺少部署配置，批量任务终止", "err")
            self.device_event("", "fail", "缺少部署配置")
            self._finish()
            return
        label = task_label(task)
        self.log(
            "",
            "批量任务开始: {}，共 {} 台设备，并发 {}".format(label, len(devices), workers),
            "info",
        )
        try:
            pool_size = max(1, min(int(workers or 3), 99))
            with ThreadPoolExecutor(max_workers=pool_size) as pool:
                futures = {}
                for device in devices:
                    if self.cancel_flag.is_set():
                        break
                    self.device_event(device["ip"], "run", "开始任务", "准备中")
                    client = SshClient(
                        device,
                        on_line=lambda text, level, ip=device["ip"]: self.log(ip, text, level),
                    )
                    job = pool.submit(
                        self._execute,
                        client,
                        device,
                        task,
                        deploy,
                        versions,
                        archive,
                        port,
                        autostart,
                        seed,
                    )
                    futures[job] = device
                for future in as_completed(futures):
                    device = futures[future]
                    started = time.monotonic()
                    try:
                        result, done_text = future.result()
                    except Exception as exc:
                        result = {"ok": False, "reason": str(exc)}
                        done_text = "任务异常"
                    elapsed = time.monotonic() - started
                    ok = bool(result.get("ok"))
                    reason = self._clean(result.get("reason") or "", device)
                    item = self._report(device, result, elapsed)
                    with self.lock:
                        for current in self.devices:
                            if current["ip"] == device["ip"]:
                                current["status"] = "ok" if ok else "fail"
                                current["reason"] = reason
                                current["stage"] = device.get("stage") or ""
                                current["arch"] = device.get("arch") or ""
                                current["version"] = item["version"]
                                current["elapsed"] = item["elapsed"]
                                break
                        self.reports.append(item)
                        if ok:
                            self.ok_count += 1
                        else:
                            self.fail_count += 1
                    self.bus.push({"type": "fleet_report", "report": item})
                    self.device_event(
                        device["ip"], "ok" if ok else "fail", reason, item["stage"]
                    )
                    self.log(
                        device["ip"],
                        "{}（用时 {} 秒）".format(reason or done_text, item["elapsed"]),
                        "ok" if ok else "err",
                    )
        finally:
            self._finish()

    def _finish(self):
        with self.lock:
            self.running = False
            self.finished = True
            ok = self.fail_count == 0 and self.ok_count > 0
        self.bus.push(
            {
                "type": "fleet_done",
                "ok": ok,
                "ok_count": self.ok_count,
                "fail_count": self.fail_count,
                "reports": copy.deepcopy(self.reports),
            }
        )
