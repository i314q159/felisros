import glob
import os
import re

BLOCK_START = "# >>> felisros >>>"
BLOCK_END = "# <<< felisros <<<"
CMAKE_GLOB = "/opt/ros/*/share/rosidl_adapter/cmake/rosidl_adapt_interfaces.cmake"


def home_dir():
    user = os.environ.get("SUDO_USER")
    if user and user != "root":
        return os.path.expanduser("~" + user), user
    return os.path.expanduser("~"), None


def rc_path(home):
    shell = os.environ.get("SHELL", "/bin/bash")
    return os.path.join(home, ".zshrc" if shell.endswith("zsh") else ".bashrc")


def strip_block(text):
    pattern = re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END) + r"\n?"
    return re.sub(pattern, "", text, flags=re.S)


def write_block(path, version):
    existing = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as handle:
            existing = handle.read()
    body = "{}\nsource /opt/ros/{}/setup.bash\n{}\n".format(BLOCK_START, version, BLOCK_END)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(strip_block(existing).rstrip("\n") + "\n\n" + body)


def fix_rosidl(runner, report=None):
    for path in glob.glob(CMAKE_GLOB):
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                content = handle.read()
        except OSError:
            continue
        if "idl_tuples ENCODING UTF-8)" in content:
            continue
        if 'idl_output}" idl_tuples)' not in content:
            continue
        cmd = "sed -i.felisros-bak 's#idl_output}}\" idl_tuples)#idl_output}}\" idl_tuples ENCODING UTF-8)#' " + path
        if runner.run(cmd, timeout=60, sudo=True).ok and report:
            report("已修复 {} 在中文路径下编译报错的问题".format(path.split("/")[3]), "ok")


def configure(runner, version, report=None):
    home, owner = home_dir()
    path = rc_path(home)
    write_block(path, version)
    if owner:
        runner.run("chown {} {}".format(owner, path), sudo=True)
    runner.emit("已写入环境变量: " + path, "ok")
    if report:
        report("环境变量写入 {}，新开终端自动生效".format(path), "ok")
    fix_rosidl(runner, report)
