from . import mirrors


def rank(system, report=None):
    ranked = mirrors.rank(system)
    for item in ranked:
        if report:
            if item["status"] == "ok":
                report("{}\t{}\t{} KB/s".format(item["label"], item["url"], item["speed"]), "out")
            elif item["status"] == "missing":
                report("{}\t镜像里没有 {} 版本".format(item["label"], system.codename), "info")
            else:
                report("{}\t连接失败".format(item["label"]), "info")
    return ranked


def switch(runner, apt, system, report=None, excluded=None):
    excluded = excluded or set()
    ranked = rank(system, report)
    usable = [item for item in ranked if item["status"] == "ok" and item["name"] not in excluded]
    if not usable and not mirrors.is_debian(system):
        if report:
            report("{} 已停止官方支持, 改用旧版本归档源".format(system.codename), "warn")
        ranked = mirrors.rank(system, mirrors.UBUNTU_OLD_MIRRORS)
        usable = [item for item in ranked if item["status"] == "ok"]
    if not usable:
        if report:
            report("没有找到可用的系统镜像源", "err")
        return None
    for item in usable:
        if report:
            report("尝试系统源: {} {}".format(item["label"], item["url"]), "info")
        if not mirrors.write(runner, system, item["url"]):
            continue
        if apt.update().ok:
            if report:
                report("系统软件源已切换: " + item["label"], "ok")
            return item
        if report:
            report("apt update 未通过, 换下一个系统源", "warn")
    return None
