from __future__ import annotations

from .web_ui import render_legacy_page_in_shell


def render_audit_page() -> str:
    legacy_html = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Code-KB Audit Queue</title>
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
    h3 {
      font-size: 16px;
      line-height: 1.35;
      margin: 0 0 8px;
    }
    label {
      display: block;
      font-weight: 600;
      margin: 12px 0 6px;
    }
    input, select, textarea {
      width: 100%;
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      background: var(--surface);
      color: var(--text);
      font: inherit;
    }
    textarea {
      min-height: 130px;
      resize: vertical;
      white-space: pre-wrap;
    }
    button {
      min-height: 38px;
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
    button.danger {
      border-color: var(--danger);
      color: var(--danger);
    }
    button:disabled {
      cursor: not-allowed;
      opacity: 0.55;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(280px, 360px) 1fr;
      gap: 16px;
      align-items: start;
    }
    .panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }
    .stack {
      display: grid;
      gap: 16px;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 14px;
    }
    .check {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-weight: 500;
      margin-top: 12px;
    }
    input[type="checkbox"] {
      width: 18px;
      min-height: 18px;
      margin: 0;
    }
    .queue {
      display: grid;
      gap: 8px;
      max-height: 520px;
      overflow: auto;
      padding-right: 2px;
    }
    .queue-item {
      width: 100%;
      text-align: left;
    }
    .queue-item.active {
      border-color: var(--primary);
      box-shadow: inset 3px 0 0 var(--primary);
    }
    .item-title {
      display: block;
      font-weight: 700;
      overflow-wrap: anywhere;
    }
    .item-meta {
      display: block;
      color: var(--muted);
      font-size: 13px;
      overflow-wrap: anywhere;
    }
    .summary {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 8px 16px;
    }
    dl {
      display: grid;
      grid-template-columns: minmax(110px, 150px) 1fr;
      gap: 8px 12px;
      margin: 0;
    }
    dt { color: var(--muted); }
    dd { margin: 0; overflow-wrap: anywhere; }
    pre {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      margin: 0;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--bg);
    }
    table {
      width: 100%;
      border-collapse: collapse;
    }
    th, td {
      border-top: 1px solid var(--line);
      padding: 8px;
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }
    th {
      color: var(--muted);
      font-weight: 600;
    }
    .muted { color: var(--muted); }
    .ok { color: var(--ok); }
    .warn { color: var(--warn); }
    .error { color: var(--danger); }
    .status {
      min-height: 24px;
      margin-top: 12px;
    }
    @media (max-width: 860px) {
      .layout {
        grid-template-columns: 1fr;
      }
      dl {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body data-ui-version="2">
<main class="app-shell">
  <h1>Code-KB Audit Queue</h1>
  <section class="panel">
    <div class="summary">
      <div><span class="muted">Index status</span><br><span id="index-status">-</span></div>
      <div><span class="muted">Sources</span><br><span id="index-sources">-</span></div>
      <div><span class="muted">Atoms</span><br><span id="index-atoms">-</span></div>
      <div><span class="muted">Updated</span><br><span id="last-refresh">-</span></div>
    </div>
  </section>
  <div class="layout" style="margin-top:16px">
    <aside class="panel">
      <h2>Queue</h2>
      <label for="queue-status">Status</label>
      <select id="queue-status">
        <option value="pending_review">pending_review</option>
        <option value="needs_revision">needs_revision</option>
        <option value="approved">approved</option>
        <option value="rejected">rejected</option>
      </select>
      <label for="reviewer-hash">Reviewer hash</label>
      <input id="reviewer-hash" autocomplete="off">
      <label class="check"><input id="rebuild-index" type="checkbox">rebuild_index on approve</label>
      <div class="actions">
        <button class="primary" type="button" id="refresh-queue">Refresh</button>
        <button type="button" id="remember-reviewer">Remember</button>
      </div>
      <div class="status muted" id="queue-status-text"></div>
      <div class="queue" id="queue"></div>
    </aside>
    <div class="stack">
      <section class="panel">
        <h2>Candidate</h2>
        <dl>
          <dt>ID</dt><dd id="candidate-id">-</dd>
          <dt>Status</dt><dd id="candidate-status">-</dd>
          <dt>Sub KB</dt><dd id="candidate-subkb">-</dd>
          <dt>Created</dt><dd id="candidate-created">-</dd>
          <dt>Source</dt><dd id="candidate-source">-</dd>
          <dt>Metadata</dt><dd><pre id="candidate-metadata">-</pre></dd>
        </dl>
        <label for="candidate-title">Title</label>
        <input id="candidate-title">
        <label for="candidate-content">Content</label>
        <textarea id="candidate-content"></textarea>
        <label for="audit-comment">Comment</label>
        <textarea id="audit-comment" style="min-height:74px"></textarea>
        <div class="actions">
          <button class="primary" type="button" id="approve">Approve</button>
          <button type="button" id="request-revision">Request revision</button>
          <button class="danger" type="button" id="reject">Reject</button>
          <button type="button" id="submit-revision">Submit revision</button>
        </div>
        <div class="status muted" id="detail-status"></div>
      </section>
      <section class="panel">
        <h2>Audit History</h2>
        <table>
          <thead>
            <tr><th>Time</th><th>Action</th><th>Reviewer</th><th>Comment</th></tr>
          </thead>
          <tbody id="audit-history">
            <tr><td colspan="4" class="muted">No candidate selected</td></tr>
          </tbody>
        </table>
      </section>
    </div>
  </div>
</main>
<script>
const reviewerKey = "codekb_reviewer_hash";
const queueEl = document.getElementById("queue");
const queueStatus = document.getElementById("queue-status");
const queueStatusText = document.getElementById("queue-status-text");
const detailStatus = document.getElementById("detail-status");
const reviewerHash = document.getElementById("reviewer-hash");
const rebuildIndex = document.getElementById("rebuild-index");
let selectedCandidateId = "";
let selectedCandidate = null;
let lastQueueItems = [];
reviewerHash.value = localStorage.getItem(reviewerKey) || "";

function setText(id, value) {
  document.getElementById(id).textContent = value === undefined || value === null || value === "" ? "-" : String(value);
}

function setStatus(element, text, cls) {
  element.textContent = text;
  element.className = "status " + (cls || "muted");
}

async function fetchJson(url) {
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || response.statusText);
  return data;
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

async function refreshIndexStatus() {
  try {
    const data = await fetchJson("/index/status");
    setText("index-status", data.status || "-");
    setText("index-sources", data.sources || data.source_count || "-");
    setText("index-atoms", data.atoms || data.atom_count || "-");
    setText("last-refresh", new Date().toLocaleString());
  } catch (error) {
    setText("index-status", error.message);
  }
}

function renderQueue(items) {
  lastQueueItems = items;
  queueEl.textContent = "";
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "No candidates";
    queueEl.appendChild(empty);
    return;
  }
  items.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "queue-item" + (item.candidate_id === selectedCandidateId ? " active" : "");
    button.addEventListener("click", () => loadDetail(item.candidate_id));
    const title = document.createElement("span");
    title.className = "item-title";
    title.textContent = item.title || item.candidate_id;
    const meta = document.createElement("span");
    meta.className = "item-meta";
    meta.textContent = [item.candidate_id, item.sub_kb_id, item.created_at].filter(Boolean).join(" | ");
    button.appendChild(title);
    button.appendChild(meta);
    queueEl.appendChild(button);
  });
}

async function refreshQueue() {
  setStatus(queueStatusText, "Loading...", "muted");
  try {
    const data = await fetchJson(`/audit/queue?status=${encodeURIComponent(queueStatus.value)}&limit=100`);
    renderQueue(data.candidates || []);
    setStatus(queueStatusText, `${(data.candidates || []).length} candidates`, "ok");
  } catch (error) {
    setStatus(queueStatusText, error.message, "error");
  }
}

function renderDetail(payload) {
  const candidate = payload.candidate || {};
  selectedCandidate = candidate;
  selectedCandidateId = candidate.candidate_id || "";
  setText("candidate-id", candidate.candidate_id);
  setText("candidate-status", candidate.status);
  setText("candidate-subkb", candidate.sub_kb_id);
  setText("candidate-created", candidate.created_at);
  setText("candidate-source", JSON.stringify(candidate.source || {}));
  setText("candidate-metadata", JSON.stringify(candidate.metadata || {}, null, 2));
  document.getElementById("candidate-title").value = candidate.title || "";
  document.getElementById("candidate-content").value = candidate.content || "";
  renderAudits(payload.audits || []);
  renderQueue(lastQueueItems);
}

function renderAudits(audits) {
  const body = document.getElementById("audit-history");
  body.textContent = "";
  if (!audits.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.className = "muted";
    cell.textContent = "No audit history";
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }
  audits.forEach((audit) => {
    const row = document.createElement("tr");
    [audit.created_at, audit.action, audit.reviewer_hash || audit.submitted_by_hash, audit.comment].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value || "";
      row.appendChild(cell);
    });
    body.appendChild(row);
  });
}

async function loadDetail(candidateId) {
  selectedCandidateId = candidateId;
  setStatus(detailStatus, "Loading...", "muted");
  try {
    const data = await fetchJson(`/ingest/candidates/${encodeURIComponent(candidateId)}`);
    renderDetail(data);
    await refreshQueue();
    setStatus(detailStatus, "Loaded", "ok");
  } catch (error) {
    setStatus(detailStatus, error.message, "error");
  }
}

function auditPayload(action) {
  const reviewer = reviewerHash.value.trim();
  if (!reviewer) throw new Error("reviewer_hash is required");
  return {
    action,
    reviewer_hash: reviewer,
    comment: document.getElementById("audit-comment").value.trim(),
    rebuild_index: rebuildIndex.checked,
  };
}

async function submitAudit(action) {
  if (!selectedCandidateId) {
    setStatus(detailStatus, "Select a candidate first", "error");
    return;
  }
  try {
    setStatus(detailStatus, "Submitting...", "muted");
    const data = await postJson(`/audit/${encodeURIComponent(selectedCandidateId)}`, auditPayload(action));
    selectedCandidate = data.candidate;
    await loadDetail(selectedCandidateId);
    await refreshIndexStatus();
    setStatus(detailStatus, `${data.status}${data.index_rebuild ? " / index " + data.index_rebuild.status : ""}`, "ok");
  } catch (error) {
    setStatus(detailStatus, error.message, "error");
  }
}

async function submitRevision() {
  if (!selectedCandidateId || !selectedCandidate) {
    setStatus(detailStatus, "Select a candidate first", "error");
    return;
  }
  try {
    setStatus(detailStatus, "Submitting revision...", "muted");
    const payload = {
      title: document.getElementById("candidate-title").value.trim(),
      content: document.getElementById("candidate-content").value.trim(),
      submitted_by_hash: reviewerHash.value.trim() || "curator",
      comment: document.getElementById("audit-comment").value.trim(),
      metadata: selectedCandidate.metadata || {},
    };
    await postJson(`/ingest/candidates/${encodeURIComponent(selectedCandidateId)}/revision`, payload);
    await loadDetail(selectedCandidateId);
    setStatus(detailStatus, "Revision submitted", "ok");
  } catch (error) {
    setStatus(detailStatus, error.message, "error");
  }
}

document.getElementById("refresh-queue").addEventListener("click", refreshQueue);
document.getElementById("remember-reviewer").addEventListener("click", () => {
  localStorage.setItem(reviewerKey, reviewerHash.value.trim());
  setStatus(queueStatusText, "Reviewer saved", "ok");
});
queueStatus.addEventListener("change", refreshQueue);
document.getElementById("approve").addEventListener("click", () => submitAudit("approve"));
document.getElementById("request-revision").addEventListener("click", () => submitAudit("request_revision"));
document.getElementById("reject").addEventListener("click", () => submitAudit("reject"));
document.getElementById("submit-revision").addEventListener("click", submitRevision);
refreshIndexStatus();
refreshQueue();
</script>
</body>
</html>
"""
    return render_legacy_page_in_shell(
        legacy_html=legacy_html,
        title="Code-KB Audit Queue",
        subtitle="处理低置信和缺口候选，完成审核、修订和索引刷新。",
        active="audit",
        actions=(("/index/status", "索引状态"), ("/hub", "返回工作台")),
        max_width="1240px",
    )
