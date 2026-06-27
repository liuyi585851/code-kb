from __future__ import annotations

import html

from .web_ui import render_legacy_page_in_shell


def render_user_confirmation_page(*, confirmation_id: str = "") -> str:
    safe_confirmation_id = html.escape(str(confirmation_id or ""), quote=True)
    legacy_html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Code-KB Web Push Inbox</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f6f7f9;
      --surface: #ffffff;
      --text: #1f2328;
      --muted: #667085;
      --line: #d0d7de;
      --primary: #0b6bcb;
      --danger: #b42318;
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
    label {{
      display: block;
      font-weight: 600;
      margin: 16px 0 6px;
    }}
    input, textarea {{
      width: 100%;
      min-height: 44px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px 12px;
      background: var(--surface);
      color: var(--text);
      font: inherit;
    }}
    textarea {{ min-height: 88px; resize: vertical; }}
    button {{
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 12px;
      background: var(--surface);
      color: var(--text);
      font: inherit;
      cursor: pointer;
    }}
    button.primary {{
      border-color: var(--primary);
      background: var(--primary);
      color: #ffffff;
    }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 16px;
    }}
    .panel {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      margin-top: 16px;
    }}
    .muted {{ color: var(--muted); }}
    .status {{ margin-top: 16px; min-height: 24px; }}
    .status.ok {{ color: var(--ok); }}
    .status.error {{ color: var(--danger); }}
    .pending-item {{
      width: 100%;
      text-align: left;
      margin-top: 8px;
      display: block;
    }}
    .pending-meta {{ display: block; color: var(--muted); font-size: 13px; }}
    .check {{
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      margin-top: 12px;
    }}
    input[type="checkbox"] {{
      width: 18px;
      min-height: 18px;
      margin: 0;
    }}
    dl {{
      display: grid;
      grid-template-columns: minmax(110px, 160px) 1fr;
      gap: 8px 12px;
      margin: 0;
    }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; overflow-wrap: anywhere; }}
    pre {{
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      margin: 0;
    }}
  </style>
</head>
<body data-ui-version="2">
<main class="app-shell">
  <h1>Code-KB Web Push Inbox</h1>
  <div class="panel">
    <label for="confirmation-id">Confirmation ID</label>
    <input id="confirmation-id" value="{safe_confirmation_id}" autocomplete="off">
    <label for="auth-token">Current user token</label>
    <input id="auth-token" type="password" autocomplete="current-password">
    <label class="check"><input id="include-responded" type="checkbox">Show responded messages</label>
    <label class="check"><input id="auto-refresh" type="checkbox" checked>Auto refresh inbox</label>
    <div class="actions">
      <button class="primary" type="button" id="load">Load</button>
      <button type="button" id="load-pending">Refresh inbox</button>
      <button type="button" id="remember">Remember token</button>
      <button type="button" id="forget">Forget token</button>
    </div>
  </div>
  <section class="panel" id="pending" hidden>
    <dl>
      <dt>Total</dt><dd id="pending-total"></dd>
      <dt>Last refresh</dt><dd id="last-refresh"></dd>
      <dt>Items</dt><dd><div id="pending-list"></div></dd>
    </dl>
  </section>
  <section class="panel" id="confirmation" hidden>
    <dl>
      <dt>Reason</dt><dd id="reason"></dd>
      <dt>Status</dt><dd id="state"></dd>
      <dt>Created</dt><dd id="created"></dd>
      <dt>Message</dt><dd><pre id="message"></pre></dd>
    </dl>
    <label for="comment">Comment</label>
    <textarea id="comment"></textarea>
    <div class="actions">
      <button class="primary" type="button" data-decision="confirmed">Confirm</button>
      <button type="button" data-decision="needs_followup">Need follow-up</button>
      <button type="button" data-decision="rejected">Reject</button>
    </div>
  </section>
  <div class="status muted" id="status"></div>
</main>
<script>
const tokenKey = "codekb_user_token";
const idInput = document.getElementById("confirmation-id");
const tokenInput = document.getElementById("auth-token");
const statusEl = document.getElementById("status");
const panel = document.getElementById("confirmation");
const pendingPanel = document.getElementById("pending");
const pendingList = document.getElementById("pending-list");
const includeResponded = document.getElementById("include-responded");
const autoRefresh = document.getElementById("auto-refresh");
tokenInput.value = localStorage.getItem(tokenKey) || "";
function setStatus(text, cls) {{
  statusEl.textContent = text;
  statusEl.className = "status " + (cls || "muted");
}}
async function postJson(url, payload) {{
  const response = await fetch(url, {{
    method: "POST",
    headers: {{"Content-Type": "application/json"}},
    body: JSON.stringify(payload),
  }});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || response.statusText);
  return data;
}}
async function loadConfirmation() {{
  const confirmationId = idInput.value.trim();
  const authToken = tokenInput.value.trim();
  if (!confirmationId || !authToken) {{
    setStatus("confirmation_id and token are required", "error");
    return;
  }}
  setStatus("Loading...", "muted");
  try {{
    const data = await postJson(`/auth/im/confirmations/${{encodeURIComponent(confirmationId)}}/detail`, {{auth_token: authToken}});
    const confirmation = data.confirmation;
    document.getElementById("reason").textContent = confirmation.reason || "";
    document.getElementById("state").textContent = confirmation.status || "";
    document.getElementById("created").textContent = confirmation.created_at || "";
    document.getElementById("message").textContent = confirmation.message || "";
    panel.hidden = false;
    setStatus("Loaded", "ok");
  }} catch (error) {{
    panel.hidden = true;
    setStatus(error.message, "error");
  }}
}}
async function loadPending() {{
  const authToken = tokenInput.value.trim();
  if (!authToken) {{
    setStatus("token is required", "error");
    return;
  }}
  setStatus("Loading pending...", "muted");
  try {{
    const data = await postJson("/auth/im/confirmations/pending", {{
      auth_token: authToken,
      limit: 50,
      include_responded: includeResponded.checked,
    }});
    pendingList.textContent = "";
    document.getElementById("pending-total").textContent = String(data.total || 0);
    document.getElementById("last-refresh").textContent = new Date().toLocaleString();
    (data.confirmations || []).forEach((item) => {{
      const button = document.createElement("button");
      button.type = "button";
      button.className = "pending-item";
      button.dataset.confirmationId = item.confirmation_id || "";
      const title = document.createElement("span");
      title.textContent = item.message || item.confirmation_id || "";
      const meta = document.createElement("span");
      meta.className = "pending-meta";
      meta.textContent = [item.status || "", item.reason || "", item.created_at || ""].filter(Boolean).join(" | ");
      button.appendChild(title);
      button.appendChild(meta);
      button.addEventListener("click", () => {{
        idInput.value = button.dataset.confirmationId || "";
        loadConfirmation();
      }});
      pendingList.appendChild(button);
    }});
    if (!(data.confirmations || []).length) {{
      const empty = document.createElement("span");
      empty.className = "muted";
      empty.textContent = includeResponded.checked ? "No messages" : "No pending messages";
      pendingList.appendChild(empty);
    }}
    pendingPanel.hidden = false;
    setStatus("Pending loaded", "ok");
  }} catch (error) {{
    pendingPanel.hidden = true;
    setStatus(error.message, "error");
  }}
}}
async function respond(decision) {{
  const confirmationId = idInput.value.trim();
  const authToken = tokenInput.value.trim();
  if (!confirmationId || !authToken) {{
    setStatus("confirmation_id and token are required", "error");
    return;
  }}
  setStatus("Submitting...", "muted");
  try {{
    await postJson(`/auth/im/confirmations/${{encodeURIComponent(confirmationId)}}/response`, {{
      auth_token: authToken,
      decision,
      comment: document.getElementById("comment").value,
      metadata: {{source: "confirmation_page"}},
    }});
    setStatus("Response recorded", "ok");
    await loadConfirmation();
    if (!pendingPanel.hidden) await loadPending();
  }} catch (error) {{
    setStatus(error.message, "error");
  }}
}}
document.getElementById("load").addEventListener("click", loadConfirmation);
document.getElementById("load-pending").addEventListener("click", loadPending);
document.getElementById("remember").addEventListener("click", () => {{
  localStorage.setItem(tokenKey, tokenInput.value.trim());
  setStatus("Token saved in this browser", "ok");
}});
document.getElementById("forget").addEventListener("click", () => {{
  localStorage.removeItem(tokenKey);
  tokenInput.value = "";
  setStatus("Token removed", "ok");
}});
includeResponded.addEventListener("change", () => {{
  if (tokenInput.value.trim()) loadPending();
}});
document.querySelectorAll("[data-decision]").forEach((button) => {{
  button.addEventListener("click", () => respond(button.dataset.decision));
}});
setInterval(() => {{
  if (autoRefresh.checked && tokenInput.value.trim()) {{
    loadPending();
  }}
}}, 10000);
if (idInput.value && tokenInput.value) {{
  loadConfirmation();
}} else if (tokenInput.value) {{
  loadPending();
}}
</script>
</body>
</html>
"""
    return render_legacy_page_in_shell(
        legacy_html=legacy_html,
        title="Code-KB Web Push Inbox",
        subtitle="IM主动推送接入前，所有需要当前用户确认的消息先进入这里。",
        active="confirmations",
        actions=(("/demo/current-user", "当前用户自测"), ("/hub", "返回工作台")),
        max_width="1040px",
    )
