from __future__ import annotations


def render_user_self_binding_page() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Code-KB Self Binding</title>
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
      max-width: 860px;
      margin: 0 auto;
      padding: 24px 16px;
    }
    h1 { font-size: 24px; line-height: 1.25; margin: 0 0 16px; }
    h2 { font-size: 18px; line-height: 1.3; margin: 0 0 12px; }
    label { display: block; font-weight: 600; margin: 12px 0 6px; }
    input, select {
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
    button, a.button {
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 12px;
      background: var(--surface);
      color: var(--text);
      font: inherit;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
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
    .check { display: flex; align-items: center; color: var(--muted); margin-top: 12px; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
    dl {
      display: grid;
      grid-template-columns: minmax(120px, 170px) 1fr;
      gap: 8px 12px;
      margin: 0;
    }
    dt { color: var(--muted); }
    dd { margin: 0; overflow-wrap: anywhere; }
    code {
      display: block;
      margin-top: 4px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--bg);
      color: var(--text);
      overflow-wrap: anywhere;
      white-space: pre-wrap;
    }
    .status { margin-top: 16px; min-height: 24px; overflow-wrap: anywhere; }
    .status.ok { color: var(--ok); }
    .status.error { color: var(--danger); }
    .status.warn { color: var(--warn); }
    .muted { color: var(--muted); }
  </style>
</head>
<body>
<main>
  <h1>Code-KB Self Binding</h1>
  <section class="panel">
    <div class="grid">
      <div>
        <label for="binding-code">Binding code</label>
        <input id="binding-code" type="password" autocomplete="one-time-code">
      </div>
      <div>
        <label for="display-name">Display name</label>
        <input id="display-name" autocomplete="name">
      </div>
      <div>
        <label for="route-type">Route type</label>
        <select id="route-type">
          <option value="im_message">IM message target</option>
          <option value="im_robot">IM robot route</option>
          <option value="im_userid">IM userid</option>
          <option value="manual">Manual route</option>
        </select>
      </div>
      <div>
        <label for="route-value">Route value</label>
        <input id="route-value" autocomplete="off">
      </div>
      <div>
        <label for="ttl-days">TTL days</label>
        <input id="ttl-days" type="number" min="1" max="366" value="30">
      </div>
      <div>
        <label for="scopes">Scopes</label>
        <input id="scopes" value="diagnose" autocomplete="off">
      </div>
    </div>
    <label class="check"><input id="store-browser-token" type="checkbox" checked>Store issued token in this browser</label>
    <div class="actions">
      <button type="button" class="primary" id="issue">Issue token</button>
      <button type="button" id="clear">Clear</button>
      <a class="button" href="/auth/im/mcp/setup">MCP setup</a>
      <a class="button" href="/auth/im/confirmations/page">Confirmations</a>
    </div>
  </section>
  <section class="panel" id="result-panel" hidden>
    <h2>Issued Token</h2>
    <dl>
      <dt>Status</dt><dd id="result-status"></dd>
      <dt>Token</dt><dd><code id="issued-token"></code></dd>
      <dt>Token ID</dt><dd id="token-id"></dd>
      <dt>Expires</dt><dd id="expires-at"></dd>
      <dt>User hash</dt><dd id="bound-user"></dd>
      <dt>Metadata</dt><dd id="metadata"></dd>
    </dl>
    <div class="actions">
      <button type="button" id="copy-token">Copy token</button>
      <button type="button" id="use-token">Use on this browser</button>
    </div>
  </section>
  <div class="status muted" id="status"></div>
</main>
<script>
const tokenKey = "codekb_user_token";
const statusEl = document.getElementById("status");
const resultPanel = document.getElementById("result-panel");
function value(id) {
  return document.getElementById(id).value.trim();
}
function setValue(id, text) {
  document.getElementById(id).value = text || "";
}
function setText(id, text) {
  document.getElementById(id).textContent = text || "";
}
function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = "status " + (cls || "muted");
}
function scopes() {
  return value("scopes").split(",").map((item) => item.trim()).filter(Boolean);
}
async function issueToken() {
  if (!value("binding-code")) {
    setStatus("binding code is required", "error");
    return;
  }
  if (!value("route-value")) {
    setStatus("route value is required", "error");
    return;
  }
  setStatus("Issuing token...", "muted");
  const response = await fetch("/auth/im/self-bindings", {
    method: "POST",
    headers: {"Accept": "application/json", "Content-Type": "application/json"},
    body: JSON.stringify({
      binding_code: value("binding-code"),
      display_name: value("display-name"),
      route_type: value("route-type"),
      route_value: value("route-value"),
      scopes: scopes(),
      ttl_days: Number(value("ttl-days") || 30),
    }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || response.statusText);
  renderResult(data);
  if (document.getElementById("store-browser-token").checked) {
    localStorage.setItem(tokenKey, data.token || "");
  }
  setStatus("token issued", "ok");
}
function renderResult(data) {
  const binding = data.binding || {};
  resultPanel.hidden = false;
  setText("result-status", data.status || "");
  setText("issued-token", data.token || "");
  setText("token-id", binding.token_id || "");
  setText("expires-at", binding.expires_at || "");
  setText("bound-user", binding.user_id_hash || "");
  setText("metadata", JSON.stringify(binding.metadata || {}));
}
async function copyToken() {
  await navigator.clipboard.writeText(document.getElementById("issued-token").textContent || "");
  setStatus("token copied", "ok");
}
function useToken() {
  localStorage.setItem(tokenKey, document.getElementById("issued-token").textContent || "");
  setStatus("token stored in this browser", "ok");
}
function clearForm() {
  for (const id of ["binding-code", "display-name", "route-value"]) setValue(id, "");
  setValue("ttl-days", "30");
  setValue("scopes", "diagnose");
  resultPanel.hidden = true;
  setStatus("", "muted");
}
document.getElementById("issue").addEventListener("click", () => issueToken().catch((error) => setStatus(error.message, "error")));
document.getElementById("copy-token").addEventListener("click", () => copyToken().catch((error) => setStatus(error.message, "error")));
document.getElementById("use-token").addEventListener("click", useToken);
document.getElementById("clear").addEventListener("click", clearForm);
</script>
</body>
</html>
"""
