import copy
import glob
import os
import re

ROS1 = "ROS1"
ROS2 = "ROS2"
EOL = 0
LTS = 1

COLCON = ["python3-colcon-common-extensions", "python3-argcomplete", "python3-rosdep"]
CATKIN3 = ["python3-catkin-tools", "python3-rosdep"]
CATKIN2 = ["python-catkin-tools", "python-rosdep"]

CATALOG = {
    "lyrical": {"ros": ROS2, "status": LTS, "deps": COLCON},
    "kilted": {"ros": ROS2, "status": LTS, "deps": COLCON},
    "jazzy": {"ros": ROS2, "status": LTS, "deps": COLCON},
    "rolling": {"ros": ROS2, "status": LTS, "deps": COLCON},
    "iron": {"ros": ROS2, "status": LTS, "deps": COLCON},
    "humble": {"ros": ROS2, "status": LTS, "deps": COLCON},
    "galactic": {"ros": ROS2, "status": LTS, "deps": COLCON},
    "foxy": {"ros": ROS2, "status": EOL, "deps": COLCON},
    "eloquent": {"ros": ROS2, "status": EOL, "deps": COLCON},
    "dashing": {"ros": ROS2, "status": EOL, "deps": []},
    "crystal": {"ros": ROS2, "status": EOL, "deps": []},
    "bouncy": {"ros": ROS2, "status": EOL, "deps": []},
    "ardent": {"ros": ROS2, "status": EOL, "deps": []},
    "noetic": {"ros": ROS1, "status": EOL, "deps": CATKIN3},
    "melodic": {"ros": ROS1, "status": LTS, "deps": CATKIN2},
    "kinetic": {"ros": ROS1, "status": EOL, "deps": CATKIN2},
}

MIRRORS = {
    "ustc": {
        "label": "中科大",
        "ROS1": "https://mirrors.ustc.edu.cn/ros/ubuntu/",
        "ROS2": "https://mirrors.ustc.edu.cn/ros2/ubuntu/",
    },
    "tsinghua": {
        "label": "清华 TUNA",
        "ROS1": "https://mirrors.tuna.tsinghua.edu.cn/ros/ubuntu/",
        "ROS2": "https://mirrors.tuna.tsinghua.edu.cn/ros2/ubuntu/",
    },
    "huawei": {
        "label": "华为云",
        "ROS1": "https://repo.huaweicloud.com/ros/ubuntu/",
        "ROS2": "https://repo.huaweicloud.com/ros2/ubuntu/",
    },
    "mirrorz": {
        "label": "MirrorZ 联合镜像",
        "ROS1": "https://mirrors.cernet.edu.cn/ros/ubuntu/",
        "ROS2": "https://mirrors.cernet.edu.cn/ros2/ubuntu/",
    },
    "aliyun": {
        "label": "阿里云",
        "ROS2": "https://mirrors.aliyun.com/ros2/ubuntu/",
    },
    "official": {
        "label": "ROS 官方源",
        "ROS1": "http://packages.ros.org/ros/ubuntu/",
        "ROS2": "http://packages.ros.org/ros2/ubuntu/",
    },
}

BASE = {"tsinghua", "ustc", "huawei", "official"}
WITH_MIRRORZ = BASE | {"mirrorz"}

ROS1_SUPPORT = {
    "trusty": WITH_MIRRORZ,
    "xenial": WITH_MIRRORZ,
    "bionic": WITH_MIRRORZ,
    "focal": WITH_MIRRORZ,
    "jessie": WITH_MIRRORZ,
    "stretch": WITH_MIRRORZ,
    "buster": WITH_MIRRORZ,
    "artful": BASE,
    "cosmic": BASE,
    "disco": BASE,
    "eoan": BASE,
    "lucid": BASE,
    "maverick": BASE,
    "natty": BASE,
    "oneiric": BASE,
    "precise": BASE,
    "quantal": BASE,
    "raring": BASE,
    "saucy": BASE,
    "utopic": BASE,
    "vivid": BASE,
    "wheezy": BASE,
    "wily": BASE,
    "yakkety": BASE,
    "zesty": BASE,
}

ROS2_SUPPORT = {
    "jammy": WITH_MIRRORZ | {"aliyun"},
    "noble": WITH_MIRRORZ | {"aliyun"},
    "resolute": WITH_MIRRORZ | {"aliyun"},
    "bookworm": WITH_MIRRORZ | {"aliyun"},
    "bullseye": WITH_MIRRORZ | {"aliyun"},
    "trixie": WITH_MIRRORZ,
    "bionic": {"aliyun", "official"},
    "focal": {"aliyun", "official"},
    "buster": {"aliyun", "official"},
    "xenial": {"aliyun", "official"},
    "trusty": BASE,
    "jessie": BASE,
    "stretch": BASE,
    "cosmic": {"official"},
    "disco": {"official"},
    "eoan": {"official"},
    "utopic": {"official"},
    "yakkety": {"official"},
    "zesty": {"official"},
}

ORDER = ["ustc", "tsinghua", "huawei", "mirrorz", "aliyun", "official"]
ARM_ORDER = ["official", "ustc", "tsinghua", "huawei", "aliyun"]

RECOMMEND = {
    "bionic": ["melodic"],
    "focal": ["foxy", "noetic"],
    "jammy": ["humble"],
    "noble": ["jazzy"],
    "resolute": ["lyrical"],
}

FALLBACK = [
    "lyrical",
    "jazzy",
    "humble",
    "foxy",
    "kilted",
    "iron",
    "galactic",
    "noetic",
    "melodic",
    "rolling",
]

DISTRO_VERSIONS = {
    "bionic": ["melodic", "dashing"],
    "focal": ["noetic", "foxy", "galactic"],
    "jammy": ["humble", "iron", "rolling"],
    "noble": ["jazzy", "kilted", "rolling", "lyrical"],
    "resolute": ["lyrical", "kilted"],
    "bookworm": ["humble", "jazzy"],
    "bullseye": ["humble"],
}

PACKAGES = {}

_BASE = {
    "CATALOG": copy.deepcopy(CATALOG),
    "DISTRO_VERSIONS": copy.deepcopy(DISTRO_VERSIONS),
    "RECOMMEND": copy.deepcopy(RECOMMEND),
    "MIRRORS": copy.deepcopy(MIRRORS),
    "ROS1_SUPPORT": copy.deepcopy(ROS1_SUPPORT),
    "ROS2_SUPPORT": copy.deepcopy(ROS2_SUPPORT),
    "ORDER": list(ORDER),
    "ARM_ORDER": list(ARM_ORDER),
}


def reset_overlays():
    for name, table in (
        ("CATALOG", CATALOG),
        ("DISTRO_VERSIONS", DISTRO_VERSIONS),
        ("RECOMMEND", RECOMMEND),
        ("MIRRORS", MIRRORS),
        ("ROS1_SUPPORT", ROS1_SUPPORT),
        ("ROS2_SUPPORT", ROS2_SUPPORT),
    ):
        table.clear()
        table.update(copy.deepcopy(_BASE[name]))
    ORDER[:] = list(_BASE["ORDER"])
    ARM_ORDER[:] = list(_BASE["ARM_ORDER"])
    PACKAGES.clear()


def register(manifest):
    for item in manifest.get("versions", []):
        name = item["name"]
        CATALOG[name] = {
            "ros": item["ros"],
            "status": item["status"],
            "deps": list(item["deps"]),
        }
        if item.get("packages"):
            PACKAGES[name] = dict(item["packages"])
        for codename in item["codenames"]:
            versions = DISTRO_VERSIONS.setdefault(codename, [])
            if name not in versions:
                versions.append(name)
            if item.get("recommended"):
                preferred = RECOMMEND.setdefault(codename, [])
                if name not in preferred:
                    preferred.insert(0, name)
    for item in manifest.get("mirrors", []):
        name = item["name"]
        meta = {"label": item["label"]}
        for kind in (ROS1, ROS2):
            if item.get(kind):
                meta[kind] = item[kind]
        MIRRORS[name] = meta
        for order in (ORDER, ARM_ORDER):
            if name not in order:
                order.append(name)
    for codename, names in manifest.get("support", {}).items():
        kinds = {
            CATALOG[name]["ros"]
            for name in DISTRO_VERSIONS.get(codename, [])
            if name in CATALOG
        }
        if not kinds:
            kinds = {ROS1, ROS2}
        for name in names:
            meta = MIRRORS[name]
            for kind in (ROS1, ROS2):
                if kind not in kinds:
                    continue
                table = ROS1_SUPPORT if kind == ROS1 else ROS2_SUPPORT
                if meta.get(kind):
                    table.setdefault(codename, set()).add(name)


def supported(codename):
    return codename in ROS1_SUPPORT or codename in ROS2_SUPPORT


def mirror_order(arch):
    return list(ARM_ORDER if arch == "armhf" else ORDER)


def mirror_options(codename):
    options = []
    for name in ORDER:
        kinds = [
            kind
            for kind, table in ((ROS1, ROS1_SUPPORT), (ROS2, ROS2_SUPPORT))
            if name in table.get(codename, set())
        ]
        if kinds:
            options.append({"name": name, "label": MIRRORS[name]["label"], "kinds": kinds})
    return options


def sources_for(codename, arch, first=None, excluded=None):
    excluded = excluded or set()
    order = mirror_order(arch)
    if first and first in order:
        order.remove(first)
        order.insert(0, first)
    picked = []
    for kind, table in ((ROS1, ROS1_SUPPORT), (ROS2, ROS2_SUPPORT)):
        allowed = table.get(codename)
        if not allowed:
            continue
        for name in order:
            if name in excluded or name not in allowed:
                continue
            url = MIRRORS[name].get(kind)
            if url:
                picked.append({"kind": kind, "mirror": name, "url": url})
                break
    return picked


def recommend(codename):
    return (RECOMMEND.get(codename) or [None])[0]


def pick_available(codename, available):
    for name in (RECOMMEND.get(codename) or []) + FALLBACK:
        if name in available:
            return name
    return sorted(available)[0] if available else None


def desktop_package(name):
    custom = PACKAGES.get(name, {}).get("desktop")
    if custom:
        return custom
    kind = CATALOG[name]["ros"]
    suffix = "desktop-full" if kind == ROS1 else "desktop"
    return "ros-{}-{}".format(name, suffix)


def base_package(name):
    custom = PACKAGES.get(name, {}).get("base")
    if custom:
        return custom
    return "ros-{}-ros-base".format(name)


def custom_base_packages():
    return [meta["base"] for meta in PACKAGES.values() if meta.get("base")]


def version_for_package(package):
    for name, meta in PACKAGES.items():
        if meta.get("base") == package:
            return name
    match = re.match(r"^ros-([a-z]+)-ros-base$", package)
    return match.group(1) if match else None


def dependencies(name):
    return list(CATALOG[name]["deps"])


def kind_of(name):
    return CATALOG.get(name, {}).get("ros", ROS2)


def describe(name):
    meta = CATALOG.get(name)
    if not meta:
        return name
    return "{}({})".format(name, meta["ros"])


def installed():
    return sorted(path.split("/")[3] for path in glob.glob("/opt/ros/*/setup.bash"))


def verify(name):
    return os.path.exists("/opt/ros/{}/setup.bash".format(name))


def plan_for(codename, arch, available, first=None):
    sources = sources_for(codename, arch, first=first)
    version = pick_available(codename, available)
    return {
        "sources": sources,
        "version": version,
        "recommended": recommend(codename),
    }
