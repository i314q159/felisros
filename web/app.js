const state = {
  env: null,
  mode: "auto",
  since: 0,
  timer: null,
  steps: [],
  result: null,
  running: false,
  deployConfig: null,
  template: null,
  devices: [],
  fleetFromServer: false,
  fleetDirty: false,
  fleetSince: 0,
  fleetTimer: null,
  fleetRunning: false,
  generated: null,
  reports: [],
  configName: "",
  lan: null,
  view: "config",
  peers: [],
  role: "full",
  version: "",
  updateSince: 0,
  updateTimer: null,
  distDevices: [],
  distReports: [],
  distSince: 0,
  distTimer: null,
  distRunning: false,
  presets: { default: "", profiles: [] },
};

const $ = (id) => document.getElementById(id);

function appendLog(boxId, text, level, keep) {
  const box = $(boxId);
  if (!box) return;
  const el = document.createElement("div");
  el.className = level || "out";
  // 后端部分命令行已经带 "$ " 前缀，这里只在缺失时补上
  if (level === "cmd" && text.indexOf("$ ") !== 0) el.textContent = "$ " + text;
  else el.textContent = text;
  const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
  box.appendChild(el);
  if (keep) while (box.childNodes.length > keep) box.removeChild(box.firstChild);
  if (stick) box.scrollTop = box.scrollHeight;
}

function line(text, level) {
  appendLog("log", text, level);
}

function show(view) {
  ["config", "run", "done", "fleet", "generator", "distribute", "update"].forEach((name) => {
    $("view-" + name).hidden = name !== view;
  });
  state.view = view;
  if (view !== "update") stopUpdatePoll();
  $("tab-single").classList.toggle("is-on", ["config", "run", "done"].indexOf(view) >= 0);
  $("tab-fleet").classList.toggle("is-on", view === "fleet");
  $("tab-generator").classList.toggle("is-on", view === "generator");
  $("tab-distribute").classList.toggle("is-on", view === "distribute");
  $("tab-update").classList.toggle("is-on", view === "update");
  if ($("fleet-task-note")) {
    const task = $("fleet-task").value;
    const software = task === "software" || task === "update";
    $("fleet-deploy-field").hidden = software;
    $("fleet-seed-field").hidden = task !== "update" && task !== "update_rollout";
    const notes = {
      software: "目标机无需预装 felisros，只需 SSH 和 python3；架构自动识别。",
      update: "目标机需要已安装 felisros；它们会从指定更新源拉取最新版本并重启服务。未安装时自动改为分发当前版本。",
    };
    $("fleet-task-note").textContent = notes[task] || "使用与单机安装相同的 felisros/deploy/v1 配置，由设备上的 felisros 自动安装 ROS 并回传结果。";
  }
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
}

function renderFacts(env) {
  const s = env.system;
  const rows = [
    ["系统", s.pretty || s.distro],
    ["发行代号", s.codename || "未知"],
    ["架构", s.arch],
    ["磁盘剩余", s.free_gb === null ? "未知" : s.free_gb + " GB"],
    ["已装 ROS", s.ros_versions.length ? s.ros_versions.join(", ") : "无"],
  ];
  $("facts").innerHTML = rows
    .map(([key, value]) => `<div><dt>${key}</dt><dd>${escapeHtml(String(value))}</dd></div>`)
    .join("");
  const note = $("support-note");
  if (env.role === "control") {
    note.className = "note";
    note.textContent = "Windows 控制端：通过局域网批量部署到 Linux 设备，本机不安装 ROS。";
  } else if (!env.supported) {
    note.className = "note bad";
    note.textContent = `当前系统 ${s.codename} 不在支持列表内，仍可尝试自定义安装。`;
  } else {
    note.className = "note";
    note.textContent = `推荐版本：${env.recommend || "由软件源自动决定"}。桌面版约占用 3–4GB，基础版约 1GB。`;
  }
  $("pill-mode").textContent = env.role === "control" ? "局域网控制端" : "真实安装";
}

function renderSudoHint(env) {
  const box = $("sudo-box");
  const hint = $("local-sudo-hint");
  if (!box || !hint) return;
  box.hidden = env.role === "control";
  const sudo = env.sudo || {};
  if (sudo.root) {
    hint.textContent = "当前以 root 运行，不需要 sudo 密码。";
  } else if (!sudo.available) {
    hint.textContent = "系统里没有 sudo，请用 root 账号运行 felisros。";
  } else if (sudo.passwordless) {
    hint.textContent = "本机 sudo 免密可用，密码可以留空。";
  } else {
    hint.textContent = "写入系统源、安装软件包都需要 sudo；留空则只尝试免密或已缓存的 sudo 凭证。";
  }
}

function renderOptions(env) {
  const versions = env.catalog.filter((item) => item.available);
  const list = versions.length ? versions : env.catalog;
  const versionItems = list
    .map((item) => {
      const tag = item.status === 0 ? "EOL" : "LTS";
      const flag = item.recommended ? "（推荐）" : "";
      return `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)} · ${escapeHtml(item.ros)} ${tag}${flag}</option>`;
    })
    .join("");
  $("version").innerHTML =
    '<option value="auto">自动（按系统推荐）</option>' + versionItems;
  const recommended = env.recommend;
  if (recommended && list.some((item) => item.name === recommended)) {
    $("version").value = recommended;
  }
  $("gen-version").innerHTML =
    '<option value="auto">自动（按系统推荐）</option>' + versionItems;
  $("gen-version").value = $("version").value;
  $("quick-version").innerHTML =
    '<option value="auto">自动（按目标设备选择）</option>' + versionItems;
  $("quick-version").value = "auto";
  const mirrorHtml =
    `<option value="auto">自动测速选择</option>` +
    env.mirrors
      .map((item) => `<option value="${escapeHtml(item.name)}">${escapeHtml(item.label)} · ${escapeHtml(item.kinds.join(" / "))}</option>`)
      .join("");
  $("mirror").innerHTML = mirrorHtml;
  $("gen-mirror").innerHTML = mirrorHtml;
}

function renderSteps(steps) {
  state.steps = steps;
  $("steps").innerHTML = steps
    .map(
      (step, index) =>
        `<li data-step="${escapeHtml(step.id)}"><span class="bullet">${index + 1}</span>${escapeHtml(step.title)}</li>`
    )
    .join("");
}

function setStep(id, status) {
  const el = document.querySelector(`#steps li[data-step="${id}"]`);
  if (!el) return;
  el.className = "";
  if (status && status !== "run") el.classList.add("is-" + status);
  const bullet = el.querySelector(".bullet");
  if (status === "ok") bullet.textContent = "✓";
  else if (status === "fail") bullet.textContent = "×";
  else if (status === "warn") bullet.textContent = "!";
}

function applyEvent(event) {
  if (event.type === "log") line(event.text, event.level);
  if (event.type === "step") setStep(event.step, event.status);
  if (event.type === "progress") {
    $("bar-fill").style.width = event.value + "%";
    if (event.text) $("run-msg").textContent = event.text;
  }
  if (event.type === "done") finish(event);
}

// 统一轮询：请求未返回时跳过本轮，网络异常只提示一次并继续重试
function startPolling(timerKey, interval, run, onError) {
  stopPolling(timerKey);
  const session = { busy: false, warned: false };
  state[timerKey] = setInterval(async () => {
    // 页面不可见时不轮询，回到前台后靠 since 游标一次性补齐
    if (session.busy || document.hidden) return;
    session.busy = true;
    try {
      await run();
      session.warned = false;
    } catch (exc) {
      if (!session.warned) {
        session.warned = true;
        if (onError) onError(exc);
      }
    } finally {
      session.busy = false;
    }
  }, interval);
}

function stopPolling(timerKey) {
  if (state[timerKey]) clearInterval(state[timerKey]);
  state[timerKey] = null;
}

function poll() {
  startPolling(
    "timer",
    350,
    async () => {
      const data = await fetch(`/api/events?since=${state.since}`).then((r) => r.json());
      data.events.forEach(applyEvent);
      const last = data.events[data.events.length - 1];
      if (last) state.since = last.seq;
    },
    () => line("与本地服务失去连接，正在重试…", "err")
  );
}

function stopPoll() {
  stopPolling("timer");
}

function finish(event) {
  stopPoll();
  state.running = false;
  state.result = event.result;
  window.setTimeout(() => showResult(event.result, event.ok), 350);
}

function showResult(result, ok) {
  const verdict = $("verdict");
  verdict.className = "verdict" + (ok ? "" : " bad");
  $("result-title").textContent = ok ? "安装完成" : "安装未完成";
  $("result-desc").textContent = ok
    ? `新开一个终端即可使用 ${result.version || "ROS"}。下面是本次自动完成的配置。`
    : result.reason || "请查看日志定位问题";

  const rows = [];
  if (result.config_name) rows.push(["配置文件", result.config_name]);
  if (result.version) rows.push(["ROS 版本", `${result.version} · ${result.kind === "desktop" ? "桌面版" : "基础版"}`]);
  if (result.package) rows.push(["安装包", result.package]);
  if (result.system_source) rows.push(["系统软件源", result.system_source.label]);
  if (result.sources) rows.push(["ROS 软件源", result.sources.map((item) => item.mirror).join("、")]);
  if (result.extra_packages) rows.push(["额外软件包", result.extra_packages.join("、") + (result.extra_ok ? "" : "（部分失败）")]);
  rows.push(["环境变量", "已写入用户 shell 配置文件"]);
  rows.push(["遥测", "无"]);
  $("result-facts").innerHTML = rows
    .map(([key, value]) => `<div><dt>${key}</dt><dd>${escapeHtml(String(value))}</dd></div>`)
    .join("");

  const commands = $("commands");
  if (ok && result.commands && result.commands.length) {
    commands.innerHTML =
      "<p>新开终端后依次输入</p>" +
      result.commands
        .map(
          (cmd) =>
            `<div class="command"><code>${escapeHtml(cmd)}</code><button type="button" data-copy="${escapeHtml(cmd)}">复制</button></div>`
        )
        .join("");
    commands.querySelectorAll("[data-copy]").forEach((button) => {
      button.addEventListener("click", () => copyText(button.dataset.copy, button));
    });
  } else {
    commands.innerHTML = "";
  }
  show("done");
}

function beginRun() {
  $("log").innerHTML = "";
  $("bar-fill").style.width = "0%";
  $("run-msg").textContent = "正在启动";
  state.steps.forEach((step) => setStep(step.id, ""));
  state.running = true;
  show("run");
}

async function api(path, payload) {
  const options = payload === undefined
    ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) };
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.message || data.error || "请求失败");
  }
  return data;
}

function preview(el, lines, good) {
  el.className = "preview" + (good === null ? "" : good ? " good" : " bad");
  el.textContent = Array.isArray(lines) ? lines.join("\n") : String(lines);
}

// 页内提示，替代 alert()，避免阻塞和风格割裂
function notify(message, kind) {
  const stack = $("toast-stack");
  if (!stack) return;
  const item = document.createElement("div");
  item.className = "toast" + (kind === "bad" ? " bad" : "");
  item.setAttribute("role", "status");
  item.textContent = String(message);
  stack.appendChild(item);
  window.setTimeout(() => item.remove(), 4200);
}

function guard(fn) {
  return async () => {
    if (state.running) {
      notify("安装正在进行，结束后再操作");
      return;
    }
    try {
      await fn();
    } catch (exc) {
      notify(exc.message || exc, "bad");
    }
  };
}

function readFile(file, target) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    target.value = String(reader.result || "");
  };
  reader.readAsText(file, "utf-8");
}

function currentFormConfig() {
  return {
    schema: "felisros/deploy/v1",
    name: "felisros 部署配置",
    version: state.mode === "auto" ? "auto" : $("version").value,
    kind: state.mode === "auto" ? "auto" : $("kind").value,
    mirror: state.mode === "auto" ? "auto" : $("mirror").value,
    change_system_source: $("change-source").checked,
    extra_packages: [],
  };
}

function localPayload(config) {
  return Object.assign({}, config, { sudo_password: $("local-sudo").value });
}

function ensureOption(select, value) {
  if (!value) return;
  const exists = Array.from(select.options).some((option) => option.value === value);
  if (!exists) {
    // auto 是内置语义，不作为自定义配置项追加
    if (value === "auto") return;
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value + "（配置文件）";
    select.appendChild(option);
  }
  select.value = value;
}

async function previewDeploy() {
  const data = await api("/api/config/preview", { text: $("deploy-text").value });
  state.deployConfig = data.config;
  preview($("deploy-info"), data.summary, true);
  return data.config;
}

async function fillForm() {
  const config = state.deployConfig || await previewDeploy();
  state.mode = "custom";
  document.querySelectorAll(".mode").forEach((button) => {
    button.classList.toggle("is-on", button.dataset.mode === "custom");
  });
  $("custom-panel").hidden = false;
  ensureOption($("version"), config.version);
  ensureOption($("kind"), config.kind);
  ensureOption($("mirror"), config.mirror);
  $("change-source").checked = config.change_system_source;
  $("view-config").scrollIntoView({ behavior: "smooth" });
}

async function deployNow() {
  const config = await previewDeploy();
  await api("/api/start", localPayload(config));
  state.since = 0;
  beginRun();
  poll();
}

function exportConfig() {
  const config = currentFormConfig();
  config.name = "felisros 部署配置 - " + config.version;
  const blob = new Blob([JSON.stringify(config, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "felisros-deploy.json";
  link.click();
  URL.revokeObjectURL(link.href);
}

async function versionsPreview() {
  const data = await api("/api/versions/preview", { text: $("versions-text").value });
  preview($("versions-info"), data.summary, true);
}

async function versionsImport() {
  const data = await api("/api/versions/import", { text: $("versions-text").value });
  preview($("versions-info"), [data.message].concat(data.summary), true);
  await loadEnv();
}

async function versionsClear() {
  const data = await api("/api/versions/clear", {});
  preview($("versions-info"), [data.message], true);
  await loadEnv();
}

function fleetLine(text, level) {
  appendLog("fleet-log", text, level);
}

function markFleetDirty() {
  state.fleetDirty = true;
}

function placeholderFor(device, key) {
  const ref = device[key + "_ref"];
  if (ref) return ref;
  return device["has_" + key] ? "已设置（留空保持不变）" : "未设置";
}

function renderDevices(devices) {
  const user = $("fleet-user").value || "root";
  const pass = $("fleet-password").value;
  const sudo = $("fleet-sudo").value || pass;
  state.devices = devices.map((item) =>
    Object.assign(
      {
        alias: "",
        username: user,
        password: pass,
        password_ref: "",
        sudo_password: sudo,
        sudo_password_ref: "",
        has_password: false,
        has_sudo_password: false,
        selected: item.ssh !== false,
        status: "",
        reason: "",
      },
      item
    )
  );
  $("device-body").innerHTML = state.devices
    .map((device, index) => {
      const badge = device.ssh
        ? '<span class="ssh-yes">开放</span>'
        : '<span class="ssh-no">关闭</span>';
      const system = escapeHtml(device.os || "未知");
      return `<tr data-index="${index}">
        <td><input type="checkbox" class="dev-select" ${device.selected ? "checked" : ""}></td>
        <td>${escapeHtml(device.ip)}</td>
        <td class="${device.linux ? "os-linux" : ""}">${system}</td>
        <td>${escapeHtml(device.mac || "—")}</td>
        <td>${escapeHtml(device.hostname || "—")}</td>
        <td>${badge}</td>
        <td><input type="text" class="dev-user" value="${escapeHtml(device.username)}"></td>
        <td><input type="password" class="dev-pass" autocomplete="new-password" placeholder="${escapeHtml(placeholderFor(device, "password"))}"></td>
        <td><input type="password" class="dev-sudo" autocomplete="new-password" placeholder="${escapeHtml(placeholderFor(device, "sudo_password"))}"></td>
        <td class="dev-status">${escapeHtml(device.status || "等待")}</td>
      </tr>`;
    })
    .join("");
  renderFleetDeviceList();
  $("device-body").querySelectorAll("tr").forEach((row) => {
    const index = Number(row.dataset.index);
    const device = state.devices[index];
    row.querySelector(".dev-select").addEventListener("change", (event) => {
      device.selected = event.target.checked;
      renderFleetDeviceList();
      markFleetDirty();
    });
    row.querySelector(".dev-user").addEventListener("input", (event) => {
      device.username = event.target.value;
      markFleetDirty();
    });
    row.querySelector(".dev-pass").addEventListener("input", (event) => {
      if (event.target.value) {
        device.password = event.target.value;
        device.password_ref = "";
        markFleetDirty();
      }
    });
    row.querySelector(".dev-sudo").addEventListener("input", (event) => {
      if (event.target.value) {
        device.sudo_password = event.target.value;
        device.sudo_password_ref = "";
        markFleetDirty();
      }
    });
  });
}

function renderFleetDeviceList() {
  const list = $("fleet-devices");
  if (!list) return;
  // 只列真正参与本次任务的设备；扫描到的其他机器不勾选就不该出现在进度里
  const selected = state.devices.filter((device) => device.selected);
  list.innerHTML = selected.length
    ? selected
        .map(
          (device) =>
            `<li data-ip="${escapeHtml(device.ip)}">${escapeHtml(device.ip)} · ${escapeHtml(device.status || "等待")}</li>`
        )
        .join("")
    : '<li class="empty">尚未勾选设备</li>';
}

function setDeviceStatus(ip, status, reason) {
  state.devices.forEach((device) => {
    if (device.ip !== ip) return;
    device.status = status;
    device.reason = reason || "";
  });
  $("device-body").querySelectorAll("tr").forEach((row) => {
    const device = state.devices[Number(row.dataset.index)];
    if (device.ip === ip) {
      row.querySelector(".dev-status").textContent = status || "等待";
      row.title = device.reason;
    }
  });
  const list = $("fleet-devices");
  if (list) {
    const item = list.querySelector(`[data-ip="${ip}"]`);
    if (item) item.textContent = ip + " · " + (status || "等待") + (reason ? " · " + reason : "");
  }
}

function renderNetworks(networks) {
  const items = networks || [];
  document.querySelectorAll(".net-list").forEach((list) => {
    const existing = list.querySelectorAll('input[type="checkbox"]');
    const checked = new Set(
      Array.from(list.querySelectorAll("input:checked")).map((box) => box.value)
    );
    list.innerHTML = items.length
      ? items
          .map((item) => {
            const cidr = String(item.cidr || "");
            const label = item.name
              ? item.name + " · " + cidr
              : cidr;
            const detail = item.ip ? " (" + item.ip + ")" : "";
            return (
              '<label class="check"><input type="checkbox" value="' +
              escapeHtml(cidr) +
              '"' +
              (existing.length === 0 || checked.has(cidr) ? " checked" : "") +
              '><span>' +
              escapeHtml(label + detail) +
              "</span></label>"
            );
          })
          .join("")
      : '<span class="empty">未检测到网卡，请在下方手动填写网段</span>';
  });
}

function selectedNetworks(inputId, listId) {
  const list = listId || "net-list";
  const field = inputId || "net-input";
  const picked = Array.from(
    document.querySelectorAll("#" + list + " input:checked")
  ).map((box) => box.value.trim());
  const input = $(field);
  const custom = input
    ? input.value.trim().split(/[,，\s]+/).filter(Boolean)
    : [];
  return Array.from(new Set(picked.concat(custom)));
}

async function discoverNetworks({ listId, inputId, linuxOnlyId }) {
  return api("/api/discover", {
    networks: selectedNetworks(inputId, listId),
    linux_only: linuxOnlyId ? $(linuxOnlyId).checked : false,
  });
}

function applyFleetNetwork(network) {
  const list = Array.isArray(network) ? network : network === "auto" || !network ? [] : [network];
  const boxes = Array.from(document.querySelectorAll("#net-list input"));
  const known = boxes.map((box) => box.value);
  boxes.forEach((box) => {
    // 恢复清单里的网段时勾选对应网卡，避免清单网段与界面显示不一致
    box.checked = list.length === 0 ? true : list.indexOf(box.value) >= 0;
  });
  // 网卡已经覆盖的网段不再重复填进「额外扫描网段」
  $("net-input").value = list.filter((item) => known.indexOf(item) < 0).join(", ");
}

// 扫描期间禁用按钮并提示，避免重复点击触发多轮嗅探
async function busyButton(buttonId, busyText, fn) {
  const button = $(buttonId);
  if (!button) return fn();
  const label = button.textContent;
  button.disabled = true;
  button.textContent = busyText;
  try {
    return await fn();
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

async function scanNetwork() {
  await busyButton("net-scan", "扫描中…", async () => {
    const data = await discoverNetworks({
      listId: "net-list",
      inputId: "net-input",
      linuxOnlyId: "linux-only",
    });
    const logs = (data.logs || []).concat("发现 " + data.devices.length + " 台设备");
    $("discover-log").textContent = logs.join("\n");
    renderDevices(data.devices);
    state.fleetFromServer = false;
    state.fleetDirty = true;
  });
}

function renderPresetList(presets) {
  state.presets = presets;
  const list = $("preset-list");
  list.innerHTML = (presets.profiles || []).length
    ? presets.profiles.map((item) => `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)} · ${escapeHtml(item.username)}${item.has_password ? " · 已设密码" : ""}</option>`).join("")
    : '<option value="">（暂无预设）</option>';
  if (presets.default) list.value = presets.default;
  $("preset-name").value = presets.default || "default";
}

async function loadPresets() {
  renderPresetList(await fetch("/api/presets").then((r) => r.json()));
}

function presetPayload() {
  const name = $("preset-name").value.trim() || "default";
  const password = $("fleet-password").value;
  const selected = state.presets.profiles.find((item) => item.name === name);
  return {
    name,
    username: $("fleet-user").value.trim() || "root",
    password,
    default: true,
    keep_password: !password && Boolean(selected && selected.has_password),
  };
}

async function savePreset() {
  const data = await api("/api/presets/save", presetPayload());
  renderPresetList(data.presets);
  $("fleet-summary").textContent = data.message + "：" + data.presets.default;
}

function applyPreset() {
  const user = $("fleet-user").value.trim() || "root";
  const pass = $("fleet-password").value;
  state.devices.forEach((device) => {
    device.username = user;
    if (pass) {
      device.password = pass;
      device.sudo_password = $("fleet-sudo").value || pass;
    }
  });
  renderDevices(state.devices);
  $("fleet-summary").textContent = "已把用户名/密码套用到 " + state.devices.length + " 台设备";
  markFleetDirty();
}

async function deletePreset() {
  const data = await api("/api/presets/delete", { name: $("preset-name").value.trim() || "default" });
  renderPresetList(data.presets);
  $("fleet-summary").textContent = data.message;
}

function selectPreset() {
  const name = $("preset-list").value;
  if (!name) return;
  $("preset-name").value = name;
  const item = state.presets.profiles.find((entry) => entry.name === name);
  if (item) $("fleet-user").value = item.username;
}

async function importFleetText(text) {
  const data = await api("/api/fleet/import", { text });
  $("discover-log").textContent = (data.summary || []).join("\n") + "\n" + (data.message || "");
  if (data.fleet) {
    applyFleetNetwork(data.fleet.network);
    $("fleet-user").value = data.fleet.defaults.username;
    $("fleet-password").value = "";
    $("fleet-sudo").value = "";
    $("fleet-task").value = data.fleet.task || "rollout";
    $("fleet-port").value = data.fleet.port || 8737;
    show("fleet");
    renderDevices(data.fleet.devices);
    if (data.fleet.deploy) {
      $("fleet-deploy-text").value = JSON.stringify(data.fleet.deploy, null, 2);
      $("fleet-summary").textContent = "已随清单导入部署配置";
    }
  }
  state.fleetFromServer = true;
  state.fleetDirty = false;
  state.configName = data.fleet.deploy ? "已随设备清单导入" : "";
  $("fleet-config-name").textContent = state.configName || "尚未选择配置文件";
  renderReports((data.state && data.state.reports) || []);
}

async function importConfigFile(file) {
  if (!file) return;
  const text = await new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.readAsText(file, "utf-8");
  });
  $("fleet-deploy-text").value = text;
  state.configName = file.name;
  $("fleet-config-name").textContent = "已选择: " + file.name;
  await previewDeployConfig();
  markFleetDirty();
}

async function previewDeployConfig() {
  const text = $("fleet-deploy-text").value;
  const data = await api("/api/config/preview", { text });
  preview($("fleet-summary"), data.summary, true);
  return data;
}

function renderReports(reports) {
  state.reports = reports || [];
  const body = $("report-body");
  if (!body) return;
  if (!state.reports.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty">尚未收到回传</td></tr>';
    return;
  }
  body.innerHTML = state.reports
    .map(
      (item) => `<tr>
        <td>${escapeHtml(item.ip)}${item.alias ? " · " + escapeHtml(item.alias) : ""}</td>
        <td>${escapeHtml(item.stage || "完成")}</td>
        <td>${escapeHtml(item.arch || "—")}</td>
        <td>${escapeHtml(item.version || "—")}</td>
        <td>${escapeHtml(String(item.elapsed || 0))} 秒</td>
        <td class="${item.ok ? "ssh-yes" : "ssh-no"}">${item.ok ? "成功" : "失败"} · ${escapeHtml(item.reason || "")}</td>
      </tr>`
    )
    .join("");
}

async function exportReport() {
  const data = await fetch("/api/fleet/report").then((r) => r.json());
  const blob = new Blob(
    [JSON.stringify(data, null, 2)],
    { type: "application/json;charset=utf-8" }
  );
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "felisros-fleet-report.json";
  link.click();
  URL.revokeObjectURL(link.href);
}

function collectFleet() {
  const task = $("fleet-task").value;
  let deploy = null;
  const text = $("fleet-deploy-text").value.trim();
  if (text) {
    try {
      deploy = JSON.parse(text);
    } catch (exc) {
      throw new Error("配置文件不是合法 JSON");
    }
  }
  if (task !== "software" && task !== "update" && !deploy) {
    throw new Error("请先选择或粘贴 ROS 部署配置文件");
  }
  return {
    schema: "felisros/fleet/v1",
    name: "局域网批量部署",
    network: selectedNetworks().length ? selectedNetworks() : "auto",
    task: $("fleet-task").value,
    update_seed: $("fleet-update-seed").value.trim(),
    preset: $("preset-name").value.trim() || "default",
    port: Number($("fleet-port").value) || 8737,
    defaults: {
      username: $("fleet-user").value.trim() || "root",
      password: $("fleet-password").value,
      sudo_password: $("fleet-sudo").value,
    },
    devices: state.devices.map((device) => ({
      ip: device.ip,
      alias: device.alias || device.hostname || "",
      username: device.username,
      password: device.password || device.password_ref,
      sudo_password: device.sudo_password || device.sudo_password_ref,
      selected: device.selected,
    })),
    deploy,
  };
}

function exportFleet() {
  const fleet = collectFleet();
  const blob = new Blob([JSON.stringify(fleet, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "felisros-fleet.json";
  link.click();
  URL.revokeObjectURL(link.href);
}

function applyFleetEvent(event) {
  if (event.type === "fleet_log") {
    fleetLine((event.ip ? "[" + event.ip + "] " : "") + event.text, event.level);
  }
  if (event.type === "fleet_device") {
    setDeviceStatus(event.ip, event.status, event.text);
    const total = state.devices.length || 1;
    const done = state.devices.filter((device) => device.status === "ok" || device.status === "fail").length;
    $("fleet-bar").style.width = Math.round((done / total) * 100) + "%";
  }
  if (event.type === "fleet_report") {
    const next = state.reports.filter((item) => item.ip !== event.report.ip);
    next.push(event.report);
    renderReports(next);
  }
  if (event.type === "fleet_done") {
    stopFleetPoll();
    state.fleetRunning = false;
    $("fleet-bar").style.width = "100%";
    if (event.reports) renderReports(event.reports);
    fleetLine(
      "批量任务结束：成功 " + event.ok_count + " 台，失败 " + event.fail_count + " 台",
      event.ok ? "ok" : "warn"
    );
  }
}

function stopFleetPoll() {
  stopPolling("fleetTimer");
}

function pollFleet() {
  startPolling(
    "fleetTimer",
    400,
    async () => {
      const data = await fetch(`/api/fleet/events?since=${state.fleetSince}`).then((r) => r.json());
      data.events.forEach(applyFleetEvent);
      const last = data.events[data.events.length - 1];
      if (last) state.fleetSince = last.seq;
      (data.state.devices || []).forEach((device) => {
        if (device.status && device.status !== "pending") {
          setDeviceStatus(device.ip, device.status, device.reason);
        }
      });
      if (data.state.reports) renderReports(data.state.reports);
    },
    () => fleetLine("与本地服务失去连接，正在重试…", "err")
  );
}

async function startFleet() {
  const payload = {
    workers: Number($("fleet-workers").value),
    task: $("fleet-task").value,
    port: Number($("fleet-port").value) || 8737,
    update_seed: $("fleet-update-seed").value.trim(),
    preset: $("preset-name").value.trim() || "default",
  };
  if (!state.fleetFromServer || state.fleetDirty) {
    payload.fleet = collectFleet();
  }
  const data = await api("/api/fleet/start", payload);
  $("fleet-log").innerHTML = "";
  $("fleet-bar").style.width = "0%";
  state.fleetSince = 0;
  state.fleetRunning = true;
  renderReports([]);
  state.devices.forEach((device) => setDeviceStatus(device.ip, "pending", ""));
  fleetLine(data.message || "批量部署已开始", "info");
  pollFleet();
}

async function quickStart() {
  if (!state.devices.length) {
    $("quick-hint").textContent = "正在扫描网段…";
    await scanNetwork();
  }
  const selected = state.devices.filter((device) => device.selected);
  if (!selected.length) throw new Error("请先在设备列表勾选要部署的 Linux 设备");

  const username = $("quick-user").value.trim() || "root";
  const password = $("quick-password").value;
  const sudo = $("quick-sudo").value;
  $("fleet-user").value = username;
  $("fleet-password").value = password;
  $("fleet-sudo").value = sudo;
  $("fleet-task").value = "rollout";

  const deploy = {
    schema: "felisros/deploy/v1",
    name: "felisros 快速部署",
    version: $("quick-version").value || "auto",
    kind: $("quick-kind").value,
    mirror: "auto",
    change_system_source: true,
    extra_packages: [],
  };
  $("fleet-deploy-text").value = JSON.stringify(deploy, null, 2);
  const fleet = {
    schema: "felisros/fleet/v1",
    name: "快速批量部署",
    network: selectedNetworks().length ? selectedNetworks() : "auto",
    task: "rollout",
    update_seed: "",
    port: 8737,
    preset: "",
    defaults: { username, password, sudo_password: sudo },
    devices: selected.map((device) => ({
      ip: device.ip,
      alias: device.alias || device.hostname || "",
      username: device.username || username,
      password: device.password || device.password_ref || password,
      sudo_password: device.sudo_password || device.sudo_password_ref || sudo,
      selected: true,
    })),
    deploy,
  };

  $("quick-hint").textContent = "正在下发任务到 " + selected.length + " 台设备…";
  const data = await api("/api/fleet/start", {
    workers: Number($("fleet-workers").value),
    task: "rollout",
    port: 8737,
    update_seed: "",
    preset: "",
    fleet,
  });
  $("fleet-log").innerHTML = "";
  $("fleet-bar").style.width = "0%";
  state.fleetSince = 0;
  state.fleetRunning = true;
  renderReports([]);
  state.devices.forEach((device) => setDeviceStatus(device.ip, "pending", ""));
  fleetLine(data.message || "批量部署已开始", "info");
  pollFleet();
  $("quick-hint").textContent = "任务已下发，进展见下方日志。";
}

async function startInstall() {
  try {
    await api("/api/start", localPayload(currentFormConfig()));
  } catch (exc) {
    notify(exc.message || exc, "bad");
    return;
  }
  state.since = 0;
  beginRun();
  poll();
}

function parseDeviceLines(text) {
  return String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const parts = line.split(",").map((item) => item.trim());
      return {
        ip: parts[0],
        username: parts[1] || "",
        password: parts[2] || "",
        sudo_password: parts[3] || "",
        selected: true,
      };
    });
}

function fillDevicesFromScan() {
  const fallback = $("gen-user").value.trim() || "root";
  const lines = state.devices
    .filter((device) => device.selected)
    .map((device) => {
      const parts = [
        device.ip,
        device.username || fallback,
        device.password || "",
        device.sudo_password || "",
      ];
      while (parts.length > 1 && !parts[parts.length - 1]) parts.pop();
      return parts.join(",");
    });
  $("gen-devices").value = lines.join("\n");
}

function resetGenerated() {
  state.generated = null;
  $("gen-preview").textContent = "尚未生成配置";
  $("gen-preview").className = "preview";
  $("gen-json").textContent = "";
  $("gen-json").hidden = true;
  ["gen-download", "gen-copy", "gen-apply"].forEach((id) => ($(id).disabled = true));
}

async function buildGenerated() {
  const remote = $("gen-remote").checked;
  const payload = {
    remote,
    name: $("gen-name").value.trim(),
    version: $("gen-version").value,
    kind: $("gen-kind").value,
    mirror: $("gen-mirror").value,
    change_system_source: $("gen-change-source").checked,
    extra_packages: $("gen-extras").value,
  };
  if (remote) {
    payload.task = $("gen-task").value;
    payload.update_seed = $("gen-update-seed").value.trim();
    payload.port = Number($("gen-port").value) || 8737;
    payload.network = $("gen-network").value.trim() || "auto";
    payload.defaults = {
      username: $("gen-user").value.trim() || "root",
      password: $("gen-password").value,
      sudo_password: $("gen-sudo").value,
    };
    payload.workers = Number($("gen-workers").value);
    payload.devices = parseDeviceLines($("gen-devices").value);
  }
  const data = await api("/api/config/build", payload);
  state.generated = data;
  const lines = data.summary.slice();
  if (remote && !payload.devices.length) lines.push("注意：没有填写目标设备，清单中不含设备");
  preview($("gen-preview"), lines, true);
  $("gen-json").textContent = data.text;
  $("gen-json").hidden = false;
  $("gen-download").disabled = false;
  $("gen-copy").disabled = false;
  $("gen-apply").disabled = !remote;
  $("gen-copy").textContent = "复制 JSON";
}

function downloadGenerated() {
  if (!state.generated) return;
  const blob = new Blob([state.generated.text], {
    type: "application/json;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = state.generated.filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function copyGenerated() {
  if (!state.generated) return;
  await copyText(state.generated.text, $("gen-copy"));
}

async function applyGenerated() {
  if (!state.generated || state.generated.schema !== "felisros/fleet/v1") return;
  const text = state.generated.text;
  await importFleetText(text);
  try {
    const raw = JSON.parse(text);
    if (raw.workers) $("fleet-workers").value = String(raw.workers);
  } catch (exc) {}
  $("fleet-summary").textContent = "已从配置生成器载入，可直接批量部署";
  show("fleet");
}

function distLine(text, level) {
  appendLog("dist-log", text, level);
}

function distPlaceholder(device, key) {
  const ref = device[key + "_ref"];
  if (ref) return ref;
  if (device["has_" + key]) return "已设置（留空保持不变）";
  const unified =
    key === "sudo_password"
      ? $("dist-sudo").value || $("dist-password").value
      : $("dist-password").value;
  return unified ? "使用统一登录信息" : "未设置";
}

function refreshDistPlaceholders() {
  $("dist-device-body")
    .querySelectorAll("tr")
    .forEach((row) => {
      const device = state.distDevices[Number(row.dataset.index)];
      if (!device) return;
      row.querySelector(".dist-pass").placeholder = distPlaceholder(device, "password");
      row.querySelector(".dist-sudo").placeholder = distPlaceholder(device, "sudo_password");
    });
}

function renderDistDevices(devices) {
  state.distDevices = (devices || []).map((item) =>
    Object.assign(
      {
        alias: "",
        username: "",
        password: "",
        password_ref: "",
        sudo_password: "",
        sudo_password_ref: "",
        has_password: false,
        has_sudo_password: false,
        selected: item.ssh !== false,
        status: "",
        reason: "",
      },
      item
    )
  );
  if (!state.distDevices.length) {
    $("dist-device-body").innerHTML = '<tr><td colspan="7" class="empty">尚未发现设备</td></tr>';
    return;
  }
  $("dist-device-body").innerHTML = state.distDevices
    .map((device, index) => {
      const system = escapeHtml(device.os || "未知");
      return `<tr data-index="${index}">
        <td><input type="checkbox" class="dist-select" ${device.selected ? "checked" : ""}></td>
        <td>${escapeHtml(device.ip)}</td>
        <td>${system}</td>
        <td><input type="text" class="dist-user" value="${escapeHtml(device.username)}"></td>
        <td><input type="password" class="dist-pass" autocomplete="new-password" placeholder="${escapeHtml(distPlaceholder(device, "password"))}"></td>
        <td><input type="password" class="dist-sudo" autocomplete="new-password" placeholder="${escapeHtml(distPlaceholder(device, "sudo_password"))}"></td>
        <td class="dist-status">${escapeHtml(device.status || "等待")}</td>
      </tr>`;
    })
    .join("");
  $("dist-device-body").querySelectorAll("tr").forEach((row) => {
    const device = state.distDevices[Number(row.dataset.index)];
    row.querySelector(".dist-select").addEventListener("change", (event) => {
      device.selected = event.target.checked;
    });
    row.querySelector(".dist-user").addEventListener("input", (event) => {
      device.username = event.target.value;
    });
    row.querySelector(".dist-pass").addEventListener("input", (event) => {
      if (event.target.value) {
        device.password = event.target.value;
        device.password_ref = "";
      }
    });
    row.querySelector(".dist-sudo").addEventListener("input", (event) => {
      if (event.target.value) {
        device.sudo_password = event.target.value;
        device.sudo_password_ref = "";
      }
    });
  });
}

function setDistDeviceStatus(ip, status, reason) {
  state.distDevices.forEach((device) => {
    if (device.ip !== ip) return;
    device.status = status;
    device.reason = reason || "";
  });
  $("dist-device-body").querySelectorAll("tr").forEach((row) => {
    const device = state.distDevices[Number(row.dataset.index)];
    if (device.ip === ip) {
      row.querySelector(".dist-status").textContent = status || "等待";
      row.title = device.reason;
    }
  });
}

async function distScan() {
  await busyButton("dist-scan", "扫描中…", async () => {
    const data = await discoverNetworks({
      listId: "dist-net-list",
      inputId: "dist-net-input",
      linuxOnlyId: "dist-linux-only",
    });
    const logs = (data.logs || []).concat("发现 " + data.devices.length + " 台设备");
    $("dist-discover-log").textContent = logs.join("\n");
    renderDistDevices(data.devices);
  });
}

function renderDistReports(reports) {
  state.distReports = reports || [];
  const body = $("dist-report-body");
  if (!body) return;
  if (!state.distReports.length) {
    body.innerHTML = '<tr><td colspan="4" class="empty">尚未收到回传</td></tr>';
    return;
  }
  body.innerHTML = state.distReports
    .map(
      (item) => `<tr>
        <td>${escapeHtml(item.ip)}${item.alias ? " · " + escapeHtml(item.alias) : ""}</td>
        <td>${escapeHtml(item.arch || "—")}</td>
        <td>${escapeHtml(String(item.elapsed || 0))} 秒</td>
        <td class="${item.ok ? "ssh-yes" : "ssh-no"}">${item.ok ? "成功" : "失败"} · ${escapeHtml(item.reason || "")}</td>
      </tr>`
    )
    .join("");
}

function applyDistEvent(event) {
  if (event.type === "fleet_log") {
    distLine((event.ip ? "[" + event.ip + "] " : "") + event.text, event.level);
  }
  if (event.type === "fleet_device") {
    setDistDeviceStatus(event.ip, event.status, event.text);
    const total = state.distDevices.length || 1;
    const done = state.distDevices.filter((device) => device.status === "ok" || device.status === "fail").length;
    $("dist-bar").style.width = Math.round((done / total) * 100) + "%";
  }
  if (event.type === "fleet_report") {
    const next = state.distReports.filter((item) => item.ip !== event.report.ip);
    next.push(event.report);
    renderDistReports(next);
  }
  if (event.type === "fleet_done") {
    stopDistPoll();
    state.distRunning = false;
    $("dist-bar").style.width = "100%";
    if (event.reports) renderDistReports(event.reports);
    distLine(
      "批量分发结束：成功 " + event.ok_count + " 台，失败 " + event.fail_count + " 台",
      event.ok ? "ok" : "warn"
    );
  }
}

function stopDistPoll() {
  stopPolling("distTimer");
}

function pollDistFleet() {
  startPolling(
    "distTimer",
    400,
    async () => {
      const data = await fetch(`/api/fleet/events?since=${state.distSince}`).then((r) => r.json());
      data.events.forEach(applyDistEvent);
      const last = data.events[data.events.length - 1];
      if (last) state.distSince = last.seq;
      (data.state.devices || []).forEach((device) => {
        if (device.status && device.status !== "pending") {
          setDistDeviceStatus(device.ip, device.status, device.reason);
        }
      });
      if (data.state.reports) renderDistReports(data.state.reports);
    },
    () => distLine("与本地服务失去连接，正在重试…", "err")
  );
}

async function startDistDeploy() {
  if (state.distRunning) throw new Error("批量分发正在进行，结束后再操作");
  const selected = state.distDevices.filter((device) => device.selected);
  if (!selected.length) throw new Error("请先扫描并勾选要分发的 Linux 设备");
  const port = Number($("dist-port").value) || 8737;
  const fleet = {
    schema: "felisros/fleet/v1",
    name: "局域网主动批量分发",
    network: $("dist-net-input").value.trim() || "auto",
    task: "software",
    port,
    preset: "",
    defaults: {
      username: $("dist-user").value.trim() || "root",
      password: $("dist-password").value,
      sudo_password: $("dist-sudo").value,
    },
    devices: selected.map((device) => ({
      ip: device.ip,
      alias: device.alias || device.hostname || "",
      username: device.username,
      password: device.password || device.password_ref,
      sudo_password: device.sudo_password || device.sudo_password_ref,
      selected: true,
    })),
  };
  const data = await api("/api/fleet/start", {
    workers: Number($("dist-workers").value),
    task: "software",
    port,
    update_seed: "",
    preset: "",
    fleet,
  });
  $("dist-log").innerHTML = "";
  $("dist-bar").style.width = "0%";
  state.distSince = 0;
  state.distRunning = true;
  renderDistReports([]);
  state.distDevices.forEach((device) => setDistDeviceStatus(device.ip, "pending", ""));
  distLine(data.message || "批量分发已开始", "info");
  pollDistFleet();
}

function updateLine(text, level) {
  appendLog("update-log", text, level, 300);
}

function renderRole(caps) {
  const control = caps.role === "control";
  $("update-role").textContent = control
    ? "Windows 控制端：不安装、不更新自身；可以批量给 Linux 设备分发更新，本机仍可作为更新源。"
    : "Linux 完整端：本机安装、设备互更新、批量部署全部可用。";
  $("update-role").className = "role-banner" + (control ? " control" : "");
  state.role = caps.role;
  $("start").disabled = control;
  $("control-note").hidden = !control;
  ["update-apply", "update-auto", "update-save"].forEach((id) => {
    $(id).disabled = control;
    $(id).title = control ? "Windows 是控制端，不允许更新自身" : "";
  });
}

function renderPeers(peers) {
  state.peers = peers || [];
  if (!state.peers.length) {
    $("peer-body").innerHTML = '<tr><td colspan="5" class="empty">尚未发现设备，广播信标每 3 秒发送一次</td></tr>';
    return;
  }
  $("peer-body").innerHTML = state.peers
    .map((peer) => {
      const newer = compareVersions(peer.version, state.version) > 0;
      const version = escapeHtml(String(peer.version || "-"));
      const button = state.role === "control"
        ? '<button type="button" class="ghost peer-apply" disabled title="当前是 Windows 控制端，不能更新自身；请到 Linux 设备上操作">本机不能自更新</button>'
        : '<button type="button" class="ghost peer-apply" data-seed="' + escapeHtml(peer.base_url || ("http://" + peer.ip + ":" + (peer.port || 8737))) + '">从这台更新</button>';
      return "<tr><td>" + escapeHtml(peer.ip || "") + '</td><td class="' + (newer ? "peer-new" : "") + '">' + version +
        "</td><td>" + escapeHtml(peer.role || peer.capabilities && peer.capabilities.role || "-") + "</td><td>" +
        escapeHtml(peer.source || "beacon") + " · " + escapeHtml(String(peer.age === undefined ? "-" : peer.age + "s")) +
        "</td><td>" + button + "</td></tr>";
    })
    .join("");
  $("peer-body").querySelectorAll(".peer-apply").forEach((button) => {
    button.addEventListener("click", () => {
      $("update-seed").value = button.dataset.seed;
      applyUpdate();
    });
  });
}

function compareVersions(a, b) {
  const left = String(a || "0").split(".").map((v) => parseInt(v, 10) || 0);
  const right = String(b || "0").split(".").map((v) => parseInt(v, 10) || 0);
  const size = Math.max(left.length, right.length);
  for (let i = 0; i < size; i++) {
    const d = (left[i] || 0) - (right[i] || 0);
    if (d) return d > 0 ? 1 : -1;
  }
  return 0;
}

async function loadPeers() {
  const info = await fetch("/api/peers").then((r) => r.json());
  state.version = info.version || state.version;
  renderRole(info.capabilities || { role: info.role });
  renderPeers(info.peers || []);
  $("update-local").textContent = "本机版本: " + state.version + " · 角色: " +
    (info.role === "control" ? "Windows 控制端" : "Linux 完整端");
  if (info.auto && info.auto.settings) {
    $("update-auto").checked = !!info.auto.settings.enabled;
    $("update-interval").value = info.auto.settings.interval || 300;
    if (info.auto.settings.seed) $("update-seed").value = info.auto.settings.seed;
  }
}

async function checkUpdate() {
  $("update-status").textContent = "正在检查更新源…";
  const data = await api("/api/update/check", { seed: $("update-seed").value.trim() });
  const lines = [
    data.message || "检查完成",
    "当前版本: " + data.version + " · 检查源: " + data.checked,
  ];
  if (data.available) {
    lines.push("最新版本: " + data.version + " · 来自 " + data.seed);
    if (data.can_apply === false) lines.push("当前是控制端，不能更新自身");
  }
  if (data.errors && data.errors.length) lines.push("无法访问: " + data.errors.join("; "));
  $("update-status").textContent = lines.join("\n");
  updateLine(lines[0], data.available ? "ok" : "info");
}

async function applyUpdate() {
  const seed = $("update-seed").value.trim();
  if (!seed) throw new Error("请先填写更新源地址，或从设备列表中选择一台");
  $("update-status").textContent = "正在从 " + seed + " 拉取更新…";
  const data = await api("/api/update/apply", { seed, restart: true });
  $("update-status").textContent = data.message || data.reason || "更新结束";
  updateLine($("update-status").textContent, "ok");
  startUpdatePoll();
}

function stopUpdatePoll() {
  stopPolling("updateTimer");
}

function startUpdatePoll() {
  startPolling(
    "updateTimer",
    1500,
    async () => {
      const data = await fetch("/api/update/events?since=" + state.updateSince).then((r) => r.json());
      data.events.forEach((event) => updateLine(event.text, event.level));
      const last = data.events[data.events.length - 1];
      if (last) state.updateSince = last.seq;
      if (state.version !== data.version) {
        state.version = data.version;
        loadPeers();
      }
    },
    () => updateLine("与本地服务失去连接，正在重试…", "err")
  );
}

async function probePeers() {
  const networks = selectedNetworks("update-network", "update-net-list");
  updateLine(
    "扫描 " + (networks.length ? networks.join(", ") : "本机全部网卡") + " 中的 felisros 设备",
    "info"
  );
  const found = await discoverNetworks({
    listId: "update-net-list",
    inputId: "update-network",
  });
  const ips = (found.devices || []).map((device) => device.ip);
  const result = await api("/api/peers/probe", { ips, port: 8737 });
  const peers = result.peers || [];
  renderPeers(peers);
  $("update-status").textContent = "探测 " + ips.length + " 台设备，发现 " + peers.length + " 个更新源\n" + (found.logs || []).join("\n");
}

async function saveAutoSettings() {
  const data = await api("/api/update/settings", {
    enabled: $("update-auto").checked,
    interval: Number($("update-interval").value) || 300,
    seed: $("update-seed").value.trim(),
  });
  $("update-status").textContent = data.settings.enabled
    ? "自动更新已开启，每 " + data.settings.interval + " 秒检查一次"
    : "自动更新已关闭";
  updateLine($("update-status").textContent, "ok");
}

async function loadLan() {
  const info = await fetch("/api/lan").then((r) => r.json());
  state.lan = info;
  $("dist-command").textContent = info.package_exists
    ? info.command
    : "安装包暂不可用，请检查自动打包结果";
  $("dist-lan-note").textContent = info.lan_mode
    ? "服务已监听局域网，其他设备可直接访问并自由下载安装。"
    : "当前只监听本机。要让局域网用户访问，请用 python app.py --host 0.0.0.0 重启。";
  $("dist-package-note").textContent = info.package_exists
    ? "安装包已就绪，可直接下载，也可让对方用上面的命令自动安装。"
    : "安装包暂不可用，服务会在需要时自动尝试打包。";
  $("dist-sha").textContent = info.checksum || "无";
  $("dist-download").style.opacity = info.package_exists ? "1" : "0.5";
}

// 剪贴板在 http://<局域网IP> 下属于不安全上下文，navigator.clipboard 不可用，
// 必须先判断 isSecureContext，再退回 execCommand，否则复制会静默失败。
function legacyCopy(text) {
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.top = "-1000px";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  area.setSelectionRange(0, area.value.length);
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch (exc) {
    ok = false;
  }
  area.remove();
  return ok;
}

async function copyText(text, button) {
  let ok = false;
  if (window.isSecureContext && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      ok = true;
    } catch (exc) {
      ok = false;
    }
  }
  if (!ok) ok = legacyCopy(text);
  if (!ok) {
    notify("复制失败，请手动选中文本复制", "bad");
    return;
  }
  if (!button) return;
  const previous = button.textContent;
  button.textContent = "已复制";
  window.setTimeout(() => (button.textContent = previous), 1500);
}

function bind() {
  document.querySelectorAll(".mode").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".mode").forEach((item) => item.classList.remove("is-on"));
      button.classList.add("is-on");
      state.mode = button.dataset.mode;
      $("custom-panel").hidden = state.mode !== "custom";
    });
  });
  $("start").addEventListener("click", guard(startInstall));
  $("cancel").addEventListener("click", async () => {
    await fetch("/api/cancel", { method: "POST" });
    $("cancel").disabled = true;
  });
  $("restart").addEventListener("click", async () => {
    show("config");
    const current = await fetch("/api/state").then((r) => r.json());
    if (current.running) beginRun(), poll();
  });
  $("show-log").addEventListener("click", () => show("run"));

  $("deploy-file").addEventListener("change", (event) => readFile(event.target.files[0], $("deploy-text")));
  $("versions-file").addEventListener("change", (event) => readFile(event.target.files[0], $("versions-text")));
  $("deploy-preview").addEventListener("click", guard(previewDeploy));
  $("deploy-fill").addEventListener("click", guard(fillForm));
  $("deploy-now").addEventListener("click", guard(deployNow));
  $("deploy-export").addEventListener("click", exportConfig);
  $("versions-preview").addEventListener("click", guard(versionsPreview));
  $("versions-import").addEventListener("click", guard(versionsImport));
  $("versions-clear").addEventListener("click", guard(versionsClear));

  $("tab-single").addEventListener("click", () => show("config"));
  $("tab-fleet").addEventListener("click", () => show("fleet"));
  $("tab-generator").addEventListener("click", () => show("generator"));
  $("tab-distribute").addEventListener("click", () => show("distribute"));
  $("tab-update").addEventListener("click", () => {
    show("update");
    loadPeers();
    startUpdatePoll();
  });
  $("update-check").addEventListener("click", guard(checkUpdate));
  $("update-apply").addEventListener("click", guard(applyUpdate));
  $("update-scan").addEventListener("click", guard(probePeers));
  $("update-save").addEventListener("click", guard(saveAutoSettings));
  $("update-copy-seed").addEventListener("click", guard(async () => {
    if (state.lan) await copyText(state.lan.url, $("update-copy-seed"));
  }));
  $("fleet-task").addEventListener("change", () => {
    markFleetDirty();
    show(state.view);
  });
  ["fleet-port", "fleet-update-seed"].forEach((id) => $(id).addEventListener("input", markFleetDirty));
  $("dist-copy-command").addEventListener("click", guard(async () => {
    if (state.lan) await copyText(state.lan.command, $("dist-copy-command"));
  }));
  $("dist-copy-url").addEventListener("click", guard(async () => {
    if (state.lan) await copyText(state.lan.url, $("dist-copy-url"));
  }));
  $("dist-copy-sha").addEventListener("click", guard(async () => {
    if (state.lan) await copyText(state.lan.checksum || "", $("dist-copy-sha"));
  }));
  $("dist-scan").addEventListener("click", guard(distScan));
  $("dist-start").addEventListener("click", guard(startDistDeploy));
  ["dist-user", "dist-password", "dist-sudo"].forEach((id) =>
    $(id).addEventListener("input", refreshDistPlaceholders)
  );
  $("dist-cancel").addEventListener("click", async () => {
    await api("/api/fleet/cancel", {});
  });
  $("dev-all").addEventListener("click", () => {
    const all = state.devices.length > 0 && state.devices.every((device) => device.selected);
    state.devices.forEach((device) => {
      device.selected = !all;
    });
    document.querySelectorAll("#device-body .dev-select").forEach((box, index) => {
      box.checked = state.devices[index].selected;
    });
    renderFleetDeviceList();
    markFleetDirty();
  });
  $("dist-all").addEventListener("click", () => {
    const all = state.distDevices.length > 0 && state.distDevices.every((device) => device.selected);
    state.distDevices.forEach((device) => {
      device.selected = !all;
    });
    document.querySelectorAll("#dist-device-body .dist-select").forEach((box, index) => {
      box.checked = state.distDevices[index].selected;
    });
  });
  $("dist-report-export").addEventListener("click", guard(exportReport));

  $("gen-remote").addEventListener("change", () => {
    const on = $("gen-remote").checked;
    $("gen-remote-panel").hidden = !on;
    $("gen-mode-note").textContent = on
      ? "当前生成：远程批量部署清单（felisros/fleet/v1），内含安装配置与设备凭据。"
      : "当前生成：单机部署配置（felisros/deploy/v1）。";
    resetGenerated();
  });
  $("gen-fill-devices").addEventListener("click", fillDevicesFromScan);
  $("gen-build").addEventListener("click", guard(buildGenerated));
  $("gen-download").addEventListener("click", downloadGenerated);
  $("gen-copy").addEventListener("click", guard(copyGenerated));
  $("gen-apply").addEventListener("click", guard(applyGenerated));
  [
    "gen-name", "gen-version", "gen-kind", "gen-mirror", "gen-change-source",
    "gen-extras", "gen-network", "gen-user", "gen-password", "gen-sudo",
    "gen-workers", "gen-devices", "gen-update-seed",
  ].forEach((id) => $(id).addEventListener("input", resetGenerated));
  $("net-scan").addEventListener("click", guard(scanNetwork));
  $("preset-save").addEventListener("click", guard(savePreset));
  $("preset-apply").addEventListener("click", applyPreset);
  $("preset-delete").addEventListener("click", guard(deletePreset));
  $("preset-list").addEventListener("change", selectPreset);
  $("fleet-file").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    const text = await new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.readAsText(file, "utf-8");
    });
    try {
      await importFleetText(text);
    } catch (exc) {
      notify(exc.message || exc, "bad");
    }
  });
  $("fleet-export").addEventListener("click", guard(exportFleet));
  $("fleet-clear").addEventListener("click", guard(async () => {
    await api("/api/fleet/clear", {});
    renderDevices([]);
    state.fleetFromServer = false;
    state.fleetDirty = false;
    $("discover-log").textContent = "已清除设备清单";
    $("fleet-summary").textContent = "尚未设置";
  }));
  $("fleet-config-import").addEventListener("click", () => $("fleet-config-file").click());
  $("fleet-config-repick").addEventListener("click", () => $("fleet-config-file").click());
  $("fleet-config-file").addEventListener("change", async (event) => {
    try {
      await importConfigFile(event.target.files[0]);
    } catch (exc) {
      notify(exc.message || exc, "bad");
    }
    event.target.value = "";
  });
  $("fleet-config-preview").addEventListener("click", guard(previewDeployConfig));
  $("report-export").addEventListener("click", guard(exportReport));
  $("fleet-use-current").addEventListener("click", () => {
    $("fleet-deploy-text").value = JSON.stringify(currentFormConfig(), null, 2);
    state.configName = "当前单机配置";
    $("fleet-config-name").textContent = "已填入: 当前单机配置";
    $("fleet-summary").textContent = "已复制当前单机配置";
    markFleetDirty();
  });
  $("quick-start").addEventListener("click", guard(quickStart));
  $("fleet-start").addEventListener("click", guard(startFleet));
  $("fleet-cancel").addEventListener("click", async () => {
    await api("/api/fleet/cancel", {});
  });
  ["fleet-user", "fleet-password", "fleet-sudo", "fleet-workers", "fleet-deploy-text", "net-input"].forEach(
    (id) => $(id).addEventListener("input", markFleetDirty)
  );
  $("net-list").addEventListener("change", markFleetDirty);
}

async function loadEnv() {
  const env = await fetch("/api/env").then((r) => r.json());
  state.env = env;
  state.version = env.version || state.version;
  renderFacts(env);
  renderSudoHint(env);
  renderOptions(env);
  renderSteps(env.steps);
  const info = await fetch("/api/config").then((r) => r.json());
  state.template = info.deploy;
  if (!$("gen-name").value) $("gen-name").value = state.template ? state.template.name : "";
  preview($("versions-info"), info.versions || "未导入版本清单", Boolean(info.versions));
}

function restoreFleet(data) {
  const fleet = data.fleet;
  if (!fleet) return;
  applyFleetNetwork(fleet.network);
  $("fleet-user").value = fleet.defaults.username;
  $("fleet-password").value = "";
  $("fleet-sudo").value = "";
  $("fleet-task").value = fleet.task || "rollout";
  $("fleet-port").value = fleet.port || 8737;
  $("fleet-update-seed").value = fleet.update_seed || "";
  if (fleet.preset) $("preset-name").value = fleet.preset;
  show(state.view);
  renderDevices(fleet.devices);
  if (fleet.deploy) $("fleet-deploy-text").value = JSON.stringify(fleet.deploy, null, 2);
  $("discover-log").textContent = (data.summary || []).join("\n");
  $("fleet-summary").textContent = "已载入保存的设备清单";
  state.fleetFromServer = true;
  state.fleetDirty = false;
  (data.state.devices || []).forEach((device) => {
    if (device.status && device.status !== "pending") {
      setDeviceStatus(device.ip, device.status, device.reason);
    }
  });
}

async function boot() {
  try {
    await loadEnv();
    bind();
    const net = await fetch("/api/network").then((r) => r.json());
    renderNetworks(net.networks || []);
    await loadLan();
    await loadPeers();
    await loadPresets();
    const fleetInfo = await fetch("/api/fleet").then((r) => r.json());
    restoreFleet(fleetInfo);
    show("config");
    $("fleet-deploy-text").value = JSON.stringify(
      fleetInfo.fleet && fleetInfo.fleet.deploy ? fleetInfo.fleet.deploy : state.template,
      null,
      2
    );
    const current = await fetch("/api/state").then((r) => r.json());
    if (current.running) {
      state.since = 0;
      beginRun();
      poll();
    } else if (current.finished) {
      showResult(current.result || {}, current.ok);
    }
  } catch (exc) {
    $("facts").innerHTML = `<div><dt>错误</dt><dd>无法连接本地服务</dd></div>`;
  }
}

boot();
