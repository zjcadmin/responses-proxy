const root = document.getElementById("manager-root");
const toastRoot = document.getElementById("toast-root");
const modalRoot = document.getElementById("modal-root");

const POLL_INTERVAL_MS = 5000;
const CLOCK_INTERVAL_MS = 1000;
const LOG_LINE_LIMIT = 160;

const state = {
  authenticated: false,
  sessionChecked: false,
  loginError: "",
  modalOpen: false,
  editingPreset: null,
  status: null,
  presets: [],
  settings: null,
  settingsForm: { proxy_api_key: "" },
  settingsDirty: false,
  logs: { events: [], stdout: [], stderr: [] },
  pollingHandle: null,
  clockHandle: null,
  activeLogTab: "events",
  activePreviewTab: "env",
  currentSection: "overview",
  dashboardMounted: false,
  busyAction: "",
  nowLabel: "",
};

function nowString() {
  const value = new Date();
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  const hours = String(value.getHours()).padStart(2, "0");
  const minutes = String(value.getMinutes()).padStart(2, "0");
  const seconds = String(value.getSeconds()).padStart(2, "0");
  return `${year}/${month}/${day} ${hours}:${minutes}:${seconds}`;
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    credentials: "same-origin",
    ...options,
  });

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();

  if (!response.ok) {
    const message =
      payload?.detail ||
      payload?.error?.message ||
      payload?.message ||
      `请求失败（${response.status}）`;
    throw new Error(message);
  }

  return payload;
}

function showToast(message, type = "info") {
  if (!toastRoot) return;
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  toastRoot.appendChild(toast);
  window.setTimeout(() => toast.remove(), 3200);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function prettyJson(value) {
  if (!value || (typeof value === "object" && Object.keys(value).length === 0)) {
    return "{}";
  }
  return JSON.stringify(value, null, 2);
}

function logLines(key) {
  const lines = state.logs?.[key];
  if (!Array.isArray(lines) || !lines.length) {
    return "暂无日志";
  }
  return lines.slice(-LOG_LINE_LIMIT).join("\n");
}

function getProxy() {
  return state.status?.proxy || null;
}

function getManager() {
  return state.status?.manager || null;
}

function getActivePreset() {
  return (
    state.presets.find((preset) => preset.id === state.activePresetId) ||
    state.status?.active_preset ||
    null
  );
}

function proxyStatusLabel(proxy) {
  if (!proxy) return "未启动";
  if (proxy.running) return "系统运行中";
  if (proxy.state === "error") return "启动失败";
  if (proxy.state === "starting") return "启动中";
  return "已停止";
}

function proxyStatusTone(proxy) {
  if (!proxy) return "muted";
  if (proxy.running) return "success";
  if (proxy.state === "error") return "danger";
  if (proxy.state === "starting") return "warning";
  return "muted";
}

function currentBaseUrl() {
  return getProxy()?.base_url || "尚未启动代理";
}

function isBusy(actionName = "") {
  if (!state.busyAction) {
    return false;
  }
  return actionName ? state.busyAction === actionName : true;
}

function buttonDisabled(condition) {
  return condition ? "disabled" : "";
}

function setRegion(id, html) {
  const node = document.getElementById(id);
  if (node) {
    node.innerHTML = html;
  }
}

function navItems() {
  return [
    { id: "overview", label: "控制大屏", icon: "📊", note: "当前状态与快速操作" },
    { id: "presets", label: "模型预设", icon: "🧩", note: "Base URL / 模型 / Key / 端口" },
    { id: "settings", label: "环境配置", icon: "🔐", note: "管理 .env 与代理密钥" },
    { id: "preview", label: "同步预览", icon: "🗂", note: "查看将要写入的配置文件" },
    { id: "logs", label: "运行日志", icon: "📝", note: "排查启动与上游错误" },
  ];
}

function previewContent() {
  const sync = state.settings?.sync || {};
  if (state.activePreviewTab === "model") {
    return sync.model_config_preview || "{}";
  }
  if (state.activePreviewTab === "launch") {
    return sync.launch_preview || "{}";
  }
  return sync.env_preview || "";
}

function previewPath() {
  const sync = state.settings?.sync || {};
  if (state.activePreviewTab === "model") {
    return sync.model_config_path || "model-config.json";
  }
  if (state.activePreviewTab === "launch") {
    return sync.launch_path || "runtime/proxy-launch.json";
  }
  return sync.env_path || ".env";
}

function render() {
  if (!state.sessionChecked) {
    state.dashboardMounted = false;
    root.innerHTML = `
      <div class="login-shell">
        <section class="login-card">
          <div class="login-badge">Responses Proxy</div>
          <h1>正在连接本地控制台</h1>
          <p>正在读取会话、模型预设和代理状态，请稍候。</p>
        </section>
      </div>
    `;
    renderPresetModal();
    return;
  }

  if (!state.authenticated) {
    state.dashboardMounted = false;
    root.innerHTML = `
      <div class="login-shell">
        <section class="login-card">
          <div class="login-badge">Responses Proxy</div>
          <h1>登录管理台</h1>
          <p>在这里配置模型预设、环境变量、代理端口，并统一同步到本地运行文件。</p>
          <form id="login-form" class="login-form">
            <label class="field">
              <span>访问密码</span>
              <input
                class="input"
                id="password"
                name="password"
                type="password"
                placeholder="输入管理台访问密码"
                autocomplete="current-password"
              />
            </label>
            <button class="button button-primary" type="submit">进入控制台</button>
            <div class="error-text">${escapeHtml(state.loginError)}</div>
          </form>
        </section>
      </div>
    `;
    renderPresetModal();
    return;
  }

  mountDashboardShell();
  syncDashboard();
  renderPresetModal();
}

function mountDashboardShell() {
  if (state.dashboardMounted) {
    return;
  }

  root.innerHTML = `
    <div class="dashboard-shell">
      <aside class="dashboard-sidebar" id="sidebar-region"></aside>
      <main class="dashboard-main">
        <section id="header-region"></section>
        <section id="stats-region"></section>
        <div class="dashboard-grid dashboard-grid-top">
          <section id="presets-region"></section>
          <section id="settings-region"></section>
        </div>
        <div class="dashboard-grid dashboard-grid-bottom">
          <section id="preview-region"></section>
          <section id="logs-region"></section>
        </div>
      </main>
    </div>
  `;
  state.dashboardMounted = true;
}

function syncDashboard() {
  if (!state.dashboardMounted) {
    return;
  }

  const proxy = getProxy();
  const manager = getManager();
  const activePreset = getActivePreset();

  setRegion("sidebar-region", renderSidebar(activePreset));
  setRegion("header-region", renderHeader(proxy, activePreset, manager));
  setRegion("stats-region", renderStats(proxy, activePreset));
  setRegion("presets-region", renderPresetSection(activePreset));
  if (!state.settingsDirty) {
    setRegion("settings-region", renderSettingsSection(activePreset, proxy));
  }
  setRegion("preview-region", renderPreviewSection(activePreset));
  setRegion("logs-region", renderLogsSection());
}

function renderSidebar(activePreset) {
  const items = navItems();
  return `
    <div class="sidebar-brand">
      <div class="sidebar-logo">⚙</div>
      <div>
        <div class="sidebar-title">代理控制大屏</div>
        <div class="sidebar-subtitle">${escapeHtml(activePreset?.name || "未激活预设")}</div>
      </div>
    </div>

    <div class="sidebar-group-label">主导航</div>
    <nav class="sidebar-nav">
      ${items
        .map(
          (item) => `
            <button
              class="sidebar-link ${state.currentSection === item.id ? "active" : ""}"
              data-action="nav-section"
              data-target="${item.id}"
            >
              <span class="sidebar-link-icon">${item.icon}</span>
              <span class="sidebar-link-text">
                <strong>${item.label}</strong>
                <small>${item.note}</small>
              </span>
            </button>
          `
        )
        .join("")}
    </nav>

    <div class="sidebar-footer">
      <div>当前 Base URL</div>
      <code>${escapeHtml(currentBaseUrl())}</code>
    </div>
  `;
}

function renderHeader(proxy, activePreset, manager) {
  return `
    <section class="topbar-card" id="section-overview">
      <div class="topbar-title">
        <div class="topbar-icon">📈</div>
        <div>
          <h1>模型代理控制台</h1>
          <p>点激活或启动代理时，统一同步预设、.env、model-config.json 和运行快照。</p>
        </div>
      </div>
      <div class="topbar-meta">
        <div class="status-inline">
          <span class="status-dot ${proxyStatusTone(proxy)}"></span>
          <span>${escapeHtml(proxyStatusLabel(proxy))}</span>
        </div>
        <div class="topbar-time">${escapeHtml(state.nowLabel || nowString())}</div>
        <button class="button button-ghost" data-action="refresh" ${buttonDisabled(isBusy())}>刷新</button>
        <button class="button button-ghost" data-action="logout" ${buttonDisabled(isBusy())}>退出</button>
      </div>
    </section>
  `;
}

function renderStats(proxy, activePreset) {
  const cards = [
    {
      title: "当前模型",
      value: activePreset?.model || "未配置",
      note: activePreset?.provider || "请先创建一个模型预设",
      accent: "blue",
      icon: "🤖",
    },
    {
      title: "代理端口",
      value: String(proxy?.port || activePreset?.proxy_port || "8800"),
      note: `${activePreset?.proxy_host || proxy?.host || "127.0.0.1"} 监听`,
      accent: "green",
      icon: "🔌",
    },
    {
      title: "激活预设",
      value: activePreset?.name || "未激活",
      note: activePreset?.base_url || "点编辑可修改模型信息",
      accent: "purple",
      icon: "🧠",
    },
    {
      title: "同步目标",
      value: state.settings?.sync?.active_preset_name || "未同步",
      note: state.settings?.sync?.env_path || ".env",
      accent: "orange",
      icon: "🗄",
    },
  ];

  return `
    <section class="stats-row">
      ${cards
        .map(
          (card) => `
            <article class="stat-box accent-${card.accent}">
              <div class="stat-icon">${card.icon}</div>
              <div class="stat-copy">
                <div class="stat-title">${card.title}</div>
                <div class="stat-value">${escapeHtml(card.value)}</div>
                <div class="stat-note">${escapeHtml(card.note)}</div>
              </div>
            </article>
          `
        )
        .join("")}
    </section>
  `;
}

function renderPresetSection(activePreset) {
  return `
    <section class="panel-card panel-large" id="section-presets">
      <div class="card-header">
        <div>
          <div class="card-title">模型预设</div>
          <div class="card-subtitle">保存多套模型配置，支持直接编辑端口、Key、Base URL 和认证头。</div>
        </div>
        <div class="card-actions">
          <button class="button button-secondary" data-action="edit-active-preset" ${buttonDisabled(!activePreset || isBusy())}>编辑当前预设</button>
          <button class="button button-primary" data-action="new-preset" ${buttonDisabled(isBusy())}>新建预设</button>
        </div>
      </div>
      ${
        state.presets.length
          ? `
            <div class="preset-table">
              <div class="preset-table-head">
                <span>预设</span>
                <span>模型</span>
                <span>同步信息</span>
                <span>操作</span>
              </div>
              ${state.presets.map((preset) => renderPresetRow(preset)).join("")}
            </div>
          `
          : `<div class="empty-box">还没有模型预设，先创建一套 DeepSeek、Mimo 或自定义服务商配置。</div>`
      }
    </section>
  `;
}

function renderPresetRow(preset) {
  const active = preset.id === state.activePresetId;
  const running = Boolean(getProxy()?.running) && state.status?.active_preset?.id === preset.id;
  return `
    <article class="preset-table-row ${active ? "is-active" : ""}">
      <div class="preset-main">
        <div class="preset-main-top">
          <strong>${escapeHtml(preset.name)}</strong>
          <span class="badge">${escapeHtml(preset.provider)}</span>
          ${active ? `<span class="badge badge-success">已激活</span>` : ""}
          ${running ? `<span class="badge badge-info">运行中</span>` : ""}
        </div>
        <div class="preset-main-sub">${escapeHtml(preset.description || "无额外备注")}</div>
      </div>
      <div class="preset-detail">
        <div>${escapeHtml(preset.model)}</div>
        <small>${escapeHtml(preset.base_url)}</small>
      </div>
      <div class="preset-detail">
        <div>${escapeHtml(preset.proxy_host)}:${escapeHtml(preset.proxy_port)}</div>
        <small>${escapeHtml(preset.chat_path)} · ${escapeHtml(preset.api_key_header_name || "Authorization")}</small>
      </div>
      <div class="row-actions">
        <button class="button button-ghost" data-action="activate-preset" data-id="${preset.id}" ${buttonDisabled(isBusy())}>激活</button>
        <button class="button button-ghost" data-action="activate-restart" data-id="${preset.id}" ${buttonDisabled(isBusy())}>激活并重启</button>
        <button class="button button-ghost" data-action="test-preset" data-id="${preset.id}" ${buttonDisabled(isBusy())}>测试</button>
        <button class="button button-secondary" data-action="edit-preset" data-id="${preset.id}" ${buttonDisabled(isBusy())}>编辑</button>
        <button class="button button-danger button-soft" data-action="delete-preset" data-id="${preset.id}" ${buttonDisabled(isBusy())}>删除</button>
      </div>
    </article>
  `;
}

function renderSettingsSection(activePreset, proxy) {
  const sync = state.settings?.sync || {};
  return `
    <section class="panel-card panel-side" id="section-settings">
      <div class="card-header">
        <div>
          <div class="card-title">环境配置</div>
          <div class="card-subtitle">这里管理 .env 中不属于单个预设的项目，保存后会同步刷新本地环境文件。</div>
        </div>
      </div>

      <form id="settings-form" class="settings-form">
        <label class="field">
          <span>代理 API Key</span>
          <input
            class="input"
            name="proxy_api_key"
            value="${escapeHtml(state.settingsForm.proxy_api_key || "")}"
            placeholder="用于 Codex 访问本地代理"
          />
        </label>
        <div class="mini-grid">
          <div class="mini-stat">
            <span>当前代理</span>
            <strong>${escapeHtml(proxyStatusLabel(proxy))}</strong>
            <small>${escapeHtml(currentBaseUrl())}</small>
          </div>
          <div class="mini-stat">
            <span>当前预设</span>
            <strong>${escapeHtml(activePreset?.name || "未激活")}</strong>
            <small>${escapeHtml(activePreset?.model || "点击左侧新建预设")}</small>
          </div>
        </div>
        <div class="settings-note">
          <strong>同步说明</strong>
          <p>点“激活”“启动代理”或保存这里的环境配置时，会把当前预设统一写入 <code>.env</code>、<code>model-config.json</code> 和 <code>runtime/proxy-launch.json</code>。</p>
        </div>
        <div class="card-actions">
          <button class="button button-primary" type="submit" ${buttonDisabled(isBusy())}>保存环境配置</button>
          <button class="button button-secondary" type="button" data-action="copy-base-url" ${buttonDisabled(!proxy?.base_url)}>复制 Base URL</button>
        </div>
      </form>
    </section>
  `;
}

function renderPreviewSection(activePreset) {
  const sync = state.settings?.sync || {};
  const tabs = [
    { key: "env", label: ".env" },
    { key: "model", label: "model-config.json" },
    { key: "launch", label: "runtime/proxy-launch.json" },
  ];
  return `
    <section class="panel-card panel-large" id="section-preview">
      <div class="card-header">
        <div>
          <div class="card-title">同步预览</div>
          <div class="card-subtitle">当前激活预设：${escapeHtml(sync.active_preset_name || activePreset?.name || "未激活")}。以下内容会作为本地运行快照使用。</div>
        </div>
      </div>
      <div class="tabs-row">
        ${tabs
          .map(
            (tab) => `
              <button
                class="tab-button ${state.activePreviewTab === tab.key ? "active" : ""}"
                data-action="set-preview-tab"
                data-tab="${tab.key}"
              >
                ${tab.label}
              </button>
            `
          )
          .join("")}
      </div>
      <div class="preview-meta">
        <span>文件路径</span>
        <code>${escapeHtml(previewPath())}</code>
      </div>
      <pre class="preview-box">${escapeHtml(previewContent() || "暂无同步内容")}</pre>
    </section>
  `;
}

function renderLogsSection() {
  const tabs = [
    { key: "events", label: "Manager Events" },
    { key: "stdout", label: "Proxy Stdout" },
    { key: "stderr", label: "Proxy Stderr" },
  ];
  return `
    <section class="panel-card panel-side" id="section-logs">
      <div class="card-header">
        <div>
          <div class="card-title">运行日志</div>
          <div class="card-subtitle">如果代理启动失败、上游拒绝请求或端口冲突，先看这里。</div>
        </div>
      </div>
      <div class="tabs-row">
        ${tabs
          .map(
            (tab) => `
              <button
                class="tab-button ${state.activeLogTab === tab.key ? "active" : ""}"
                data-action="set-log-tab"
                data-tab="${tab.key}"
              >
                ${tab.label}
              </button>
            `
          )
          .join("")}
      </div>
      <div class="card-actions log-actions">
        <button class="button button-primary" data-action="start-proxy" ${buttonDisabled(getProxy()?.running || isBusy())}>启动代理</button>
        <button class="button button-secondary" data-action="restart-proxy" ${buttonDisabled(isBusy())}>重启代理</button>
        <button class="button button-danger button-soft" data-action="stop-proxy" ${buttonDisabled(!getProxy()?.running || isBusy())}>停止代理</button>
      </div>
      <pre class="log-box">${escapeHtml(logLines(state.activeLogTab))}</pre>
    </section>
  `;
}

function renderPresetModal() {
  if (!modalRoot) {
    return;
  }

  if (!state.modalOpen) {
    modalRoot.innerHTML = "";
    document.body.classList.remove("drawer-open");
    return;
  }

  document.body.classList.add("drawer-open");
  const preset = state.editingPreset || {
    name: "",
    provider: "",
    base_url: "",
    chat_path: "/chat/completions",
    api_key: "",
    model: "",
    proxy_host: "127.0.0.1",
    proxy_port: 8800,
    request_timeout_seconds: 120,
    headers: {},
    description: "",
    api_key_header_name: "Authorization",
    api_key_prefix: "Bearer",
  };

  modalRoot.innerHTML = `
    <div class="drawer-backdrop">
      <section class="drawer-panel">
        <div class="drawer-header">
          <div>
            <div class="drawer-badge">${state.editingPreset ? "编辑预设" : "新建预设"}</div>
            <h2>${state.editingPreset ? "修改模型预设" : "新增模型预设"}</h2>
            <p>这里直接修改模型名、API Key、Base URL、代理端口以及认证头。保存后可用“激活并重启”立即生效。</p>
          </div>
          <button class="button button-ghost" type="button" data-action="close-modal">关闭</button>
        </div>

        <form id="preset-form" class="drawer-form">
          <div class="drawer-grid">
            <label class="field">
              <span>预设名称</span>
              <input class="input" name="name" value="${escapeHtml(preset.name)}" required />
            </label>
            <label class="field">
              <span>服务商</span>
              <input class="input" name="provider" value="${escapeHtml(preset.provider)}" required />
            </label>
            <label class="field field-wide">
              <span>Base URL</span>
              <input class="input" name="base_url" value="${escapeHtml(preset.base_url)}" required />
            </label>
            <label class="field">
              <span>Chat Path</span>
              <input class="input" name="chat_path" value="${escapeHtml(preset.chat_path)}" />
            </label>
            <label class="field">
              <span>模型名</span>
              <input class="input" name="model" value="${escapeHtml(preset.model)}" required />
            </label>
            <label class="field field-wide">
              <span>API Key</span>
              <input class="input" name="api_key" value="${escapeHtml(preset.api_key)}" required />
            </label>
            <label class="field">
              <span>代理监听地址</span>
              <input class="input" name="proxy_host" value="${escapeHtml(preset.proxy_host)}" required />
            </label>
            <label class="field">
              <span>代理端口</span>
              <input class="input" name="proxy_port" type="number" min="1" max="65535" value="${escapeHtml(preset.proxy_port)}" required />
            </label>
            <label class="field">
              <span>请求超时（秒）</span>
              <input class="input" name="request_timeout_seconds" type="number" min="1" value="${escapeHtml(preset.request_timeout_seconds)}" required />
            </label>
            <label class="field">
              <span>认证头名称</span>
              <input class="input" name="api_key_header_name" value="${escapeHtml(preset.api_key_header_name || "")}" placeholder="Authorization / X-API-Key" />
            </label>
            <label class="field field-wide">
              <span>认证前缀</span>
              <input class="input" name="api_key_prefix" value="${escapeHtml(preset.api_key_prefix || "")}" placeholder="Bearer" />
              <small>推荐直接写 Bearer，系统会自动拼成 Bearer your-key。</small>
            </label>
            <label class="field field-wide">
              <span>自定义 Headers（JSON）</span>
              <textarea class="textarea" name="headers">${escapeHtml(prettyJson(preset.headers))}</textarea>
            </label>
            <label class="field field-wide">
              <span>备注</span>
              <textarea class="textarea" name="description">${escapeHtml(preset.description || "")}</textarea>
            </label>
          </div>
          <div class="drawer-actions">
            <button class="button button-ghost" type="button" data-action="close-modal">取消</button>
            <button class="button button-primary" type="submit" ${buttonDisabled(isBusy())}>
              ${state.editingPreset ? "保存修改" : "创建预设"}
            </button>
          </div>
        </form>
      </section>
    </div>
  `;
}

function openPresetModal(preset = null) {
  stopPolling();
  state.editingPreset = preset;
  state.modalOpen = true;
  renderPresetModal();
}

function closePresetModal() {
  state.editingPreset = null;
  state.modalOpen = false;
  renderPresetModal();
  if (state.authenticated) {
    startPolling();
  }
}

function applyPresetsPayload(presetsPayload, statusPayload = state.status) {
  state.presets = presetsPayload.presets || [];
  state.activePresetId = presetsPayload.active_preset_id || statusPayload?.active_preset?.id || null;
}

function applySettingsPayload(settingsPayload) {
  state.settings = settingsPayload;
  if (!state.settingsDirty) {
    state.settingsForm.proxy_api_key = settingsPayload?.settings?.proxy_api_key || "";
  }
}

async function loadInitialDashboard() {
  const [statusPayload, presetsPayload, logsPayload, settingsPayload] = await Promise.all([
    api("/api/status"),
    api("/api/presets"),
    api("/api/logs"),
    api("/api/settings"),
  ]);
  state.status = statusPayload;
  applyPresetsPayload(presetsPayload, statusPayload);
  state.logs = logsPayload;
  applySettingsPayload(settingsPayload);
}

async function refreshLiveRegions() {
  const [statusPayload, logsPayload, settingsPayload] = await Promise.all([
    api("/api/status"),
    api("/api/logs"),
    api("/api/settings"),
  ]);
  state.status = statusPayload;
  if (statusPayload.active_preset?.id) {
    state.activePresetId = statusPayload.active_preset.id;
  }
  state.logs = logsPayload;
  applySettingsPayload(settingsPayload);
}

async function refreshPresetList() {
  const presetsPayload = await api("/api/presets");
  applyPresetsPayload(presetsPayload, state.status);
}

function startClock() {
  stopClock();
  state.nowLabel = nowString();
  state.clockHandle = window.setInterval(() => {
    state.nowLabel = nowString();
    if (state.authenticated && state.dashboardMounted) {
      setRegion("header-region", renderHeader(getProxy(), getActivePreset(), getManager()));
    }
  }, CLOCK_INTERVAL_MS);
}

function stopClock() {
  if (state.clockHandle) {
    window.clearInterval(state.clockHandle);
    state.clockHandle = null;
  }
}

async function checkSession() {
  try {
    const payload = await api("/api/session", { headers: {} });
    state.authenticated = Boolean(payload.authenticated);
    state.sessionChecked = true;
    if (state.authenticated) {
      await loadInitialDashboard();
      startPolling();
      startClock();
    } else {
      stopPolling();
      stopClock();
    }
  } catch (error) {
    state.authenticated = false;
    state.sessionChecked = true;
    stopPolling();
    stopClock();
  }

  render();
}

function startPolling() {
  stopPolling();
  state.pollingHandle = window.setInterval(async () => {
    if (!state.authenticated || state.modalOpen) return;
    try {
      await refreshLiveRegions();
      syncDashboard();
    } catch (error) {
      console.error(error);
    }
  }, POLL_INTERVAL_MS);
}

function stopPolling() {
  if (state.pollingHandle) {
    window.clearInterval(state.pollingHandle);
    state.pollingHandle = null;
  }
}

async function handleLogin(event) {
  event.preventDefault();
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) {
    return;
  }

  const password = form.password.value;
  state.loginError = "";
  render();

  try {
    await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    state.authenticated = true;
    await loadInitialDashboard();
    render();
    startPolling();
    startClock();
    showToast("已进入控制台。", "success");
  } catch (error) {
    state.loginError = error.message;
    render();
  }
}

async function handleLogout() {
  state.busyAction = "logout";
  syncDashboard();
  try {
    await api("/api/auth/logout", { method: "POST" });
    stopPolling();
    stopClock();
    state.authenticated = false;
    state.loginError = "";
    state.status = null;
    state.presets = [];
    state.settings = null;
    state.settingsForm.proxy_api_key = "";
    state.settingsDirty = false;
    state.activePresetId = null;
    state.logs = { events: [], stdout: [], stderr: [] };
    state.busyAction = "";
    render();
    showToast("已退出登录。", "info");
  } catch (error) {
    state.busyAction = "";
    syncDashboard();
    showToast(error.message, "error");
  }
}

async function runAction(actionName, successMessage, runner, options = {}) {
  const { refreshPresets = false, refreshAll = false } = options;
  state.busyAction = actionName;
  syncDashboard();
  renderPresetModal();

  try {
    const result = await runner();
    if (refreshAll) {
      await loadInitialDashboard();
    } else {
      if (result?.proxy || result?.active_preset) {
        state.status = result;
        if (result.active_preset?.id) {
          state.activePresetId = result.active_preset.id;
        }
      }
      if (refreshPresets) {
        await refreshPresetList();
      }
      await refreshLiveRegions();
    }
    state.busyAction = "";
    syncDashboard();
    renderPresetModal();
    showToast(successMessage, "success");
    return result;
  } catch (error) {
    state.busyAction = "";
    syncDashboard();
    renderPresetModal();
    showToast(error.message, "error");
    return null;
  }
}

async function handlePresetSubmit(event) {
  event.preventDefault();
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) {
    return;
  }

  let headers = {};
  try {
    headers = JSON.parse(form.headers.value || "{}");
  } catch (error) {
    showToast("自定义 Headers 必须是合法 JSON。", "error");
    return;
  }

  const payload = {
    name: form.name.value.trim(),
    provider: form.provider.value.trim(),
    base_url: form.base_url.value.trim(),
    chat_path: form.chat_path.value.trim(),
    api_key: form.api_key.value.trim(),
    model: form.model.value.trim(),
    proxy_host: form.proxy_host.value.trim(),
    proxy_port: Number(form.proxy_port.value),
    request_timeout_seconds: Number(form.request_timeout_seconds.value),
    headers,
    description: form.description.value.trim(),
    api_key_header_name: form.api_key_header_name.value.trim(),
    api_key_prefix: form.api_key_prefix.value.trim(),
  };

  await runAction(
    "save-preset",
    state.editingPreset ? "预设已更新。" : "预设已创建。",
    async () => {
      if (state.editingPreset) {
        await api(`/api/presets/${state.editingPreset.id}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        });
      } else {
        await api("/api/presets", {
          method: "POST",
          body: JSON.stringify(payload),
        });
      }
      closePresetModal();
      return null;
    },
    { refreshAll: true }
  );
}

async function handleSettingsSubmit(event) {
  event.preventDefault();
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) {
    return;
  }

  const payload = {
    proxy_api_key: form.proxy_api_key.value.trim(),
  };

  state.settingsDirty = true;
  await runAction(
    "save-settings",
    "环境配置已保存。",
    async () => {
      const result = await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      state.settingsDirty = false;
      applySettingsPayload(result);
      return state.status;
    },
    { refreshAll: true }
  );
}

async function copyBaseUrl() {
  const value = getProxy()?.base_url;
  if (!value) {
    showToast("代理尚未启动，暂时没有可复制的地址。", "error");
    return;
  }
  try {
    await navigator.clipboard.writeText(value);
    showToast("Codex Base URL 已复制。", "success");
  } catch (error) {
    showToast("复制失败，请手动复制页面中的地址。", "error");
  }
}

function goToSection(target) {
  state.currentSection = target;
  syncDashboard();
  const node = document.getElementById(`section-${target}`);
  if (node) {
    node.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

async function handleActionClick(event) {
  const target = event.target.closest("[data-action]");
  if (!target) {
    return;
  }

  const { action, id, tab, target: sectionTarget } = target.dataset;

  switch (action) {
    case "logout":
      await handleLogout();
      return;
    case "refresh":
      await runAction("refresh", "状态已刷新。", async () => {
        await loadInitialDashboard();
        return state.status;
      });
      return;
    case "copy-base-url":
      await copyBaseUrl();
      return;
    case "nav-section":
      goToSection(sectionTarget || "overview");
      return;
    case "new-preset":
      openPresetModal(null);
      return;
    case "edit-active-preset": {
      const activePreset = getActivePreset();
      if (activePreset) {
        openPresetModal(activePreset);
      }
      return;
    }
    case "close-modal":
      closePresetModal();
      return;
    case "edit-preset":
      openPresetModal(state.presets.find((preset) => preset.id === id) || null);
      return;
    case "set-log-tab":
      state.activeLogTab = tab || "events";
      syncDashboard();
      return;
    case "set-preview-tab":
      state.activePreviewTab = tab || "env";
      syncDashboard();
      return;
    case "activate-preset":
      await runAction(
        "activate-preset",
        "预设已激活并同步。",
        async () => api(`/api/presets/${id}/activate`, { method: "POST" }),
        { refreshAll: true }
      );
      return;
    case "activate-restart":
      await runAction(
        "activate-restart",
        "预设已激活，代理已重启。",
        async () => {
          await api(`/api/presets/${id}/activate`, { method: "POST" });
          return api("/api/proxy/restart", {
            method: "POST",
            body: JSON.stringify({ preset_id: id }),
          });
        },
        { refreshAll: true }
      );
      return;
    case "test-preset":
      await runAction("test-preset", "连接测试已完成。", async () => {
        const result = await api(`/api/presets/${id}/test`, { method: "POST" });
        showToast(result.message || (result.ok ? "连接成功。" : "连接失败。"), result.ok ? "success" : "error");
        return state.status;
      });
      return;
    case "delete-preset":
      if (!window.confirm("确定删除这套预设吗？")) {
        return;
      }
      await runAction(
        "delete-preset",
        "预设已删除。",
        async () => {
          await api(`/api/presets/${id}`, { method: "DELETE" });
          return state.status;
        },
        { refreshAll: true }
      );
      return;
    case "start-proxy":
      await runAction("start-proxy", "代理已启动。", async () => api("/api/proxy/start", { method: "POST" }), {
        refreshAll: true,
      });
      return;
    case "restart-proxy":
      await runAction(
        "restart-proxy",
        "代理已重启。",
        async () => api("/api/proxy/restart", { method: "POST" }),
        { refreshAll: true }
      );
      return;
    case "stop-proxy":
      await runAction(
        "stop-proxy",
        "代理已停止。",
        async () => api("/api/proxy/stop", { method: "POST" }),
        { refreshAll: true }
      );
      return;
    default:
      return;
  }
}

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  if (form.id === "login-form") {
    handleLogin(event);
  }
  if (form.id === "preset-form") {
    handlePresetSubmit(event);
  }
  if (form.id === "settings-form") {
    handleSettingsSubmit(event);
  }
});

document.addEventListener("input", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)) {
    return;
  }
  if (target.name === "proxy_api_key" && target.form?.id === "settings-form") {
    state.settingsDirty = true;
    state.settingsForm.proxy_api_key = target.value;
  }
});

document.addEventListener("click", (event) => {
  handleActionClick(event);
});

checkSession();
