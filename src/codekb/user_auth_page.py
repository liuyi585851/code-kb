from __future__ import annotations

import html


def render_im_mcp_setup_page() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Code-KB MCP Auth</title>
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
      --warn: #b54708;
      --ok: #067647;
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
      max-width: 760px;
      margin: 0 auto;
      padding: 24px 16px;
    }
    h1 {
      font-size: 24px;
      line-height: 1.25;
      margin: 0 0 16px;
    }
    label {
      display: block;
      font-weight: 600;
      margin: 16px 0 6px;
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
    button.primary, a.button.primary {
      border-color: var(--primary);
      background: var(--primary);
      color: #ffffff;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 16px;
    }
    .panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      margin-top: 16px;
    }
    .muted { color: var(--muted); }
    .status { margin-top: 16px; min-height: 24px; overflow-wrap: anywhere; }
    .status.ok { color: var(--ok); }
    .status.error { color: var(--danger); }
    .status.warn { color: var(--warn); }
    dl {
      display: grid;
      grid-template-columns: minmax(110px, 160px) 1fr;
      gap: 8px 12px;
      margin: 0;
    }
    dt { color: var(--muted); }
    dd { margin: 0; overflow-wrap: anywhere; }
  </style>
</head>
<body>
<main>
  <h1>Code-KB MCP Auth</h1>
  <div class="panel">
    <label for="auth-token">Current user token</label>
    <input id="auth-token" type="password" autocomplete="current-password">
    <div class="actions">
      <a class="button primary" id="authorize" href="/auth/im/oauth/login?next=/auth/im/mcp/setup">IM authorize</a>
      <button type="button" id="check">Check</button>
      <button type="button" id="smoke">Run self-test</button>
      <button type="button" id="copy">Copy MCP args</button>
      <button type="button" id="forget">Forget token</button>
    </div>
  </div>
  <section class="panel" id="binding" hidden>
    <dl>
      <dt>Status</dt><dd id="binding-status"></dd>
      <dt>Token ID</dt><dd id="token-id"></dd>
      <dt>Expires</dt><dd id="expires-at"></dd>
      <dt>Scopes</dt><dd id="scopes"></dd>
      <dt>API base</dt><dd id="api-base"></dd>
    </dl>
  </section>
  <section class="panel" id="setup-panel">
    <dl>
      <dt>Setup</dt><dd id="setup-state">Loading...</dd>
      <dt>OAuth</dt><dd id="oauth-state"></dd>
      <dt>Missing env</dt><dd id="oauth-missing"></dd>
      <dt>Callback</dt><dd id="oauth-callback"></dd>
      <dt>Active tokens</dt><dd id="active-bindings"></dd>
      <dt>External inputs</dt><dd><a id="external-inputs" href="/diagnose/external-inputs/page">Open</a></dd>
      <dt>External inputs MD</dt><dd><a id="external-inputs-md" href="/diagnose/external-inputs.md">Open</a></dd>
      <dt>Final verification</dt><dd><a id="final-verification" href="/diagnose/final-verification/page">Open</a></dd>
      <dt>Self binding</dt><dd><a id="self-binding" href="/auth/im/self-bindings/page">Open</a></dd>
      <dt>Token binding fallback</dt><dd><a id="token-binding" href="/auth/im/token-bindings/page">Open</a></dd>
      <dt>IM config</dt><dd><a id="im-configure" href="/auth/im/configure/page">Open</a></dd>
      <dt>Current user demo</dt><dd><a id="current-user-demo" href="/demo/current-user">Open</a></dd>
      <dt>Webhook demo</dt><dd><a id="webhook-demo" href="/demo/webhook">Open</a></dd>
      <dt>Web push inbox</dt><dd><a id="confirmations" href="/auth/im/confirmations/page">Open</a></dd>
    </dl>
  </section>
  <div class="status muted" id="status"></div>
</main>
<script>
const tokenKey = "codekb_user_token";
const tokenInput = document.getElementById("auth-token");
const statusEl = document.getElementById("status");
const bindingPanel = document.getElementById("binding");
const authorize = document.getElementById("authorize");
tokenInput.value = localStorage.getItem(tokenKey) || "";
authorize.href = "/auth/im/oauth/login?next=" + encodeURIComponent("/auth/im/mcp/setup");
function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = "status " + (cls || "muted");
}
async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || response.statusText);
  return data;
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
    document.getElementById("setup-state").textContent = data.status || "";
    document.getElementById("oauth-state").textContent = oauth.configured ? "configured" : "pending";
    document.getElementById("oauth-missing").textContent = (oauth.missing_env || []).join(", ") || "-";
    document.getElementById("oauth-callback").textContent = oauth.callback_url || "";
    document.getElementById("active-bindings").textContent = String(mcp.active_token_bindings || 0);
    document.getElementById("external-inputs").href = data.external_inputs_page_url || data.external_inputs_url || "/diagnose/external-inputs/page";
    document.getElementById("external-inputs-md").href = data.external_inputs_markdown_url || "/diagnose/external-inputs.md";
    document.getElementById("final-verification").href = data.final_verification_page_url || data.final_verification_url || "/diagnose/final-verification/page";
    document.getElementById("self-binding").href = data.self_binding_page_url || "/auth/im/self-bindings/page";
    document.getElementById("token-binding").href = data.token_binding_page_url || "/auth/im/token-bindings/page";
    document.getElementById("im-configure").href = data.im_configure_page_url || data.im_configure_url || "/auth/im/configure/page";
    document.getElementById("current-user-demo").href = data.current_user_demo_url || "/demo/current-user";
    document.getElementById("webhook-demo").href = data.webhook_demo_url || "/demo/webhook";
    document.getElementById("confirmations").href = data.web_push_inbox_url || data.confirmations_url || "/auth/im/confirmations/page";
    if (oauth.login_url) authorize.href = oauth.login_url;
    if (!oauth.configured) {
      setStatus("IM OAuth is not configured yet. Send the callback URL and missing env keys to the IM admin.", "warn");
    }
  } catch (error) {
    document.getElementById("setup-state").textContent = "error";
    setStatus(error.message, "error");
  }
}
async function checkToken() {
  const authToken = tokenInput.value.trim();
  if (!authToken) {
    bindingPanel.hidden = true;
    setStatus("token is required", "error");
    return;
  }
  setStatus("Checking...", "muted");
  try {
    const data = await postJson("/auth/im/current-user/status", {auth_token: authToken});
    const binding = data.binding || {};
    localStorage.setItem(tokenKey, authToken);
    document.getElementById("binding-status").textContent = data.status || "";
    document.getElementById("token-id").textContent = binding.token_id || "";
    document.getElementById("expires-at").textContent = binding.expires_at || "";
    document.getElementById("scopes").textContent = (binding.scopes || []).join(", ");
    document.getElementById("api-base").textContent = (data.mcp || {}).api_base_url || "";
    bindingPanel.hidden = false;
    setStatus("Token is active", "ok");
  } catch (error) {
    bindingPanel.hidden = true;
    setStatus(error.message, "error");
  }
}
async function copyMcpArgs() {
  const authToken = tokenInput.value.trim();
  if (!authToken) {
    setStatus("token is required", "error");
    return;
  }
  await navigator.clipboard.writeText(JSON.stringify({auth_token: authToken}, null, 2));
  setStatus("MCP args copied", "ok");
}
async function runSmoke() {
  const authToken = tokenInput.value.trim();
  if (!authToken) {
    setStatus("token is required", "error");
    return;
  }
  setStatus("Running self-test...", "muted");
  const data = await postJson("/auth/im/current-user/smoke", {auth_token: authToken});
  const confirmation = data.confirmation || {};
  if (confirmation.confirmation_id) {
    const url = "/auth/im/confirmations/page?confirmation_id=" + encodeURIComponent(confirmation.confirmation_id);
    document.getElementById("confirmations").href = url;
    setStatus("Self-test " + data.status + ". Open confirmations to respond.", data.ok ? "ok" : "warn");
  } else {
    setStatus("Self-test " + data.status, data.ok ? "ok" : "warn");
  }
}
document.getElementById("check").addEventListener("click", checkToken);
document.getElementById("smoke").addEventListener("click", () => {
  runSmoke().catch((error) => setStatus(error.message, "error"));
});
document.getElementById("copy").addEventListener("click", () => {
  copyMcpArgs().catch((error) => setStatus(error.message, "error"));
});
document.getElementById("forget").addEventListener("click", () => {
  localStorage.removeItem(tokenKey);
  tokenInput.value = "";
  bindingPanel.hidden = true;
  setStatus("Token removed", "ok");
});
loadSetupStatus();
if (tokenInput.value) checkToken();
</script>
</body>
</html>
"""


def render_im_oauth_success_page(
    *,
    token: str,
    token_id: str,
    expires_at: str,
    next_url: str = "",
) -> str:
    safe_token = html.escape(str(token or ""), quote=True)
    safe_token_id = html.escape(str(token_id or ""), quote=True)
    safe_expires_at = html.escape(str(expires_at or ""), quote=True)
    safe_next_url = html.escape(str(next_url or ""), quote=True)
    next_link = ""
    if safe_next_url:
        next_link = f'<p><a class="button" href="{safe_next_url}">Continue</a></p>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Code-KB Auth</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f6f7f9;
      --surface: #ffffff;
      --text: #1f2328;
      --muted: #667085;
      --line: #d0d7de;
      --primary: #0b6bcb;
      --ok: #067647;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #101418;
        --surface: #161b22;
        --text: #f0f3f6;
        --muted: #9aa4b2;
        --line: #30363d;
        --primary: #5aa7ff;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
    }}
    main {{
      max-width: 760px;
      margin: 0 auto;
      padding: 24px 16px;
    }}
    h1 {{
      font-size: 24px;
      line-height: 1.25;
      margin: 0 0 16px;
    }}
    .panel {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      margin-top: 16px;
    }}
    .muted {{ color: var(--muted); }}
    .ok {{ color: var(--ok); }}
    code {{
      display: block;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 6px;
      overflow-wrap: anywhere;
      background: var(--bg);
    }}
    .button {{
      display: inline-block;
      min-height: 40px;
      border: 1px solid var(--primary);
      border-radius: 6px;
      padding: 8px 12px;
      background: var(--primary);
      color: #ffffff;
      text-decoration: none;
    }}
  </style>
</head>
<body>
<main>
  <h1>Code-KB Auth</h1>
  <section class="panel">
    <p class="ok">授权完成，当前用户 token 已签发。</p>
    <p class="muted">Token ID: {safe_token_id}</p>
    <p class="muted">Expires: {safe_expires_at}</p>
    <code id="auth-token">{safe_token}</code>
    {next_link}
  </section>
</main>
<script>
const token = document.getElementById("auth-token").textContent.trim();
if (token) {{
  localStorage.setItem("codekb_user_token", token);
}}
</script>
</body>
</html>
"""
