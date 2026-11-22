import ipaddress
import os
import platform
import re
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

MAX_HOSTS = 2048

LINUX_HINTS = (
    "linux",
    "ubuntu",
    "debian",
    "centos",
    "fedora",
    "raspbian",
    "alpine",
    "openwrt",
    "archlinux",
)


def normalize_network(value):
    try:
        net = ipaddress.ip_network((value or "").strip(), strict=False)
    except (ValueError, AttributeError, TypeError):
        raise ValueError("网段格式不正确，例如 192.168.1.0/24")
    if net.version != 4:
        raise ValueError("目前只支持 IPv4 网段")
    if net.num_addresses > MAX_HOSTS + 2:
        raise ValueError("网段过大，请指定 /24 或更小的范围")
    return net


def normalize_networks(value):
    """把输入解析为去重后的网段列表。

    支持数组、逗号/空格分隔的字符串、单个 CIDR 和 "auto"；
    留空或 "auto" 时使用本机所有网卡的网段。
    """
    if value is None or value == "" or value == [] or (
        isinstance(value, str) and value.strip().lower() == "auto"
    ):
        items = [item["cidr"] for item in default_networks()]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    elif isinstance(value, str):
        items = [item for item in re.split(r"[,，\s]+", value.strip()) if item]
    else:
        raise ValueError("网段格式不正确，例如 192.168.1.0/24")
    nets = []
    seen = set()
    for item in items:
        net = normalize_network(item)
        key = str(net)
        if key not in seen:
            seen.add(key)
            nets.append(net)
    return nets or [normalize_network(item["cidr"]) for item in default_networks()]


def _windows_networks():
    """从 ipconfig 输出解析网卡名、IPv4 地址与子网掩码。"""
    results = []
    try:
        output = subprocess.run(
            ["ipconfig"],
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return results
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if "IPv4" not in line or ":" not in line:
            continue
        address = line.rsplit(":", 1)[-1].strip()
        if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", address) or address.startswith("127."):
            continue
        name = ""
        for back in range(index - 1, -1, -1):
            previous = lines[back]
            if previous.strip() and not previous[0].isspace() and previous.strip().endswith(":"):
                name = previous.strip()[:-1].strip()
                break
        prefix = 24
        for following in lines[index + 1 :]:
            trimmed = following.strip()
            if trimmed and not following[0].isspace() and trimmed.endswith(":"):
                break
            if ("Subnet Mask" in following or "子网掩码" in following) and ":" in trimmed:
                mask = trimmed.rsplit(":", 1)[-1].strip()
                try:
                    prefix = ipaddress.ip_network((address, mask), strict=False).prefixlen
                except ValueError:
                    pass
                break
        try:
            net = ipaddress.ip_network(address + "/" + str(prefix), strict=False)
        except ValueError:
            continue
        if net.num_addresses > MAX_HOSTS:
            net = ipaddress.ip_network(address + "/24", strict=False)
        results.append({"name": name, "ip": address, "cidr": str(net)})
    return results


def default_networks():
    """枚举本机所有网卡的 IPv4 网段，返回 [{name, ip, cidr}]。"""
    results = []
    seen = set()
    if platform.system() == "Windows":
        for item in _windows_networks():
            if item["cidr"] not in seen:
                seen.add(item["cidr"])
                results.append(item)
    if platform.system() == "Linux":
        try:
            output = subprocess.run(
                ["ip", "-4", "-o", "addr"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
            for line in output.splitlines():
                match = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)/(\d+)\b", line)
                if not match or match.group(1).startswith("127."):
                    continue
                try:
                    net = ipaddress.ip_network(
                        match.group(1) + "/" + match.group(2), strict=False
                    )
                except ValueError:
                    continue
                if net.num_addresses > MAX_HOSTS:
                    continue
                name_match = re.search(r"\bdev\s+(\S+)", line)
                results.append(
                    {
                        "name": name_match.group(1) if name_match else "",
                        "ip": match.group(1),
                        "cidr": str(net),
                    }
                )
                seen.add(str(net))
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    if not results:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                address = info[4][0]
                if not address or address.startswith("127."):
                    continue
                net = ipaddress.ip_network(address + "/24", strict=False)
                if str(net) in seen:
                    continue
                seen.add(str(net))
                results.append({"name": "", "ip": address, "cidr": str(net)})
        except OSError:
            pass
    return results or [{"name": "", "ip": "", "cidr": "192.168.1.0/24"}]


def default_network():
    """返回第一块可用网卡的网段（兼容旧调用方）。"""
    return default_networks()[0]["cidr"]


def arp_table():
    table = {}
    path = "/proc/net/arp"
    if not os.path.isfile(path):
        return table
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            next(handle, None)
            for line in handle:
                parts = line.split()
                if len(parts) >= 4 and parts[3] != "0x0":
                    table[parts[0]] = parts[3].lower()
    except OSError:
        return {}
    return table


def ping(address):
    system = platform.system()
    command = (
        ["ping", "-n", "1", "-w", "1000", address]
        if system == "Windows"
        else ["ping", "-c", "1", "-W", "1", "-n", address]
    )
    try:
        return subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2.5,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def port_open(address, port=22, timeout=0.6):
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return True
    except OSError:
        return False


def ssh_banner(address, port=22, timeout=1.2):
    try:
        with socket.create_connection((address, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            data = sock.recv(256)
    except OSError:
        return ""
    line = data.decode("utf-8", errors="replace").splitlines()
    return line[0].strip() if line else ""


def ping_ttl(address):
    system = platform.system()
    command = (
        ["ping", "-n", "1", "-w", "1000", address]
        if system == "Windows"
        else ["ping", "-c", "1", "-W", "1", "-n", address]
    )
    try:
        output = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=2.5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"\bttl[=:]\s*(\d+)", output, re.I)
    return int(match.group(1)) if match else None


def classify_os(banner, ttl, felisros=False):
    text = (banner or "").lower()
    if felisros:
        return "Linux"
    if any(hint in text for hint in LINUX_HINTS):
        return "Linux"
    if ttl == 64:
        return "Linux"
    if ttl == 128:
        return "Windows"
    if text.startswith("ssh-2.0"):
        return "SSH 设备"
    return "未知"


def reverse_name(address):
    try:
        output = subprocess.run(
            ["getent", "hosts", address],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.split()
        if len(output) >= 2:
            return output[1].split(".")[0]
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def discover(network=None, report=None):
    nets = normalize_networks(network)
    hosts = []
    seen = set()
    for net in nets:
        for host in net.hosts():
            address = str(host)
            if address not in seen:
                seen.add(address)
                hosts.append(address)
    if report:
        report(
            "扫描 {} ，共 {} 个地址".format(
                "、".join(str(net) for net in nets), len(hosts)
            )
        )

    alive = []
    with ThreadPoolExecutor(max_workers=64) as pool:
        futures = {pool.submit(ping, host): host for host in hosts}
        for future in as_completed(futures):
            if future.result():
                alive.append(futures[future])
    alive.sort(key=lambda item: ipaddress.ip_address(item))
    if report:
        report("发现 {} 台在线设备，正在嗅探系统与 SSH 端口".format(len(alive)))

    arp = arp_table()
    devices = []
    with ThreadPoolExecutor(max_workers=48) as pool:
        ssh_futures = {pool.submit(port_open, host, 22, 0.6): host for host in alive}
        banner_futures = {pool.submit(ssh_banner, host, 22, 1.2): host for host in alive}
        ttl_futures = {pool.submit(ping_ttl, host): host for host in alive}
        felis_futures = {pool.submit(port_open, host, 8737, 0.5): host for host in alive}
        name_futures = {pool.submit(reverse_name, host): host for host in alive}
        ssh_hosts = set()
        banners = {}
        ttls = {}
        felis_hosts = set()
        names = {}
        for future in as_completed(ssh_futures):
            if future.result():
                ssh_hosts.add(ssh_futures[future])
        for future in as_completed(banner_futures):
            value = future.result()
            if value:
                banners[banner_futures[future]] = value
        for future in as_completed(ttl_futures):
            value = future.result()
            if value:
                ttls[ttl_futures[future]] = value
        for future in as_completed(felis_futures):
            if future.result():
                felis_hosts.add(felis_futures[future])
        for future in as_completed(name_futures):
            names[name_futures[future]] = future.result()

    for host in alive:
        banner = banners.get(host, "")
        ttl = ttls.get(host)
        felis = host in felis_hosts
        is_ssh = host in ssh_hosts or bool(banner)
        system = classify_os(banner, ttl, felis)
        devices.append(
            {
                "ip": host,
                "mac": arp.get(host, ""),
                "hostname": names.get(host, ""),
                "ssh": is_ssh,
                "os": system,
                "linux": system == "Linux",
                "banner": banner,
                "ttl": ttl,
                "felisros": felis,
                "kind": "linux" if system == "Linux" else ("ssh" if is_ssh else "alive"),
            }
        )
    linux_count = sum(1 for item in devices if item["linux"])
    if report:
        report(
            "嗅探结果: {} 台 Linux，{} 台开放 SSH，{} 台已装 felisros".format(
                linux_count, sum(1 for item in devices if item["ssh"]), len(felis_hosts)
            )
        )
    return devices
