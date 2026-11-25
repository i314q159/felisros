# felisros

ROS / ROS2 图形化安装器。把鱼香 ROS（fishros）一键安装脚本里的核心决策逻辑拆成可复用的 Python 模块，再套一层本地 Web 界面。

**没有遥测，没有统计上报，没有广告位。** 详见下文「隐私：没有遥测」。

Linux 版是**完整版**：本机安装 ROS、局域网分发、设备互更新、批量部署全部可用。Windows 版是**控制端**：不会在 Windows 本机安装或更新自身，只负责把软件、更新和配置批量分发给局域网里的 Linux 设备。整条分发链没有账号、授权、中心服务器或许可校验，任何拿到安装包的人都可以自由复制、安装、再分发给其他设备。

**主界面是本地 Web 界面**（`app.py`），浏览器打开即可使用；局域网分发与备用控制入口也都在同一个界面里。

名字里的 `felis` 是猫属（*Felis catus*），与 fishros 的「鱼」对应；同时避开 ROS 圈已被 catkin 占用的 `cat` 前缀。

## 与 fishros 的关系

- 安装流程参考 `github.com/fishros/install` 中的 `install.py`、`tools/tool_install_ros.py`、`tools/tool_config_system_source.py`、`tools/base.py`，属于独立重写，**不下载、不执行、不依赖** fishros 的任何脚本。
- fishros 原项目采用 **CC BY-NC-SA 4.0**（署名 - 非商业性使用 - 相同方式共享）。参考其设计思路时请保留署名并遵守原许可； felisros 自身新增代码的许可由使用者自行决定。
- 不是 fishros 官方项目，与「小鱼」及鱼香 ROS 社区无关联。出问题请自行排查，不要向 fishros 社区反馈。

## 隐私：没有遥测

fishros 一键安装会在启动时上报 `run`、上报每一次菜单选择，并后台访问 `fishros.org.cn/forum/topic/1733` 计数，还会在菜单和结束时从 `/api/install/ads` 拉取推广链接。**felisros 一项都没有实现。**

具体来说：

| 行为 | fishros | felisros |
| --- | --- | --- |
| 启动次数 / 菜单点击上报 | 有 | **无** |
| 后台访问论坛帖计数 | 有 | **无** |
| 商品推广 / 广告接口 | 有 | **无** |
| 崩溃日志自动回传 | 预留 | **无** |
| 设备指纹、UUID、账号、用户名采集 | 无 | **无** |
| 版本清单里自定义镜像主机 | 不适用 | 仅当你导入清单并点击安装后才会访问，界面会显示来源 |
| 配置文件内容上传 | 不适用 | **无**，全部在本机解析 |
| 设备扫描结果、SSH 密码、sudo 密码上传 | 不适用 | **无**，扫描和连接都从你的机器直接发起；API 返回已脱敏 |
| 读取 fishros.com / fishros.org.cn | 有 | **无** |
| 批量安装结果/用时回传 | 不适用 | 仅回传到发起任务的 Windows 控制端；设备本机 `127.0.0.1`，无外部服务器 |
| 设备互更新 / 版本广播 | 不适用 | 仅在本地局域网 UDP 8738 广播与 TCP 8737 拉取更新包，不连公网、不上报 |

发现只针对你勾选的本地网卡网段（可多选）或手动填写的本地网段，用 ICMP ping 与 TCP 22 端口探测；不会向公网扫描，也不会把结果发到任何服务器。

 felisros 发起的网络请求只有下面几类，除局域网设备互更新外，其余**全部发生在你点击「开始安装」之后**：

| 用途 | 主机 | 代码位置 |
| --- | --- | --- |
| 系统 / ROS 镜像测速与包下载 | `mirrors.ustc.edu.cn`、`mirrors.tuna.tsinghua.edu.cn`、`repo.huaweicloud.com`、`mirrors.aliyun.com`、`mirrors.163.com`、`packages.ros.org` 等 | `core/mirrors.py`、`core/rosrepo.py`、`core/apt.py` |
| ROS 公钥 | **无网络请求**：公钥随安装包内置，安装时本地写入目标机 | `core/data/ros-archive-keyring.gpg`、`core/rosrepo.py` |
| 版本清单里由用户填写的镜像（仅导入并部署后） | 由清单指定，例如 `ros.lab.example` | `core/ros.py`、`core/rosrepo.py` |
| 批量任务中遥控已安装的 felisros | 目标设备本机 `127.0.0.1:<服务端口>`，从控制端经 SSH 发起 | `core/remote.py` |
| 设备互发现与更新包拉取 | 用户所在局域网的设备 IP（UDP 8738 广播 + TCP 8737 `/api/release`、`/download`） | `core/peers.py`、`core/release.py` |

可以自己 grep 审计（除镜像测速外，只会命中本机回环地址与内置结果解析函数）：

```bash
grep -RInE 'fishros\.org\.cn|/api/install|StatUtils|AdUtils|topic/1733|urlopen|127\.0\.0\.1' felisros/
```

本地写入只有：`/etc/apt/sources.list*`（勾选换系统源时，原文件备份为 `*.felisros.bak`）、`/etc/apt/sources.list.d/felisros.list` 与 `/usr/share/keyrings/felisros-ros.gpg`（**仅当设备没有可复用的 ROS 源、需要 felisros 自己写源时**）、用户 `~/.bashrc`（或 `~/.zshrc`），以及 `~/.config/felisros/versions.json`、`~/.config/felisros/fleet.json`、`~/.config/felisros/presets.json`、`~/.config/felisros/update.json`（含密码的文件权限为 `0600`）。设备已有的 ROS 源文件不会被改写。除此之外不写任何其他持久状态。

## 依赖

Python 只用标准库，前端资源全部随项目附带、不加载任何 CDN；不需要 `pip install`（`requirements.txt` 仅作说明）。

| 场景 | 需要 |
| --- | --- |
| 基本运行 | Python 3.8+、现代浏览器 |
| Linux 真实安装 ROS | `sudo`、`apt-get` 或 `aptitude`、`curl` 或 `wget`、`coreutils`（`base64`、`install`、`cp`、`tee`）、`sed` |
| 设备发现 | `ip`、`ping`（iproute2 / iputils） |
| 局域网批量部署与遥控 | `ssh` 客户端；目标设备需可 SSH 登录并具备 sudo 权限 |
| 后台运行与退出 | 分发后自动后台启动（不设置开机自启），设备上可用 `felisros --stop` 退出 |
| 安装 / 卸载 / 打包脚本 | POSIX `sh`、`cp`、`sed`、`pkill`（卸载）、`crontab`（可选清理） |

Windows / macOS 作为控制端只需要 Python 3 与浏览器；上面的系统命令只在被控的 Linux 设备上执行。

## 运行

真实安装必须在 Linux 上、且有 sudo 权限。

```bash
cd felisros
python3 app.py
```

`app.py` 会启动本机 HTTP 服务并打开浏览器界面，不需要安装任何第三方包。

服务默认绑定 `0.0.0.0`，浏览器界面是 `http://127.0.0.1:8737`。「安装模式」里有「本机 sudo 密码」输入框：填上当前登录用户的 sudo 密码，程序就用 `sudo -S` 从标准输入读密码执行 apt 与系统源写入，密码不落到命令行和日志里。不想每次输入时，也可以先用 `sudo -v` 缓存凭证后留空，或直接 `sudo python3 app.py` 以 root 运行（程序会通过 `SUDO_USER` 找到真正用户的 shell 配置文件）。开始安装前会先校验 sudo：免密可用、密码正确、密码错误、当前用户不在 sudoers 里都有对应提示，不会在没权限时把每个镜像源都重试一遍。

在 Windows / macOS 上不能真实安装 ROS，但界面会明确标记为**控制端**：单机安装按钮被禁用，不允许更新自身；「配置生成器」「局域网批量部署」「局域网分发」「设备互更新」仍可使用。也可以给局域网里的多台 Linux 设备批量安装软件、推送更新，再遥控它们安装 ROS。

参数：

| 参数 | 作用 |
| --- | --- |
| `--host` | 绑定地址，默认 `0.0.0.0`（监听所有网卡）；只想本机访问时填 `127.0.0.1` |
| `--port` | 端口，默认 `8737` |
| `--no-browser` | 不自动打开浏览器 |
| `--lan` | 兼容旧用法的空操作，等价于默认的 `--host 0.0.0.0` |
| `--deploy FILE` | 按 JSON 配置文件无界面部署，完成后返回退出码 0/1 |
| `--fleet FILE` | 按设备清单 JSON 批量部署，完成后返回退出码 0/1 |
| `--report FILE` | 配合 `--fleet`，把每台设备的结果与用时写入 JSON 报告 |
| `--versions FILE` | 启动时导入版本清单 JSON |
| `--no-beacon` | 不向局域网广播本机版本（默认每 3 秒广播一次） |
| `--check-update` | 检查局域网是否有更新版本后退出 |
| `--apply-update` | 从更新源拉取并安装最新版本后退出（仅 Linux） |
| `--update-seed URL` | 手动指定更新源，如 `http://192.168.1.20:8737` |
| `--no-restart` | 更新后不自动重启服务，配合 `--apply-update` 使用 |
| `--stop` | 停止后台运行的 felisros（分发时自动启动的那个）后退出，同时清掉旧版本留下的 systemd / crontab 自启项 |

`--deploy` 无界面部署时，sudo 密码可用环境变量 `FELISROS_SUDO_PASSWORD` 提供，避免写进命令行。

## 分发到 Linux

项目自带打包脚本，只依赖 Python 标准库。启动服务、打开局域网页面或执行批量分发时会自动检测源码变更并重建 `dist/`；也可以手动执行：

```bash
python3 build.py
```

产物在 `dist/`：`felisros-linux.tar.gz`（另附 `felisros-linux.zip`）。解压后有两种用法：

```bash
tar xzf felisros-linux.tar.gz
cd felisros-linux

./bin/felisros --port 8737
```

或者安装到当前用户目录并注册桌面入口（不需要 root）：

```bash
./install.sh
felisros
```

`install.sh` 把程序复制到 `~/.local/share/felisros/app`，启动器放到 `~/.local/bin/felisros`，桌面项放到 `~/.local/share/applications/felisros.desktop`。如果 `~/.local/bin` 不在 PATH 里，脚本会提示添加。卸载用 `./uninstall.sh`；导入过的版本清单不会自动删除。

启动器 `felisros` 直接打开网页端界面，无需任何第三方依赖。网页端同时用于局域网分发与本地控制；默认监听所有网卡地址，可用 `--host 127.0.0.1` 改为只监听本机。

注册桌面入口和 `install.sh` 都不会写开机自启：远程分发时由控制端在目标设备上后台拉起一次（`setsid` 启动，SSH 断开也继续跑），设备重启后需要时手动运行 `felisros`。退出后台服务用 `felisros --stop`。

## 配置文件一键部署

配置文件格式 `felisros/deploy/v1`，完整示例见 `examples/deploy.example.json`：

```json
{
  "schema": "felisros/deploy/v1",
  "name": "实验室 ROS 一键部署",
  "version": "jazzy",
  "kind": "desktop",
  "mirror": "ustc",
  "change_system_source": true,
  "extra_packages": ["ros-jazzy-turtlesim"]
}
```

字段说明：

| 字段 | 取值 | 说明 |
| --- | --- | --- |
| `version` | 版本名或 `auto` | 必须来自内置版本表或已导入的清单 |
| `kind` | `desktop` / `base` / `auto` | 桌面版、基础版、按磁盘余量自动决定 |
| `mirror` | 镜像名或 `auto` | 镜像名见界面下拉框，`auto` 为测速选择 |
| `change_system_source` | 布尔值 | 是否同时把系统 apt 源换成国内镜像 |
| `extra_packages` | 软件包名数组 | 最多 30 个，安装 ROS 本体后追加安装 |

三种用法：

- 界面：在「配置文件 / 版本导入」卡片选择文件或粘贴 JSON，点「预览配置」→「一键部署」。
- 界面：点「填入表单」把配置回填到上面的选择框，确认后再点「开始安装」。
- 命令行：`python3 app.py --deploy deploy.json --no-browser`，日志直接打到终端，成功返回 0，失败返回 1。

界面里的「导出当前配置」按钮会把当前选择保存成 `felisros-deploy.json`，可作为模板继续改。所有软件包名、版本名和地址都会经过严格校验，避免配置文件变成命令注入入口。

## 导入 ROS 版本清单

版本清单格式 `felisros/versions/v1`，示例见 `examples/versions.example.json`。它可以新增自定义发行版别名、私服镜像地址以及「哪个系统代号允许用哪些镜像」的映射：

```json
{
  "schema": "felisros/versions/v1",
  "name": "实验室内网版本清单",
  "versions": [
    {
      "name": "jazzy-lab",
      "ros": "ROS2",
      "status": "LTS",
      "codenames": ["noble"],
      "packages": {
        "desktop": "ros-jazzy-desktop",
        "base": "ros-jazzy-ros-base"
      },
      "deps": ["python3-colcon-common-extensions"],
      "recommended": true
    }
  ],
  "mirrors": [
    { "name": "lab", "label": "实验室内网", "ROS2": "https://ros.lab.example/ros2/ubuntu/" }
  ],
  "support": { "noble": ["lab", "ustc"] }
}
```

- `versions[].packages` 可省略，省略时按 `ros-<版本>-desktop` / `ros-<版本>-ros-base` 推导；自定义包名用于私服上的非标准命名。
- `support` 引用的镜像必须在内置镜像或本清单的 `mirrors` 里定义。
- 界面：选文件 →「校验清单」→「导入版本」，导入后版本与镜像下拉框立即刷新；「清除导入」恢复内置版本表。
- 命令行：`python3 app.py --versions versions.json`，会保存到 `~/.config/felisros/versions.json`，下次启动自动载入。
- 版本名、系统代号和镜像名只允许小写字母/数字/连字符，地址只允许 `http(s)`，避免清单写入危险命令。

## ROS 软件源与签名校验

安装 ROS 前要先有可用的 apt 源。felisros 的策略是**先复用、再自建**，尽量不动设备已有配置：

1. **优先复用设备已有源**：扫描 `/etc/apt/sources.list`、`/etc/apt/sources.list.d/*.list` 以及 deb822 格式的 `*.sources`，找出与当前系统代号匹配的 ROS 源（`*ros2*/ubuntu`、`*ros/ubuntu`、`packages.ros.org` 等）。命中后先删除 felisros 自己历史上写过的 `felisros.list`（只删自己的，避免同一仓库出现两遍），再执行 `apt-get update` 并用 `apt-cache search ros-base` 确认能取到包。通过就直接复用，界面的「ROS 软件源」会显示实际复用的镜像（例如 `ustc`）。
2. **复用不通过才自建**：把随包内置的 ROS 归档公钥写到 `/usr/share/keyrings/felisros-ros.gpg`，源行写成 `deb [arch=… signed-by=/usr/share/keyrings/felisros-ros.gpg] …`。这里**不使用 `trusted=yes`**，apt 会正常校验仓库签名；公钥也不再联网下载，离线或只有内网镜像时同样可用。内置公钥为 Open Robotics 的 `C1CF6E31E6BADE8868B172B4F42ED6FBAB17C654`，可用 `gpg --show-keys core/data/ros-archive-keyring.gpg` 核对。
3. **仍然失败就换源重试**：`apt update` 失败、源里查不到 ROS 包、或探测到某个镜像的安装包已被删除（HTTP 403/404/410）时，自动排除该镜像并换下一个，最多重试 8 轮。

几点实际影响：

- 选了「ROS 镜像源」但设备已有可用源时，**已有源优先**，所选镜像不会生效；想强制使用指定镜像，先删掉设备上的 ROS 源文件再安装。
- 自建源时写入的 `felisros.list` 和 `felisros-ros.gpg` 在卸载 felisros 时不会自动删除；需要恢复原状时手动删除这两个文件，再 `apt-get update` 即可。
- 已有源不可用、回退自建后，两套源可能同时存在，日志里会给出「现有 ROS 软件源不可用，改用内置镜像」的告警，可按需手动清理旧文件。
- 复用的判定包含「该源能提供当前系统支持的 ROS 版本」，避免把只有 ROS1 源的机器误判成可装 ROS2。

## 局域网自由分发

Windows 端可以把自己变成分发站，让局域网内所有用户自由、免费地安装 felisros，目标机不需要预装任何客户端：

```bash
python app.py
```

`dist/` 会在启动时自动检查并按需重建，无需每次手动运行 `python build.py`。

启动后会打印局域网地址。局域网用户在自己的 Linux 机器上只需执行一行命令：

```bash
curl -fsSL http://<Windows 的局域网 IP>:8737/install.sh | sh
```

这个脚本会：

1. 自动识别架构（`amd64` / `x86-64`、`arm64` / `aarch64`、`armhf` / `armv7` 等），未知架构也会尝试安装（felisros 是纯 Python，架构无关，只要求目标机有 `python3` 和 `curl`/`wget`）。
2. 从 Windows 端下载安装包并校验 SHA256。
3. 安装到用户目录 `~/.local`，不需要 root。
4. 安装后运行 `~/.local/bin/felisros` 即可打开界面。

界面顶部第四个标签页「局域网分发」会显示当前可用的安装命令、分发页地址、直接下载链接与 SHA256，并可一键复制。局域网用户打开分发页或粘贴一键安装命令，就能免费自行安装 felisros，不需要账号、不需要预装客户端。没有授权、遥测或回传，安装包可任意复制分发。服务默认监听所有网卡，建议只在可信的局域网内运行；如需仅本机访问，用 `python app.py --host 127.0.0.1` 启动。

### 主动批量分发

同一个「局域网分发」页的下半部分是主动批量分发面板，用于**不依赖对方操作**、由控制端主动把 felisros 推送到多台 Linux 设备：

1. 勾选本机网卡（可多选，也可填写额外 CIDR；全部留空则扫描所有网卡），点「扫描设备」，默认只列出 Linux 设备。
2. 勾选目标设备，可逐台覆盖用户名 / SSH 密码 / Sudo 密码，也可在「统一登录信息」里一次填好（SSH 密码留空表示使用密钥登录）。
3. 设置并发数与服务端口，点「批量分发软件」。分发完成后目标设备上的 felisros 会在后台启动（不设置开机自启）；需要退出时在设备上执行 `felisros --stop`，重启后手动运行 `felisros` 即可。

它与「局域网批量部署」共用同一套批量引擎（`software` 任务）和结果回传：每台设备的架构、用时与成功/失败原因直接显示在面板下方，也可点「导出报告 JSON」保存。目标机只需能 SSH 登录、有 `python3`，无需预装任何客户端。被动安装与主动分发可以并存——想让别人自己装就发安装命令，想统一部署就用这个面板。

注意：从这里启动任务会把本次配置保存为当前设备清单（与「局域网批量部署」页共用），方便在批量页继续查看或复用。

## 设备互更新（自由分发网络）

顶部第五个标签页「设备互更新」让每台装了 felisros 的 Linux 设备既是使用者也是更新源：

1. 服务启动后每 3 秒通过 UDP 8738 广播自己的版本号、SHA256 与服务端口；同时监听其他设备的广播。已安装设备也能按需从本地源码现场生成安装包，所以不需要预先持有 `dist/` 才能当种子。
2. 界面点「扫描更新源」会按勾选的本机网卡（可多选，留空扫描全部网卡）和额外 CIDR 做一次 ICMP 扫描，并对在线设备直连 8737 端口读取 `/api/release`，兼容不转发 UDP 广播的网络；也可手动填写 `http://IP:8737`。
3. 「检查更新」比较版本号，挑出最高的更新源；「立即更新」下载安装包、校验 SHA256、原子替换程序目录，保留最近三份备份并重启服务。
4. 「自动更新」可设置检查间隔（最少 60 秒），发现更高版本自动拉取并重启，设置保存在 `~/.config/felisros/update.json`。

Windows 控制端不会更新自身，但可以把本机当作更新源，让 Linux 设备从这里拉取；也可以在批量任务里让目标设备从指定种子更新。整个过程没有中心服务器、没有许可校验、没有遥测，只有设备之间的局域网直连。

命令行也能检查/更新：

```bash
python3 app.py --check-update
python3 app.py --apply-update --update-seed http://192.168.1.20:8737
```

## 局域网设备发现与批量部署

界面顶部切到「局域网批量部署」即可使用。控制端只需装了 OpenSSH 客户端（`ssh` 命令，Windows 10/11 自带即可），**不要求控制端是 Linux**。批量部署会真实连接设备。目标设备需要开启 SSH，且提供的用户能登录。

批量部署支持五种任务，在「部署任务」下拉框切换：

- **先分发 felisros 软件，再遥控安装 ROS（`rollout`，默认）**：这是完整的两步流水线。第一步通过 SSH 把安装包推到设备、装到用户目录并在后台启动服务（不设置开机自启，也不会写 systemd 单元或 crontab）。第二步把选好的 `felisros/deploy/v1` 配置文件下发到设备，**调用设备上刚装好的 felisros 本机 API 遥控执行安装**，完成后把成功/失败、ROS 版本、镜像源、原因和用时回传到 Windows 端。目标机只需 SSH 和 `python3`，不用预装任何东西。
- **先更新 felisros，再遥控安装 ROS（`update_rollout`）**：让已装 felisros 的设备从指定更新源拉取最新版本、重启服务，再把配置下发并遥控安装 ROS。未安装 felisros 的设备会自动改为分发当前版本。
- **只分发 felisros 软件（`software`）**：仅做软件分发，不安装 ROS。
- **只更新已安装的 felisros（`update`）**：设备从更新源拉取新版本、校验、替换并重启；回传更新后的版本号和用时。
- **只安装 ROS（`ros`）**：设备本机临时运行一次 felisros 安装器，装完清理，不保留客户端。

更新任务的「更新源地址」留空时使用控制端本机地址；也可填另一台已更新设备的 `http://IP:8737`。SSH 会先识别目标系统，非 Linux 设备直接拒绝，不会给 Windows 自己安装任何东西。

**只输入用户名和密码**：在「凭据预设」里填一次默认用户名和密码（Sudo 密码留空自动等于 SSH 密码），点「保存预设」即可长期复用。扫描到的 Linux 设备会自动套用这组凭据，逐台不需要再填；界面默认勾掉非 Linux 设备，避免误操作。预设保存在 `~/.config/felisros/presets.json`（权限 `0600`），API 返回一律脱敏，密码支持 `${环境变量名}`。也可以只填不保存，直接用于本次批量任务。

**Linux 嗅探**：扫描时除了 ping 与 22 端口，还会读取 SSH 横幅、ping TTL、探测 8737 端口（已装 felisros 的设备）。横幅含 `Ubuntu`/`Debian`/`OpenSSH…Linux` 等特征、TTL=64、或已运行 felisros 的设备会被标记为 Linux；Windows（TTL=128）与未知设备默认不显示。表格里新增「系统」列，可用「只显示 Linux 设备」开关切换。

**勾选设备与选择配置文件**：先在「局域网设备发现」里扫描并勾选设备（可填统一预设的用户名/密码，也可逐台覆盖）。扫描结果里只有被勾选的设备会进入下方进度列表，取消勾选会立即从列表移除。然后在「部署任务」里点「选择配置文件」从 Windows 本地挑一份 `felisros/deploy/v1` JSON；点「重新选择」可随时换一份，点「校验配置」先验证格式。也可以直接粘贴 JSON，或用「用单机配置填入」复制当前页面的参数。最后点「开始批量部署」。

**发现逻辑**：自动枚举本机所有网卡的 IPv4 网段，界面上列出网卡名、地址和 CIDR，默认全部勾选，可只选其中几块网卡；也可以在下方手填额外网段，多个用逗号分隔（例如 `192.168.1.0/24, 10.0.0.0/24`）。扫描会合并所有选中网段并去重后，先并行 ping，再对在线设备探测 22 端口、抓取 SSH 横幅、读 ping TTL、探测 8737 端口，并从 `/proc/net/arp` 取 MAC、尝试反查主机名。综合这些信号判断是不是 Linux 设备。为避免误扫，单个网段最多 2048 个地址，超出请缩小范围。UDP 8738 广播也会被发往每块网卡的定向广播地址，便于多网段下的设备互发现。

**结果回传**：每台设备完成后，Windows 端通过 SSH 读取设备 felisros 服务返回的 `done` 事件，得到结构化结果。界面下方「安装结果与用时回传」表格逐台显示阶段、架构、ROS 版本、用时与原因，并可导出 `felisros-fleet-report.json`。命令行加 `--report report.json` 可把同一份报告写入文件。这个回传是 Windows 控制端主动向自己管理的设备取结果，**不会发到任何第三方服务器**。默认 3 台并发，可在界面调到 1～99，可随时取消。

**设备清单**格式 `felisros/fleet/v1`，示例：

```json
{
  "schema": "felisros/fleet/v1",
  "name": "实验室设备",
  "network": ["192.168.1.0/24", "10.0.0.0/24"],
  "task": "update_rollout",
  "update_seed": "http://192.168.1.20:8737",
  "preset": "lab",
  "port": 8737,
  "defaults": {
    "username": "pi",
    "password": "${FLEET_PASSWORD}",
    "sudo_password": "${FLEET_SUDO_PASSWORD}"
  },
  "devices": [
    { "ip": "192.168.1.12", "alias": "机器人一号", "selected": true },
    { "ip": "192.168.1.20", "username": "admin", "password": "本机专用密码", "selected": true },
    { "ip": "192.168.1.35", "selected": false }
  ],
  "deploy": {
    "schema": "felisros/deploy/v1",
    "name": "批量 ROS 部署",
    "version": "jazzy",
    "kind": "base",
    "mirror": "auto",
    "change_system_source": false,
    "extra_packages": []
  }
}
```

`network` 可以是网段数组（如上），也可以是单个 CIDR 字符串（旧格式兼容）；填 `"auto"` 或不填表示扫描时自动使用本机所有网卡网段。

- `task`：`rollout` 表示先推送安装包并装好 felisros，再把 `deploy` 下发并遥控安装 ROS、回传结果；`update_rollout` 先让设备从更新源升级 felisros 再做同样的事；`software` 只做软件分发，`update` 只升级 felisros，`ros` 只在目标机临时运行安装器。省略时：带 `deploy` 则用 `rollout`，带 `versions` 用 `ros`，否则 `software`。
- `update_seed`：更新任务的来源地址，留空则用控制端本机。完整示例见 `examples/fleet-update.example.json`。
- `preset`：引用 `presets.json` 里保存的用户名/密码预设；批量启动时自动套用到所有目标设备，密码不会出现在 API 返回或日志里。
- `port`：软件在目标设备上监听的服务端口（默认 8737）；`rollout` 任务也用它。分发后只在本次会话里后台启动，不设置开机自启（旧清单里的 `autostart` 字段会被忽略）。
- `defaults` 是全局默认凭据；`devices[]` 里的 `username` / `password` / `sudo_password` 会覆盖默认值。
- `sudo_password` 省略时自动使用同设备的 SSH 密码。
- 密码字段支持 `${环境变量名}`，启动时从环境变量解析；推荐用它代替明文。
- 界面里可扫描后手工填每台设备的用户名/密码，也可导入/导出上述 JSON。导出文件如果含明文密码，请自行设置权限（例如 `chmod 600 fleet.json`）。
- 命令行：`python3 app.py --fleet fleet.json --no-browser --report report.json`，日志打到终端，成功返回 0；`report.json` 含每台的 IP、阶段、架构、ROS 版本、用时与结果，适合放进已有的运维脚本。
- 界面也可调 `GET /api/fleet/report` 拿到同一份结构化报告。
- 服务端保存的 `fleet.json` 权限为 `0600`；所有 API 返回的密码字段都会被清空，SSH 命令日志、批量日志与结果里都不会出现密码。
- 密码认证通过系统的 SSH askpass 机制完成，不依赖 `sshpass`；如果目标设备配置了密钥登录，把密码留空即可。

## 在 Windows 上生成配置文件（含远程部署勾选）

界面顶部第三个标签页「配置生成器」专门用于**制作配置**，不执行安装、不保存文件、不导入清单。因此在 Windows / macOS 控制端生成的文件与 Linux 上生成的文件完全等价，可以直接拿到目标机器使用。

用法：

1. 在 Windows 上运行 `python app.py`，浏览器打开后切到「配置生成器」。
2. 填写 ROS 版本、安装内容、镜像源、是否换系统源、额外软件包。
3. **不勾选「远程部署」**：生成 `felisros/deploy/v1` 单机配置，下载得到 `felisros-deploy.json`。在目标 Linux 设备上用 `python3 app.py --deploy felisros-deploy.json --no-browser` 执行。
4. **勾选「远程部署」**：展开设备区域，选择任务类型（默认「分发软件 + 遥控安装 ROS」；也可选「设备互更新 + 遥控安装 ROS」、只分发软件、只更新已装软件或只装 ROS），填写更新源（更新任务用，留空用本机）、目标网段、默认用户名/密码/sudo 密码、服务端口和并发数，生成 `felisros/fleet/v1` 批量清单，下载得到 `felisros-fleet.json`。在控制端用 `python3 app.py --fleet felisros-fleet.json --no-browser --report report.json` 执行；如果控制端开着界面，也可以点「载入到批量部署」直接跳过去执行。

目标设备每行一台，格式为 `IP[,用户名[,SSH密码[,Sudo密码]]]`，后三项留空则继承默认值。例如：

```text
192.168.1.12
192.168.1.20,pi,raspberry
192.168.1.35,pi,${ROBOT_PASS},${ROBOT_SUDO}
```

生成的文件只是 JSON，不含执行逻辑，也不会回传任何内容。后端接口是 `POST /api/config/build`，只做 schema 校验后把文本返回给浏览器，**不落盘**；下载由浏览器通过 Blob 完成，文件直接进 Windows 的下载目录。

凭据注意：勾选远程部署并填入明文密码时，密码会写进下载的文件里。建议用 `${环境变量名}` 引用（在目标控制端提前设好环境变量），或在下载后执行 `chmod 600 felisros-fleet.json` 并妥善保管。

## 界面流程

1. **系统卡**：发行版、代号、架构、磁盘余量、已装 ROS 版本。
2. **模式选择**：全自动（测速 + 推荐版本 + 空间不足降级）或自定义（版本 / 桌面版 / 镜像源）。
3. **执行**：六步进度条 + 实时滚动的命令日志，可随时取消。
4. **结果**：本次自动选择清单 + 可直接复制的验证命令（如 `ros2 run turtlesim turtlesim_node`）。

页面里的复制按钮会先尝试 `navigator.clipboard`；用 `http://<局域网 IP>:8737` 打开时属于不安全上下文，浏览器不提供该 API，程序会自动退回 `document.execCommand("copy")`，两种环境都能复制并提示「已复制」。

## core 包（函数封装清单）

| 模块 | 主要 API | 对应 fishros 逻辑 |
| --- | --- | --- |
| `system.py` | `SystemInfo.to_dict()` | `distro` / `osversion` 系统识别 |
| `runner.py` | `Runner.run()`、`Runner.cancel()` | `CmdTask` 命令执行与输出流 |
| `apt.py` | `Apt.search_base()`、`Apt.install_with_repair()` | `AptUtils` + aptitude / fix-broken / fix-missing 兜底 |
| `mirrors.py` | `rank()`、`write()` | 下载 `dists/<codename>/Release` 测速、写系统源 |
| `sysrepo.py` | `switch()` | `tool_config_system_source.change_sys_source` |
| `ros.py` | `sources_for()`、`recommend()`、`pick_available()` | 版本表 + `ros_dist_dic` / `ros2_dist_dic` 能力矩阵 |
| `keys.py` | `install()` | 早期「多来源下载公钥 + 指纹校验」方案，现已不再被调用（文件保留）；当前由 `rosrepo.py` 使用内置公钥 |
| `rosrepo.py` | `ensure()`、`existing()`、`stale_mirrors()` | 复用设备已有 ROS 源、内置公钥 `signed-by` 写源、坏包 HEAD 探测（403/404/410 换源） |
| `shellenv.py` | `configure()` | 写 `.bashrc` + 修复 `rosidl_adapt_interfaces.cmake` 中文路径编译问题 |
| `installer.py` | `Installer.start()`、`EventBus.since()` | 菜单流程 + 状态机 + 日志，替换为事件流 |
| `config.py` | `parse_versions()`、`parse_deploy()`、`parse_fleet()`、`upsert_preset()`、`redact_presets()` | 新增：版本清单 / 部署配置 / 设备清单 / 凭据预设的校验、持久化和脱敏 |
| `discovery.py` | `default_network()`、`discover()`、`classify_os()` | 新增：网段识别、ping / SSH 横幅 / TTL / 8737 端口嗅探 Linux 设备 |
| `remote.py` | `SshClient.install()`、`SshClient.update_software()`、`SshClient.install_ros_remote()` | 新增：SSH 上传、askpass 密码认证、sudo 提权与日志脱敏 |
| `fleet.py` | `Fleet.start()`、`Fleet.cancel()`、`Fleet.snapshot()` | 新增：多设备并发部署、更新与 ROS 安装的两阶段编排 |
| `capabilities.py` | `manifest()`、`allow()` | 新增：Linux 完整端 / Windows 控制端能力边界 |
| `release.py` | `local_manifest()`、`install_from()`、`compare()` | 新增：版本号、清单、SHA256 校验与原子自更新 |
| `peers.py` | `PeerBeacon`、`check_update()`、`apply_update()`、`AutoUpdater` | 新增：局域网互发现、拉取更新与自动更新 |

刻意没有照搬的部分：编号菜单（改成 API）、`FISHROS_URL` 远程脚本链（改成全本地模块）、`--no-check-certificate` 全局关证书校验、以及上文列出的遥测与广告。

## 目录结构

```
felisros/
├── app.py                 本地 HTTP 服务、JSON API、HTML 界面与命令行入口
├── felisros.toml          软件版本号，直接编辑 [app] version 即可
├── build.py               生成可分发到 Linux 的 tar.gz / zip
├── packaging/             分发用启动器、安装/卸载脚本、桌面入口
├── examples/              部署配置与版本清单示例
├── core/
│   ├── __init__.py
│   ├── system.py           系统、架构、磁盘识别
│   ├── runner.py           命令执行、超时、取消
│   ├── apt.py              apt 操作与依赖修复阶梯
│   ├── config.py           配置解析、凭据预设、校验、持久化与脱敏
│   ├── capabilities.py     平台角色与能力边界（Windows 控制端）
│   ├── release.py          版本、清单、更新包生成与原子自更新
│   ├── peers.py            UDP 广播互发现、更新检查与自动更新
│   ├── discovery.py        局域网扫描与设备识别
│   ├── remote.py           SSH 上传、密码认证与远程安装/更新
│   ├── service.py          后台服务启动与 --stop 停止逻辑
│   ├── fleet.py            批量部署编排与事件
│   ├── mirrors.py          系统镜像表、测速、写源与备份
│   ├── sysrepo.py          系统源切换流程
│   ├── ros.py              ROS 版本 / 镜像能力矩阵 + 导入覆盖
│   ├── keys.py             早期公钥下载方案（保留，未使用）
│   ├── rosrepo.py          复用已有 ROS 源、内置公钥 signed-by 写源、坏包探测
│   ├── data/
│   │   └── ros-archive-keyring.gpg  ROS 归档公钥（随包分发，供 signed-by 校验）
│   ├── shellenv.py         shell 配置与中文路径修复
│   └── installer.py        六步编排、事件总线
├── web/
│   ├── index.html
│   ├── style.css
│   └── app.js
└── dist/                   build.py 的产物（生成）
```

## 常见问题

**Q：日志里密码显示成 `******`，用户名会跟着变吗？**
不会。脱敏只替换**单独出现**的密码（前后都不是字母或数字），密码是 `admin` 时用户名 `admin1` 仍原样显示；同一段文本里出现多组密码时优先匹配更长的那个。

**Q：用局域网 IP 打开页面，复制按钮点不动？**
旧版本直接依赖 `navigator.clipboard`，而 `http://<局域网IP>` 不是安全上下文，该 API 不存在，点击会抛错。现在统一走 `copyText()`：能拿到剪贴板 API 就用，拿不到就退回离屏 `textarea` + `execCommand("copy")`，失败时才提示手动复制。

**Q：设备上已经配好 ROS 源，会被 felisros 改掉吗？**
不会。它只读取这些文件；只要源可用就直接复用，不改写。需要自己写源时只新增 `felisros.list`，并删除 felisros 以前留下的同名文件，不动用户自己的 `ros2.list` / `ros-fish.list` 等。

**Q：为什么要内置 ROS 公钥？**
目标机常常没有外网，运行时下载 `raw.githubusercontent.com` 上的 key 会失败。内置公钥后可以离线完成签名校验，同时避免再用 `trusted=yes` 关闭校验。

**Q：改了控制端代码，界面没变化？**
控制端需要重启 `python app.py` 才会加载新的后端代码；Linux 设备端随安装包更新（`release.ensure_package()` 检测到源码比 `dist/` 新会自动重打包）。

## 限制

- 只能在 Linux 上真实安装 ROS；Windows / macOS 是控制端，单机安装与自更新会被禁用，但可管理 Linux 设备。
- 自更新只在 Linux 上生效；更新会从局域网内版本更高的设备拉取，校验 SHA256 后替换程序目录并重启服务，失败时可从保留的 `.backup-*` 目录恢复。
- 换系统源会修改 apt 配置，原文件备份为 `*.felisros.bak`，程序**不会自动还原**，需要时请手动恢复。
- 已有 ROS 源不可用时会回退到自建源，此时两套源可能同时存在（日志会告警）；自建的 `felisros.list` 与 `felisros-ros.gpg` 在卸载时不会自动删除。
- 修改的是系统级包源与密钥，请在自己的机器上自行判断风险。
