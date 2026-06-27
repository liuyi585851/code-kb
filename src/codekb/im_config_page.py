from __future__ import annotations


def render_im_config_page() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Code-KB IM Config</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f6f7f9;
      --surface: #ffffff;
      --text: #1f2328;
      --muted: #667085;
      --line: #d0d7de;
      --primary: #0b6bcb;
      --danger: #b42318;
      --ok: #067647;
      --warn: #b54708;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #101418;
        --surface: #161b22;
        --text: #f0f3f6;
        --muted: #9aa4b2;
        --line: #30363d;
        --primary: #5aa7ff;
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
    }
    main {
      max-width: 900px;
      margin: 0 auto;
      padding: 24px 16px;
    }
    h1 {
      font-size: 24px;
      line-height: 1.25;
      margin: 0 0 16px;
    }
    h2 {
      font-size: 18px;
      line-height: 1.3;
      margin: 0 0 12px;
    }
    label {
      display: block;
      font-weight: 600;
      margin: 12px 0 6px;
    }
    input {
      width: 100%;
      min-height: 44px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px 12px;
      background: var(--surface);
      color: var(--text);
      font: inherit;
    }
    input[type="checkbox"] {
      width: 18px;
      min-height: 18px;
      margin: 0 8px 0 0;
    }
    button {
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 12px;
      background: var(--surface);
      color: var(--text);
      font: inherit;
      cursor: pointer;
    }
    button.primary {
      border-color: var(--primary);
      background: var(--primary);
      color: #ffffff;
    }
    .panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      margin-top: 16px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 8px 16px;
    }
    .checks {
      display: grid;
      gap: 10px;
      margin-top: 14px;
    }
    .check {
      display: flex;
      align-items: center;
      color: var(--muted);
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 16px;
    }
    dl {
      display: grid;
      grid-template-columns: minmax(130px, 180px) 1fr;
      gap: 8px 12px;
      margin: 0;
    }
    dt { color: var(--muted); }
    dd { margin: 0; overflow-wrap: anywhere; }
    .status { margin-top: 16px; min-height: 24px; overflow-wrap: anywhere; }
    .status.ok { color: var(--ok); }
    .status.error { color: var(--danger); }
    .status.warn { color: var(--warn); }
    .muted { color: var(--muted); }
  </style>
</head>
<body>
<main>
  <h1>Code-KB IM Config</h1>
  <section class="panel">
    <div class="grid">
      <div>
        <label for="admin-token">Admin token</label>
        <input id="admin-token" type="password" autocomplete="current-password">
      </div>
      <div>
        <label for="agent-id">Agent ID</label>
        <input id="agent-id" autocomplete="off">
      </div>
      <div>
        <label for="corp-id">Corp ID</label>
        <input id="corp-id" autocomplete="off">
      </div>
      <div>
        <label for="app-secret">App secret</label>
        <input id="app-secret" type="password" autocomplete="new-password">
      </div>
      <div>
        <label for="state-secret">OAuth state secret</label>
        <input id="state-secret" type="password" autocomplete="new-password">
      </div>
      <div>
        <label for="redirect-uri">OAuth callback</label>
        <input id="redirect-uri" autocomplete="off">
      </div>
      <div>
        <label for="confirm-url">Confirm URL base</label>
        <input id="confirm-url" autocomplete="off">
      </div>
    </div>
    <div class="checks">
      <label class="check"><input id="enable-send" type="checkbox">Enable IM send</label>
      <label class="check"><input id="confirm-send" type="checkbox">Confirm real send</label>
    </div>
    <div class="actions">
      <button type="button" id="plan">Plan</button>
      <button type="button" class="primary" id="apply">Apply</button>
      <button type="button" id="clear">Clear</button>
    </div>
  </section>
  <section class="panel" id="setup-panel">
    <h2>Setup</h2>
    <dl>
      <dt>Status</dt><dd id="setup-status">Loading...</dd>
      <dt>Missing env</dt><dd id="missing-env"></dd>
      <dt>Callback</dt><dd id="callback-url"></dd>
      <dt>Active tokens</dt><dd id="active-tokens"></dd>
    </dl>
  </section>
  <section class="panel" id="report-panel" hidden>
    <h2>Report</h2>
    <dl>
      <dt>Status</dt><dd id="report-status"></dd>
      <dt>OK</dt><dd id="report-ok"></dd>
      <dt>Applied</dt><dd id="report-applied"></dd>
      <dt>Restart</dt><dd id="report-restart"></dd>
      <dt>Missing env</dt><dd id="report-missing"></dd>
      <dt>Updates</dt><dd id="report-updates"></dd>
      <dt>Send enabled</dt><dd id="report-send"></dd>
      <dt>Message</dt><dd id="report-message"></dd>
    </dl>
  </section>
  <div class="status muted" id="status"></div>
</main>
<script>
const statusEl = document.getElementById("status");
const reportPanel = document.getElementById("report-panel");
function value(id) {
  return document.getElementById(id).value.trim();
}
function checked(id) {
  return document.getElementById(id).checked;
}
function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = "status " + (cls || "muted");
}
function setText(id, text) {
  document.getElementById(id).textContent = text || "";
}
async function loadSetupStatus() {
  try {
    const response = await fetch("/auth/im/mcp/setup/status", {
      headers: {"Accept": "application/json"},
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || response.statusText);
    const oauth = data.oauth || {};
    const mcp = data.mcp || {};
    setText("setup-status", data.status || "");
    setText("missing-env", (oauth.missing_env || []).join(", ") || "-");
    setText("callback-url", oauth.callback_url || "");
    setText("active-tokens", String(mcp.active_token_bindings || 0));
    if (!value("redirect-uri") && oauth.callback_url) {
      document.getElementById("redirect-uri").value = oauth.callback_url;
    }
    if (!value("confirm-url") && data.confirmations_url) {
      document.getElementById("confirm-url").value = data.confirmations_url;
    }
  } catch (error) {
    setText("setup-status", "error");
    setStatus(error.message, "error");
  }
}
async function submitConfig(apply) {
  const adminToken = value("admin-token");
  if (!adminToken) {
    setStatus("admin token is required", "error");
    return;
  }
  setStatus(apply ? "Applying..." : "Planning...", "muted");
  const payload = {
    corp_id: value("corp-id"),
    agent_id: value("agent-id"),
    app_secret: value("app-secret"),
    oauth_state_secret: value("state-secret"),
    redirect_uri: value("redirect-uri"),
    confirm_url_base: value("confirm-url"),
    enable_send: checked("enable-send"),
    confirm_real_send: checked("confirm-send"),
    apply,
  };
  const response = await fetch("/auth/im/configure", {
    method: "POST",
    headers: {
      "Accept": "application/json",
      "Content-Type": "application/json",
      "X-CodeKB-Admin-Token": adminToken,
    },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || response.statusText);
  renderReport(data);
  setStatus(data.status || "ok", data.ok ? "ok" : "warn");
}
function renderReport(data) {
  reportPanel.hidden = false;
  setText("report-status", data.status || "");
  setText("report-ok", String(Boolean(data.ok)));
  setText("report-applied", String(Boolean(data.applied)));
  setText("report-restart", String(Boolean(data.restart_required)));
  setText("report-missing", (data.missing_env || []).join(", ") || "-");
  setText("report-updates", (data.planned_update_keys || []).join(", ") || "-");
  setText("report-send", String(Boolean(data.im_send_enabled_after_apply)));
  setText("report-message", data.message || "");
}
document.getElementById("plan").addEventListener("click", () => {
  submitConfig(false).catch((error) => setStatus(error.message, "error"));
});
document.getElementById("apply").addEventListener("click", () => {
  submitConfig(true).catch((error) => setStatus(error.message, "error"));
});
document.getElementById("clear").addEventListener("click", () => {
  for (const id of ["admin-token", "corp-id", "agent-id", "app-secret", "state-secret"]) {
    document.getElementById(id).value = "";
  }
  document.getElementById("enable-send").checked = false;
  document.getElementById("confirm-send").checked = false;
  reportPanel.hidden = true;
  setStatus("", "muted");
});
loadSetupStatus();
</script>
</body>
</html>
"""
