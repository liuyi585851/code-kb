from __future__ import annotations

from .web_ui import render_legacy_page_in_shell


def render_webhook_demo_page() -> str:
    legacy_html = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Code-KB Webhook Demo</title>
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
      max-width: 1180px;
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
    textarea {
      min-height: 360px;
      resize: vertical;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 13px;
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
    button.primary, a.button.primary {
      border-color: var(--primary);
      background: var(--primary);
      color: #ffffff;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(340px, 520px) 1fr;
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
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 8px 12px;
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
    pre {
      min-height: 360px;
      max-height: 640px;
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
    @media (max-width: 900px) {
      .layout { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body data-ui-version="2">
<main class="app-shell">
  <h1>Code-KB Webhook Demo</h1>
  <div class="layout">
    <section>
      <div class="panel">
        <h2>Webhook Input</h2>
        <div class="grid">
          <div>
            <label for="source">Source</label>
            <select id="source">
              <option value="code_review">code_review</option>
              <option value="ci">ci</option>
              <option value="mr">mr</option>
              <option value="issue_tracker">issue_tracker</option>
              <option value="crash">crash</option>
              <option value="generic">generic</option>
            </select>
          </div>
          <div>
            <label for="webhook-token">Webhook shared token</label>
            <input id="webhook-token" type="password" autocomplete="off">
          </div>
          <div>
            <label for="auth-token">Current user token</label>
            <input id="auth-token" type="password" autocomplete="current-password">
          </div>
          <div>
            <label for="confirmation-policy">Confirmation policy</label>
            <select id="confirmation-policy">
              <option value="never">never</option>
              <option value="needs_review">needs_review</option>
              <option value="always">always</option>
            </select>
          </div>
          <div>
            <label for="confirmation-reason">Confirmation reason</label>
            <select id="confirmation-reason">
              <option value="human_review_required">human_review_required</option>
              <option value="problem_solved">problem_solved</option>
              <option value="interaction_complete">interaction_complete</option>
              <option value="gap_candidate_review">gap_candidate_review</option>
            </select>
          </div>
        </div>
        <label class="check"><input id="remember-token" type="checkbox" checked>Remember current user token in this browser</label>
        <label for="payload">Payload JSON</label>
        <textarea id="payload" spellcheck="false"></textarea>
        <div class="actions">
          <button type="button" id="load-sample">Load sample</button>
          <button type="button" id="normalize">Normalize</button>
          <button type="button" id="validate">Validate</button>
          <button class="primary" type="button" id="diagnose">Diagnose</button>
          <button type="button" id="gap-candidate">Submit gap candidate</button>
          <a class="button" href="/demo/current-user">Current user demo</a>
          <a class="button" href="/auth/im/confirmations/page">Web inbox</a>
        </div>
      </div>
    </section>
    <section>
      <div class="panel">
        <h2>Result</h2>
        <pre id="result">{}</pre>
      </div>
      <div class="status muted" id="status"></div>
    </section>
  </div>
</main>
<script>
const tokenKey = "codekb_user_token";
const sourceInput = document.getElementById("source");
const payloadInput = document.getElementById("payload");
const authTokenInput = document.getElementById("auth-token");
const webhookTokenInput = document.getElementById("webhook-token");
const resultEl = document.getElementById("result");
const statusEl = document.getElementById("status");
authTokenInput.value = localStorage.getItem(tokenKey) || "";
const samples = {
  code_review: {
    code_review: {
      repo_path: "ym/app",
      branch: "feature/udt",
      pipeline: {id: "cb-build-001", url: "https://code_review.example/build/1?access_token=example-token"},
      job: {name: "udt-ci"},
      error: {code: "DEVICE_SEQ", message: "DEVICE_SEQ 构建失败 password=example-password"},
      log_tail: "missing DEVICE_SEQ",
      event_name: "build_failed",
      kb_area: ["testing"]
    }
  },
  ci: {
    ci: {
      repository: {path: "ym/app"},
      branch: "main",
      pipeline: {id: "pipeline-456", url: "https://ci.example/pipeline/456?token=example-token"},
      job: {name: "udt-ci", log: "{\\"client_secret\\":\\"example-client-secret\\",\\"message\\":\\"missing DEVICE_SEQ\\"}"},
      failure: {code: "DEVICE_SEQ", message: "DEVICE_SEQ 参数缺失"},
      event: "pipeline_failed"
    },
    sub_kbs: ["testing"]
  },
  mr: {
    mr: {
      repository: {path: "ym/app"},
      source_branch: "feature/device-seq",
      iid: "123",
      title: "DEVICE_SEQ MR 检查失败",
      description: "signature=example-signature",
      web_url: "https://git.example/ym/app/merge_requests/123?signature=example-signature",
      event: "merge_request"
    },
    sub_kbs: ["release"]
  },
  issue_tracker: {
    issue_tracker: {
      project: {name: "ym/app"},
      bug: {
        id: "BUG-123",
        title: "DEVICE_SEQ 构建失败",
        description: "{\\"user_ticket\\":\\"example-ticket\\",\\"detail\\":\\"DEVICE_SEQ\\"}",
        url: "https://issue_tracker.example/bug/123?token=example-token"
      },
      priority: "P1",
      status: "new"
    },
    sub_kbs: ["testing"]
  },
  crash: {
    crash: {
      app: "ym/app",
      version: "1.2.3",
      issue_id: "CRASH-9",
      summary: "DEVICE_SEQ 相关崩溃",
      stack_trace: "Authorization: Bearer example-bearer\\ncorpsecret=example-corp-secret\\nDEVICE_SEQ",
      url: "https://crash.example/issue/9?corpsecret=example-corp-secret",
      platform: "android",
      severity: "P1"
    },
    sub_kbs: ["testing"]
  },
  generic: {
    context: {
      repo: "ym/app",
      error_code: "DEVICE_SEQ",
      error_text: "DEVICE_SEQ 手工反馈",
      log_excerpt: "--refresh-token example-refresh-token"
    },
    tags: "manual,qa",
    sub_kbs: ["testing"]
  }
};
function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = "status " + (cls || "muted");
}
function renderResult(data) {
  resultEl.textContent = JSON.stringify(data || {}, null, 2);
}
function loadSample() {
  payloadInput.value = JSON.stringify(samples[sourceInput.value] || samples.generic, null, 2);
  renderResult({});
  setStatus("Sample loaded", "ok");
}
function parsePayload() {
  const text = payloadInput.value.trim();
  if (!text) throw new Error("payload JSON is required");
  const payload = JSON.parse(text);
  if (!payload || Array.isArray(payload) || typeof payload !== "object") throw new Error("payload must be a JSON object");
  return payload;
}
function payloadWithConfirmation() {
  const payload = parsePayload();
  const policy = document.getElementById("confirmation-policy").value;
  const authToken = authTokenInput.value.trim();
  if (policy !== "never") {
    if (!authToken) throw new Error("current user token is required when confirmation policy is enabled");
    payload.auth_token = authToken;
    payload.confirmation_policy = policy;
    payload.confirmation_reason = document.getElementById("confirmation-reason").value;
    payload.confirmation_message = "请确认该 webhook 诊断结果是否需要处理";
    payload.confirmation_payload = {surface: "webhook_demo", source: sourceInput.value};
  }
  if (authToken && document.getElementById("remember-token").checked) {
    localStorage.setItem(tokenKey, authToken);
  }
  return payload;
}
async function postJson(path, payload) {
  const headers = {"Accept": "application/json", "Content-Type": "application/json"};
  const sharedToken = webhookTokenInput.value.trim();
  if (sharedToken) headers["X-CodeKB-Token"] = sharedToken;
  const response = await fetch(path, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || response.statusText);
  return data;
}
async function runAction(action) {
  const source = sourceInput.value;
  const payload = action === "diagnose" || action === "gap-candidate" ? payloadWithConfirmation() : parsePayload();
  const suffix = action === "diagnose" ? "" : "/" + action;
  setStatus(action + " running...", "muted");
  const data = await postJson(`/diagnose/webhook/${encodeURIComponent(source)}${suffix}`, payload);
  renderResult(data);
  const confirmation = data.confirmation || {};
  if (confirmation.confirmation_id) {
    setStatus(action + " done; confirmation queued: " + confirmation.confirmation_id, "ok");
  } else {
    setStatus(action + " done", "ok");
  }
}
sourceInput.addEventListener("change", loadSample);
document.getElementById("load-sample").addEventListener("click", loadSample);
document.getElementById("normalize").addEventListener("click", () => runAction("normalize").catch((error) => setStatus(error.message, "error")));
document.getElementById("validate").addEventListener("click", () => runAction("validate").catch((error) => setStatus(error.message, "error")));
document.getElementById("diagnose").addEventListener("click", () => runAction("diagnose").catch((error) => setStatus(error.message, "error")));
document.getElementById("gap-candidate").addEventListener("click", () => runAction("gap-candidate").catch((error) => setStatus(error.message, "error")));
loadSample();
</script>
</body>
</html>
"""
    return render_legacy_page_in_shell(
        legacy_html=legacy_html,
        title="Code-KB Webhook Demo",
        subtitle="模拟外部研发事件，验证 normalize、validate、diagnose 和 gap candidate 链路。",
        active="webhook",
        actions=(("/demo/current-user", "当前用户自测"), ("/auth/im/confirmations/page", "确认收件箱")),
        max_width="1240px",
    )
