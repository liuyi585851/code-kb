from __future__ import annotations

from .web_ui import render_legacy_page_in_shell


def render_current_user_demo_page() -> str:
    legacy_html = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Code-KB Current User Demo</title>
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
      max-width: 1120px;
      margin: 0 auto;
      padding: 24px 16px;
    }
    h1 { font-size: 24px; line-height: 1.25; margin: 0 0 16px; }
    h2 { font-size: 18px; line-height: 1.3; margin: 0 0 12px; }
    label { display: block; font-weight: 600; margin: 12px 0 6px; }
    input, select, textarea {
      width: 100%;
      min-height: 44px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px 12px;
      background: var(--surface);
      color: var(--text);
      font: inherit;
    }
    textarea { min-height: 88px; resize: vertical; }
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
    button.primary, a.button.primary {
      border-color: var(--primary);
      background: var(--primary);
      color: #ffffff;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(300px, 420px) 1fr;
      gap: 16px;
      align-items: start;
    }
    .panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      margin-top: 16px;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 16px;
    }
    .check {
      display: flex;
      align-items: center;
      color: var(--muted);
      margin-top: 12px;
    }
    .status { margin-top: 16px; min-height: 24px; overflow-wrap: anywhere; }
    .status.ok { color: var(--ok); }
    .status.error { color: var(--danger); }
    .status.warn { color: var(--warn); }
    .muted { color: var(--muted); }
    dl {
      display: grid;
      grid-template-columns: minmax(110px, 160px) 1fr;
      gap: 8px 12px;
      margin: 0;
    }
    dt { color: var(--muted); }
    dd { margin: 0; overflow-wrap: anywhere; }
    pre {
      min-height: 120px;
      max-height: 420px;
      overflow: auto;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      margin: 0;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--bg);
      color: var(--text);
    }
    .inbox-item {
      width: 100%;
      text-align: left;
      margin-top: 8px;
      display: block;
    }
    .inbox-meta {
      display: block;
      color: var(--muted);
      font-size: 13px;
    }
    @media (max-width: 860px) {
      .layout { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body data-ui-version="2">
<main class="app-shell">
  <h1>Code-KB Current User Demo</h1>
  <div class="layout">
    <section>
      <div class="panel">
        <h2>Current User</h2>
        <label for="auth-token">Current user token</label>
        <input id="auth-token" type="password" autocomplete="current-password">
        <div class="actions">
          <button class="primary" type="button" id="check-token">Check token</button>
          <button type="button" id="remember-token">Remember</button>
          <button type="button" id="forget-token">Forget</button>
          <a class="button" href="/auth/im/self-bindings/page">Self binding</a>
        </div>
      </div>
      <div class="panel">
        <h2>Use Case</h2>
        <label for="query">Diagnostic query</label>
        <input id="query" value="DEVICE_SEQ 是什么？">
        <label for="sub-kbs">Sub KBs</label>
        <input id="sub-kbs" value="testing">
        <label for="confirmation-reason">Confirmation reason</label>
        <select id="confirmation-reason">
          <option value="problem_solved">problem_solved</option>
          <option value="interaction_complete">interaction_complete</option>
          <option value="human_review_required">human_review_required</option>
          <option value="gap_candidate_review">gap_candidate_review</option>
        </select>
        <label for="confirmation-message">Confirmation message</label>
        <textarea id="confirmation-message">请确认本次 AI 交互是否完成，以及建议是否可采纳。</textarea>
        <div class="actions">
          <button class="primary" type="button" id="run-diagnose">Diagnose and push</button>
          <button type="button" id="push-only">Push message only</button>
          <button type="button" id="refresh-inbox">Refresh inbox</button>
        </div>
      </div>
      <div class="panel">
        <h2>Inbox</h2>
        <label class="check"><input id="include-responded" type="checkbox">Show responded messages</label>
        <label class="check"><input id="auto-refresh" type="checkbox" checked>Auto refresh</label>
        <dl>
          <dt>Total</dt><dd id="inbox-total">-</dd>
          <dt>Selected</dt><dd id="selected-id">-</dd>
          <dt>Last refresh</dt><dd id="last-refresh">-</dd>
        </dl>
        <div id="inbox-list" class="status muted"></div>
        <label for="response-comment">Response comment</label>
        <textarea id="response-comment"></textarea>
        <div class="actions">
          <button class="primary" type="button" data-decision="confirmed">Confirm</button>
          <button type="button" data-decision="needs_followup">Need follow-up</button>
          <button type="button" data-decision="rejected">Reject</button>
          <a class="button" href="/auth/im/confirmations/page">Open inbox page</a>
        </div>
      </div>
    </section>
    <section>
      <div class="panel">
        <h2>Token Status</h2>
        <dl>
          <dt>Status</dt><dd id="token-status">-</dd>
          <dt>Token ID</dt><dd id="token-id">-</dd>
          <dt>Expires</dt><dd id="expires-at">-</dd>
          <dt>Scopes</dt><dd id="scopes">-</dd>
        </dl>
      </div>
      <div class="panel">
        <h2>Latest Result</h2>
        <pre id="result">{}</pre>
      </div>
      <div class="status muted" id="status"></div>
    </section>
  </div>
</main>
<script>
const tokenKey = "codekb_user_token";
const tokenInput = document.getElementById("auth-token");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const inboxList = document.getElementById("inbox-list");
const includeResponded = document.getElementById("include-responded");
const autoRefresh = document.getElementById("auto-refresh");
let selectedConfirmationId = "";
tokenInput.value = localStorage.getItem(tokenKey) || "";
function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = "status " + (cls || "muted");
}
function value(id) {
  return document.getElementById(id).value.trim();
}
function renderResult(data) {
  resultEl.textContent = JSON.stringify(data || {}, null, 2);
}
function subKbs() {
  return value("sub-kbs").split(",").map((item) => item.trim()).filter(Boolean);
}
async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: {"Accept": "application/json", "Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || response.statusText);
  return data;
}
function tokenPayload() {
  const authToken = tokenInput.value.trim();
  if (!authToken) throw new Error("current user token is required");
  return authToken;
}
async function checkToken() {
  const authToken = tokenPayload();
  setStatus("Checking token...", "muted");
  const data = await postJson("/auth/im/current-user/status", {auth_token: authToken});
  const binding = data.binding || {};
  document.getElementById("token-status").textContent = data.status || "-";
  document.getElementById("token-id").textContent = binding.token_id || "-";
  document.getElementById("expires-at").textContent = binding.expires_at || "-";
  document.getElementById("scopes").textContent = (binding.scopes || []).join(", ") || "-";
  localStorage.setItem(tokenKey, authToken);
  renderResult(data);
  setStatus("Token is active", "ok");
}
async function runDiagnoseAndPush() {
  const authToken = tokenPayload();
  setStatus("Running diagnosis...", "muted");
  const data = await postJson("/diagnose", {
    auth_token: authToken,
    query: value("query"),
    sub_kbs: subKbs(),
    top_k: 4,
    include_governance: false,
    confirmation_policy: "always",
    confirmation_reason: value("confirmation-reason"),
    confirmation_message: value("confirmation-message"),
    confirmation_payload: {surface: "current_user_demo"},
  });
  renderResult(data);
  const confirmation = data.confirmation || {};
  if (confirmation.confirmation_id) {
    selectedConfirmationId = confirmation.confirmation_id;
    document.getElementById("selected-id").textContent = selectedConfirmationId;
  }
  await refreshInbox();
  setStatus("Diagnosis completed and confirmation pushed to web inbox", "ok");
}
async function pushOnly() {
  const authToken = tokenPayload();
  setStatus("Pushing message...", "muted");
  const data = await postJson("/auth/im/confirmations/request", {
    auth_token: authToken,
    reason: value("confirmation-reason"),
    message: value("confirmation-message"),
    payload: {surface: "current_user_demo", query: value("query"), sub_kbs: subKbs()},
  });
  renderResult(data);
  const confirmation = data.confirmation || {};
  selectedConfirmationId = confirmation.confirmation_id || "";
  document.getElementById("selected-id").textContent = selectedConfirmationId || "-";
  await refreshInbox();
  setStatus("Message pushed to web inbox", "ok");
}
async function refreshInbox() {
  const authToken = tokenPayload();
  const data = await postJson("/auth/im/confirmations/pending", {
    auth_token: authToken,
    limit: 50,
    include_responded: includeResponded.checked,
  });
  document.getElementById("inbox-total").textContent = String(data.total || 0);
  document.getElementById("last-refresh").textContent = new Date().toLocaleString();
  inboxList.textContent = "";
  const items = data.confirmations || [];
  if (!items.length) {
    inboxList.textContent = includeResponded.checked ? "No messages" : "No pending messages";
    return data;
  }
  for (const item of items) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "inbox-item";
    button.dataset.confirmationId = item.confirmation_id || "";
    const title = document.createElement("span");
    title.textContent = item.message || item.confirmation_id || "";
    const meta = document.createElement("span");
    meta.className = "inbox-meta";
    meta.textContent = [item.status || "", item.reason || "", item.created_at || ""].filter(Boolean).join(" | ");
    button.appendChild(title);
    button.appendChild(meta);
    button.addEventListener("click", async () => {
      selectedConfirmationId = button.dataset.confirmationId || "";
      document.getElementById("selected-id").textContent = selectedConfirmationId || "-";
      const detail = await postJson(`/auth/im/confirmations/${encodeURIComponent(selectedConfirmationId)}/detail`, {
        auth_token: tokenInput.value.trim(),
      });
      renderResult(detail);
      setStatus("Message selected", "ok");
    });
    inboxList.appendChild(button);
  }
  return data;
}
async function respond(decision) {
  const authToken = tokenPayload();
  if (!selectedConfirmationId) throw new Error("select a confirmation first");
  setStatus("Recording response...", "muted");
  const data = await postJson(`/auth/im/confirmations/${encodeURIComponent(selectedConfirmationId)}/response`, {
    auth_token: authToken,
    decision,
    comment: value("response-comment"),
    metadata: {source: "current_user_demo"},
  });
  renderResult(data);
  await refreshInbox();
  setStatus("Response recorded", "ok");
}
document.getElementById("check-token").addEventListener("click", () => checkToken().catch((error) => setStatus(error.message, "error")));
document.getElementById("remember-token").addEventListener("click", () => {
  localStorage.setItem(tokenKey, tokenInput.value.trim());
  setStatus("Token saved in this browser", "ok");
});
document.getElementById("forget-token").addEventListener("click", () => {
  localStorage.removeItem(tokenKey);
  tokenInput.value = "";
  setStatus("Token removed", "ok");
});
document.getElementById("run-diagnose").addEventListener("click", () => runDiagnoseAndPush().catch((error) => setStatus(error.message, "error")));
document.getElementById("push-only").addEventListener("click", () => pushOnly().catch((error) => setStatus(error.message, "error")));
document.getElementById("refresh-inbox").addEventListener("click", () => refreshInbox().catch((error) => setStatus(error.message, "error")));
includeResponded.addEventListener("change", () => {
  if (tokenInput.value.trim()) refreshInbox().catch((error) => setStatus(error.message, "error"));
});
document.querySelectorAll("[data-decision]").forEach((button) => {
  button.addEventListener("click", () => respond(button.dataset.decision).catch((error) => setStatus(error.message, "error")));
});
setInterval(() => {
  if (autoRefresh.checked && tokenInput.value.trim()) {
    refreshInbox().catch((error) => setStatus(error.message, "error"));
  }
}, 10000);
if (tokenInput.value) {
  checkToken().then(refreshInbox).catch((error) => setStatus(error.message, "error"));
}
</script>
</body>
</html>
"""
    return render_legacy_page_in_shell(
        legacy_html=legacy_html,
        title="Code-KB Current User Demo",
        subtitle="验证当前用户 token、发起诊断并把确认消息推送到网页收件箱。",
        active="current-user",
        actions=(("/auth/im/self-bindings/page", "自助绑定"), ("/auth/im/confirmations/page", "确认收件箱")),
        max_width="1180px",
    )
