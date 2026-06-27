"use strict";
/* Code-KB 控制台 —— 基于 hash 路由的单页应用,对接 JSON API。 */

const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; };
const view = $("#view");
const state = { mode: "ask", ask: null, code: null };

const API = {
  async get(p, headers) { return resp(await fetch(p, { headers: { accept: "application/json", "x-codekb-source": "web", ...(headers || {}) }, cache: "no-store" })); },
  async post(p, b, headers) { return resp(await fetch(p, { method: "POST", headers: { "content-type": "application/json", "x-codekb-source": "web", ...(headers || {}) }, body: JSON.stringify(b || {}) })); },
};
async function resp(r) {
  let d = null; try { d = await r.json(); } catch (_) {}
  if (!r.ok) { const e = new Error((d && (d.detail || d.message)) || ("加载失败 (HTTP " + r.status + ")")); e.data = d; throw e; }
  return d ?? {};
}
const adminHdr = () => { const t = (localStorage.getItem("kb_admin_token") || "").trim(); return t ? { "x-codekb-admin-token": t } : {}; };
const webhookHdr = () => { const t = (localStorage.getItem("kb_webhook_token") || "").trim(); return t ? { "x-codekb-token": t } : {}; };
const say = (m) => { const s = document.getElementById("status"); if (s) s.textContent = m || ""; };
const focusH1 = () => { const h = view.querySelector("h1"); if (h) { h.setAttribute("tabindex", "-1"); h.focus({ preventScroll: false }); } };
const loading = (t) => `<div class="loading fade"><span class="spin"></span>${esc(t || "加载中…")}</div>`;
const errBox = (e) => `<div class="err fade">出错了：${esc(e.message || e)}</div>`;
const empty = (big, sub) => `<div class="empty fade"><div class="big">${esc(big)}</div><div class="muted">${esc(sub || "")}</div></div>`;
const pageH = (eyebrow, title, sub, actions) =>
  `<div class="page-h"><div><span class="eyebrow">${esc(eyebrow)}</span><h1>${esc(title)}</h1>${sub ? `<p>${esc(sub)}</p>` : ""}</div><div class="row">${actions || ""}</div></div>`;
const num = (n) => (n == null ? "—" : (typeof n === "number" ? (Number.isInteger(n) ? n.toLocaleString() : n.toFixed(2)) : n));

/* 递归友好渲染:把 API 对象渲染成键值列表 / 表格,而不是裸 JSON */
function tableOf(arr) {
  const keys = [...new Set(arr.flatMap((o) => (o && typeof o === "object" ? Object.keys(o) : [])))].slice(0, 8);
  if (!keys.length) return arr.map((x) => `<span class="badge">${esc(x)}</span>`).join(" ");
  const body = arr.slice(0, 60).map((o) => `<tr>${keys.map((k) => {
    const val = (o || {})[k];
    return `<td>${val && typeof val === "object" ? friendly(val) : (typeof val === "string" ? esc(val) : (val == null ? "" : esc(val)))}</td>`;
  }).join("")}</tr>`).join("");
  return `<table><thead><tr>${keys.map((k) => `<th>${esc(k)}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table>`
    + (arr.length > 60 ? `<div class="muted" style="margin-top:6px">…共 ${arr.length} 条</div>` : "");
}
function friendly(v) {
  if (v == null || v === "") return '<span class="muted">—</span>';
  if (Array.isArray(v)) return v.length ? (typeof v[0] === "object" && v[0] !== null ? tableOf(v) : v.map((x) => `<span class="badge">${esc(x)}</span>`).join(" ")) : '<span class="muted">空</span>';
  if (typeof v === "object") return `<dl class="kv">${Object.entries(v).map(([k, val]) => `<dt>${esc(k)}</dt><dd>${friendly(val)}</dd>`).join("")}</dl>`;
  if (typeof v === "boolean") return v ? '<span class="badge ok">是</span>' : '<span class="badge">否</span>';
  if (typeof v === "number") return `<span class="mono">${num(v)}</span>`;
  return esc(v);
}
const raw = (v) => `<details style="margin-top:10px"><summary style="cursor:pointer;color:var(--muted);font-size:12.5px">查看原始 JSON</summary><pre style="margin-top:8px">${esc(JSON.stringify(v, null, 2))}</pre></details>`;

const IC = {
  ask: '<path d="M12 3l1.7 4.8L18 9.5l-4.3 1.7L12 16l-1.7-4.8L6 9.5l4.3-1.7z"/>',
  code: '<path d="M8 6l-5 6 5 6M16 6l5 6-5 6"/>',
  review: '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
  diagnose: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  govern: '<path d="M3 3v18h18"/><path d="M7 14l3-3 3 3 5-6"/>',
  system: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
  usage: '<path d="M3 12h4l3 8 4-16 3 8h4"/>',
  storage: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  integrations: '<path d="M20 6H10a4 4 0 100 8h4a4 4 0 110 8H4"/>',
};
const svg = (k) => `<svg class="ic" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${IC[k] || IC.system}</svg>`;
const NAV = [
  ["ask", "问答"], ["code", "代码"], ["review", "审核"],
  ["diagnose", "诊断"], ["govern", "治理"], ["usage", "观测"], ["system", "系统"], ["storage", "存储"], ["integrations", "接入"],
];

/* ---------- 外壳装配 ---------- */
function buildNav() {
  $("#nav").innerHTML = NAV.map(([id, label]) =>
    `<a href="#/${id}" data-id="${id}" aria-label="${label}">${svg(id)}<span class="lbl-t">${label}</span></a>`).join("");
}
function setActive(id) {
  document.querySelectorAll("#nav a").forEach((a) => {
    const on = a.dataset.id === id;
    a.classList.toggle("active", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
}
const cmdInput = $("#cmd-input");
function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll("#cmd-modes button").forEach((x) => {
    const on = x.dataset.mode === mode;
    x.classList.toggle("on", on);
    x.setAttribute("aria-pressed", on ? "true" : "false");
  });
  cmdInput.placeholder = mode === "code" ? "搜代码 / 符号 / 错误码…  （回车）" : "问点什么…  （回车）";
}
$("#cmd-modes").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) setMode(b.dataset.mode); });
cmdInput.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const q = cmdInput.value.trim(); if (!q) return;
  if (state.mode === "code") { location.hash = "#/code"; runCode(q); }
  else { location.hash = "#/ask"; runAsk(q); }
});

/* ---------- 健康检查 ---------- */
async function pollHealth() {
  try {
    const d = await API.get("/healthz");
    const ok = d.status === "ok";
    $("#health-dot").className = "dot " + (ok ? "ok" : "warn");
    $("#health-text").textContent = ok ? "系统在线" : "降级运行";
  } catch (_) { $("#health-dot").className = "dot bad"; $("#health-text").textContent = "不可达"; }
}

/* ===================== 视图 ===================== */
function badge(text, cls) { return `<span class="badge ${cls || ""}">${esc(text)}</span>`; }

/* ---- 问答 ---- */
async function runAsk(q) {
  cmdInput.value = q; setActive("ask");
  view.innerHTML = pageH("智能问答", "问答", "基于权威知识返回带引用的可信答案。") + loading("正在检索并生成带引用的答案…");
  try {
    const d = await API.post("/ask", { query: q, sub_kbs: ["code", "docs", "testing", "release", "incident"], top_k: 4 });
    state.ask = { q, d };
    renderAsk();
  } catch (e) { view.querySelector(".loading").outerHTML = errBox(e); }
}
function renderAsk() {
  const { q, d } = state.ask;
  const refs = (t) => esc(t).replace(/\[(\d+)\]/g, "<sup>[$1]</sup>");
  let html = pageH("智能问答", "问答", q);
  if (d.refused) {
    html += `<div class="answer fade"><div class="meta">${badge("暂无可信答案", "warn")}${badge("已记录为知识缺口", "")}</div>
      <div class="body">没有足够的权威引用来回答。原因：${esc(d.refusal_reason || "未命中相关知识")}</div>${fbBlock(d)}</div>`;
  } else {
    const cites = (d.citations || []).map((c, i) => {
      const loc = c.file_path ? `${esc(c.file_path)}:L${c.start_line}-${c.end_line}` : esc(c.docid || "");
      const title = esc(c.title || c.symbol || c.docid || "");
      return `<div class="cite"><span class="n">${i + 1}</span><div><div class="ttl">${title}</div>
        <div class="loc">${loc}${c.language ? " · " + esc(c.language) : ""}</div>
        <pre>${esc(c.quote || "")}</pre></div></div>`;
    }).join("");
    const cv = Math.round((d.confidence || 0) * 100);
    html += `<div class="answer fade"><div class="meta">
        ${d.generation_mode === "generative" ? badge("AI 生成", "spark") : badge("原文摘录", "")}
        ${badge("置信度 " + cv + "%", cv >= 60 ? "ok" : "warn")}
        ${d.model ? badge(d.model, "cyan") : ""}${badge(Math.round(d.latency_ms || 0) + " ms", "")}</div>
      <div class="body">${refs(d.answer || "")}</div>
      ${cites ? `<div class="cites"><h4>引用来源 · ${d.citations.length}</h4>${cites}</div>` : ""}
      ${fbBlock(d)}</div>`;
  }
  view.innerHTML = html;
  bindFeedback(d); focusH1();
}
function fbBlock() { return `<div class="fb"><span class="muted">这个答案有用吗？</span>
  <button class="btn sm fb-up">👍 有用</button><button class="btn sm fb-down">👎 没用</button><span class="muted fb-done"></span></div>`; }
function bindFeedback(d) {
  const box = view.querySelector(".fb"); if (!box) return;
  const send = async (rating, reason) => {
    try {
      await API.post("/feedback", { answer_id: d.answer_id, trace_id: d.trace_id, rating, reason: reason || "" });
      box.querySelector(".fb-done").textContent = "✓ 已记录，感谢反馈"; say("反馈已记录");
      box.querySelectorAll("button").forEach((b) => (b.disabled = true));
    } catch (e) { box.querySelector(".fb-done").textContent = "提交失败"; }
  };
  box.querySelector(".fb-up").onclick = () => send(1);
  box.querySelector(".fb-down").onclick = () => send(-1, prompt("哪里不对？（可选）") || "");
}
function viewAsk() {
  setActive("ask"); setMode("ask");
  if (state.ask) { renderAsk(); return; }
  const chips = ["AuthSDK 1001 错误码是什么含义", "登录态校验在哪里实现", "多机联调要注意什么"];
  view.innerHTML = pageH("智能问答", "问答", "在上方输入问题，获取带 file:line 引用的答案。无法回答时会记录为知识缺口。")
    + `<div class="card fade"><h3>试试这些</h3><div class="chips">${chips.map((c) => `<button class="chip">${esc(c)}</button>`).join("")}</div></div>`;
  view.querySelectorAll(".chip").forEach((c) => (c.onclick = () => runAsk(c.textContent)));
  focusH1();
}

/* ---- 代码 ---- */
async function runCode(q) {
  cmdInput.value = q; setActive("code"); setMode("code");
  view.innerHTML = pageH("代码检索", "代码", q) + loading("检索代码片段…");
  try {
    const d = await API.post("/code/search", { query: q, top_k: 8 });
    state.code = { q, d }; renderCode();
  } catch (e) { view.querySelector(".loading").outerHTML = errBox(e); }
}
function renderCode() {
  const { q, d } = state.code;
  const hits = (d.hits || []).map((h) => {
    const loc = h.file_path ? `${esc(h.file_path)}:L${h.start_line}-${h.end_line}` : esc(h.docid || "");
    return `<div class="card fade" style="margin-bottom:12px">
      <div class="row" style="justify-content:space-between">
        <div class="loc" style="color:var(--cyan);font-family:var(--mono);font-size:12.5px">${loc}</div>
        <div class="row">${h.language ? badge(h.language, "cyan") : ""}${h.symbol ? badge(h.symbol, "spark") : ""}${badge("分 " + (Number(h.score) || 0).toFixed(2), "")}</div>
      </div>
      <pre style="margin-top:10px">${esc((h.snippet || "").slice(0, 1400))}</pre>
      ${h.file_path ? `<div class="row" style="margin-top:10px"><button class="btn sm ghost out" data-p="${esc(h.file_path)}">文件结构</button></div>` : ""}
    </div>`;
  }).join("");
  view.innerHTML = pageH("代码检索", "代码", q) + (hits || empty("无匹配", "换个关键词，或用具体符号/错误码"));
  view.querySelectorAll(".out").forEach((b) => (b.onclick = () => outline(b.dataset.p, b)));
  focusH1();
}
async function outline(path, btn) {
  btn.disabled = true; btn.textContent = "加载中…";
  try {
    const d = await API.post("/code/outline", { path });
    const rows = (d.symbols || []).map((s) => `<tr><td class="mono">L${s.start_line}-${s.end_line}</td><td class="mono">${esc(s.symbol || "")}</td></tr>`).join("");
    const card = btn.closest(".card");
    card.insertAdjacentHTML("beforeend", `<div class="card fade" style="margin-top:10px;background:var(--surface-2)"><h3>${esc(path)} · ${d.count} 符号</h3><table><thead><tr><th>行</th><th>符号</th></tr></thead><tbody>${rows || ""}</tbody></table></div>`);
    btn.remove();
  } catch (e) { btn.textContent = "失败"; }
}
function viewCode() {
  setActive("code"); setMode("code");
  if (state.code) { renderCode(); return; }
  view.innerHTML = pageH("代码检索", "代码", "搜大仓源码：自然语言、符号名或错误码。命中返回 repo/file:line + 自描述片段。")
    + empty("在上方搜索代码", "例：AuthSDKError、ace_sdk result code、LoginModel:Fire");
  focusH1();
}

/* ---- 审核(候选知识队列) ---- */
async function viewReview() {
  setActive("review");
  view.innerHTML = pageH("知识沉淀", "审核队列", "候选知识的审核与发布前确认。") + loading();
  try {
    const d = await API.get("/audit/queue?status=pending_review&limit=50");
    const items = d.candidates || [];
    if (!items.length) { view.innerHTML = pageH("知识沉淀", "审核队列", "候选知识的审核与发布前确认。") + empty("队列为空", "暂无待审候选知识"); return; }
    const rows = items.map((c) => `<tr>
      <td class="mono">${esc((c.candidate_id || "").slice(0, 10))}</td>
      <td>${esc(c.title || c.content || "").slice(0, 80)}</td>
      <td>${badge(esc(c.status || "pending_review"), "warn")}</td>
      <td class="mono">${esc(c.source_type || "")}${c.source_ref ? " · " + esc(c.source_ref) : ""}</td>
      <td class="row"><button class="btn sm primary ap" data-id="${esc(c.candidate_id)}">通过</button>
        <button class="btn sm bad rj" data-id="${esc(c.candidate_id)}">驳回</button></td></tr>`).join("");
    const tok = localStorage.getItem("kb_admin_token") || "";
    view.innerHTML = pageH("知识沉淀", "审核队列", `${items.length} 条待审`)
      + `<div class="card"><div class="row" style="margin-bottom:12px">
          <label for="adm" class="sr-only">管理员令牌</label>
          <input id="adm" class="field" style="max-width:340px" type="password" placeholder="管理员令牌（服务端开启写入鉴权时需要）" value="${esc(tok)}">
          <span class="muted">通过/驳回将写入知识库</span></div>
        <div id="rv-msg" class="err" style="display:none"></div>
        <table><thead><tr><th>ID</th><th>标题/内容</th><th>状态</th><th>来源</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    focusH1();
    $("#adm").addEventListener("change", (e) => localStorage.setItem("kb_admin_token", e.target.value.trim()));
    const act = async (id, action, btn) => {
      btn.disabled = true; const msg = $("#rv-msg"); msg.style.display = "none";
      const admTok = ($("#adm").value || "").trim();
      const headers = { "content-type": "application/json" };
      if (admTok) headers["x-codekb-admin-token"] = admTok;
      try {
        const r = await fetch("/audit/" + encodeURIComponent(id), { method: "POST", headers, body: JSON.stringify({ action }) });
        if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || ("HTTP " + r.status)); }
        say(action === "approve" ? "已通过" : "已驳回"); viewReview();
      } catch (e) { btn.disabled = false; msg.style.display = ""; msg.textContent = "操作失败：" + e.message; }
    };
    view.querySelectorAll(".ap").forEach((b) => (b.onclick = () => act(b.dataset.id, "approve", b)));
    view.querySelectorAll(".rj").forEach((b) => (b.onclick = () => act(b.dataset.id, "reject", b)));
  } catch (e) { view.innerHTML = pageH("知识沉淀", "审核队列", "") + errBox(e); }
}

/* ---- 诊断 ---- */
function viewDiagnose() {
  setActive("diagnose");
  view.innerHTML = pageH("研发诊断", "诊断入口", "把外部事件（CI / MR / ISSUE_TRACKER / Crash / 通用 webhook）归一化为诊断请求并预览。")
    + `<div class="card fade"><div class="grid c2">
        <div><label class="lbl">来源</label><select id="dg-src" class="field">
          <option>ci</option><option>mr</option><option>issue_tracker</option><option>crash</option><option>code_review</option><option>generic</option></select></div>
        <div><label class="lbl">动作</label><select id="dg-act" class="field"><option value="normalize">归一化预览</option><option value="validate">校验映射</option></select></div>
      </div>
      <label class="lbl" for="dg-tok">Webhook 令牌 <span class="muted">（归一化/校验接口需要）</span></label>
      <input id="dg-tok" class="field" type="password" placeholder="x-codekb-token" value="${esc(localStorage.getItem("kb_webhook_token") || "")}">
      <label class="lbl" for="dg-body">事件 payload (JSON)</label>
      <textarea id="dg-body" class="field" placeholder='{"repo":"ym/app","branch":"main","error":"..."}'></textarea>
      <div class="row" style="margin-top:12px"><button class="btn primary" id="dg-run">运行</button>
        <button class="btn ghost" id="dg-sample">载入样例套件</button></div>
      <div id="dg-out" style="margin-top:14px"></div></div>`;
  focusH1();
  $("#dg-tok").addEventListener("change", (e) => localStorage.setItem("kb_webhook_token", e.target.value.trim()));
  $("#dg-run").onclick = async () => {
    const src = $("#dg-src").value, act = $("#dg-act").value, out = $("#dg-out");
    let payload; try { payload = JSON.parse($("#dg-body").value || "{}"); } catch (e) { out.innerHTML = errBox(new Error("payload 不是合法 JSON")); return; }
    const t = ($("#dg-tok").value || "").trim();
    out.innerHTML = loading();
    try { const d = await API.post(`/diagnose/webhook/${src}/${act}`, payload, t ? { "x-codekb-token": t } : {}); out.innerHTML = `<div class="card fade">${friendly(d)}${raw(d)}</div>`; }
    catch (e) { out.innerHTML = errBox(e); }
  };
  $("#dg-sample").onclick = async () => {
    const out = $("#dg-out"); out.innerHTML = loading();
    try { const d = await API.get("/diagnose/webhook/sample-suite"); out.innerHTML = `<div class="card fade">${friendly(d)}${raw(d)}</div>`; }
    catch (e) { out.innerHTML = errBox(e); }
  };
}

/* ---- 治理 ---- */
async function viewGovern() {
  setActive("govern");
  view.innerHTML = pageH("知识治理", "治理", "知识缺口、答案反馈与治理报告。") + loading();
  try {
    const [gaps, fb] = await Promise.all([
      API.get("/diagnose/gaps/summary?limit=10").catch(() => null),
      API.get("/feedback/summary?limit=10").catch(() => null),
    ]);
    let html = pageH("知识治理", "治理", "知识缺口、答案反馈与治理报告。");
    html += `<div class="grid c4">`;
    if (fb) html += stat("反馈总数", num(fb.total)) + stat("有用率", fb.total ? Math.round((fb.positive || 0) / fb.total * 100) + "%" : "—") + stat("修正", num(fb.corrected)) + stat("坏例", num((fb.badcases || []).length));
    html += `</div>`;
    html += `<div class="grid c2" style="margin-top:14px">
      <div class="card"><h3>知识缺口 <span class="muted">/diagnose/gaps</span></h3>${gaps ? friendly(gaps) + raw(gaps) : empty("无数据")}</div>
      <div class="card"><h3>反馈坏例</h3>${fb && (fb.badcases || []).length ? (fb.badcases.slice(0, 8).map((b) => `<div class="cite" style="grid-template-columns:1fr"><div class="ttl">${esc(b.reason || b.answer_id || "")}</div><div class="loc">${esc(b.trace_id || "")}</div></div>`).join("")) : empty("暂无坏例")}</div></div>`;
    html += `<div class="row" style="margin-top:14px"><button class="btn ghost" id="gv-full">加载完整治理报告</button></div><div id="gv-out" style="margin-top:12px"></div>`;
    view.innerHTML = html; focusH1();
    $("#gv-full").onclick = async () => { const o = $("#gv-out"); o.innerHTML = loading(); try { const d = await API.get("/governance/report?limit=50"); o.innerHTML = `<div class="card fade">${friendly(d)}${raw(d)}</div>`; } catch (e) { o.innerHTML = errBox(e); } };
  } catch (e) { view.innerHTML = pageH("知识治理", "治理", "") + errBox(e); }
}
function stat(k, v, mono) { return `<div class="stat fade"><div class="k">${esc(k)}</div><div class="v ${mono ? "mono" : ""}">${esc(v)}</div></div>`; }

/* ---- 系统 ---- */
async function viewSystem() {
  setActive("system");
  view.innerHTML = pageH("系统状态", "系统", "服务、索引、向量库与就绪状态。") + loading();
  try {
    const [h, idx, qd, reg] = await Promise.all([
      API.get("/healthz").catch(() => null),
      API.get("/index/status").catch(() => null),
      API.get("/storage/qdrant/status").catch((e) => ({ error: e.message })),
      API.get("/kb/registry").catch(() => null),
    ]);
    const comp = (h && h.components) || {};
    const compRows = Object.entries(comp).map(([k, v]) => {
      const good = ["ok", "configured", "default"].includes(String(v));
      return `<tr><td>${esc(k)}</td><td>${badge(esc(v), good ? "ok" : (v === "deferred" ? "" : "warn"))}</td></tr>`;
    }).join("");
    const col = (qd && qd.collection) || {};
    let html = pageH("系统状态", "系统", "");
    html += `<div class="grid c4">
      ${stat("服务", h ? (h.status === "ok" ? "在线" : "降级") : "不可达")}
      ${stat("检索器", esc(comp.retriever || "—"))}
      ${stat("向量库 points", num(col.points_count ?? "—"), true)}
      ${stat("向量维度", num(col.vector_size ?? "—"), true)}</div>`;
    html += `<div class="grid c2" style="margin-top:14px">
      <div class="card"><h3>组件健康</h3><table><tbody>${compRows || "<tr><td>无数据</td></tr>"}</tbody></table></div>
      <div class="card"><h3>索引 <span class="muted">/index/status</span></h3>${idx ? `<dl class="kv">${Object.entries(idx).map(([k, v]) => `<dt>${esc(k)}</dt><dd class="mono">${esc(typeof v === "object" ? JSON.stringify(v) : v)}</dd>`).join("")}</dl>` : empty("无")}</div></div>`;
    if (reg && reg.sub_kbs) {
      html += `<div class="card" style="margin-top:14px"><h3>子知识库 <span class="muted">registry</span></h3><table><thead><tr><th>ID</th><th>名称</th><th>状态</th><th>Owner</th></tr></thead><tbody>${reg.sub_kbs.map((s) => `<tr><td class="mono">${esc(s.id)}</td><td>${esc(s.name || "")}</td><td>${badge(esc(s.status || ""), s.status === "pilot" ? "spark" : "")}</td><td>${esc(s.owner_group || "")}</td></tr>`).join("")}</tbody></table></div>`;
    }
    html += `<div class="card" style="margin-top:14px"><details><summary style="cursor:pointer;font-weight:700">Qdrant 原始状态</summary><pre style="margin-top:10px">${esc(JSON.stringify(qd, null, 2))}</pre></details></div>`;
    view.innerHTML = html; focusH1();
  } catch (e) { view.innerHTML = pageH("系统状态", "系统", "") + errBox(e); }
}

/* ---- 接入 ---- */
function viewIntegrations() {
  setActive("integrations");
  const tok = localStorage.getItem("kb_admin_token") || "";
  view.innerHTML = pageH("接入与确认", "接入", "IM确认、用户 token 绑定与外部接入状态。")
    + `<div class="card"><div class="row" style="margin-bottom:12px">
        <label for="adm2" class="sr-only">管理员令牌</label>
        <input id="adm2" class="field" style="max-width:340px" type="password" placeholder="管理员令牌（汇总接口需要）" value="${esc(tok)}">
        <button class="btn primary" id="ig-load">加载汇总</button></div>
      <div class="grid c2"><div class="card" id="ig-conf"><h3>确认响应汇总</h3><div class="muted">输入令牌后点“加载汇总”。</div></div>
        <div class="card" id="ig-tok"><h3>Token 绑定汇总</h3><div class="muted">输入令牌后点“加载汇总”。</div></div></div></div>
      <div class="card" style="margin-top:14px"><h3>说明</h3><p class="muted">IM真实 OAuth / 推送、Wiki 写入、ISSUE_TRACKER / Git派单需在服务器密钥文件配置凭据后启用（见 docs/integration-credentials-guide.md）。当前以网页确认 + outbox 兜底。</p></div>`;
  focusH1();
  const load = async () => {
    const t = ($("#adm2").value || "").trim(); localStorage.setItem("kb_admin_token", t);
    const hdr = t ? { "x-codekb-admin-token": t } : {};
    for (const [path, sel, title] of [
      ["/auth/im/confirmations/responses/summary", "#ig-conf", "确认响应汇总"],
      ["/auth/im/token-bindings/summary", "#ig-tok", "Token 绑定汇总"],
    ]) {
      const el = $(sel); el.innerHTML = `<h3>${title}</h3>` + loading();
      try { const d = await API.get(path, hdr); el.innerHTML = `<h3>${title}</h3>${friendly(d)}${raw(d)}`; }
      catch (e) { el.innerHTML = `<h3>${title}</h3>` + errBox(e); }
    }
  };
  $("#ig-load").onclick = load;
}

/* ---- 存储(管理员) ---- */
function viewStorage() {
  setActive("storage");
  const tok = localStorage.getItem("kb_admin_token") || "";
  view.innerHTML = pageH("管理员 · 存储", "存储与数据库", "概览 + 内嵌 Qdrant / Postgres 控制台（需管理员令牌）。")
    + `<div class="card"><div class="row">
        <label for="adm3" class="sr-only">管理员令牌</label>
        <input id="adm3" class="field" style="max-width:300px" type="password" placeholder="管理员令牌" value="${esc(tok)}">
        <button class="btn primary" id="st-load">加载</button>
        <div class="cmd-modes" id="st-tabs" style="margin-left:auto">
          <button data-tab="overview" class="on" aria-pressed="true">概览</button>
          <button data-tab="qdrant" aria-pressed="false">Qdrant 控制台</button>
          <button data-tab="pg" aria-pressed="false">Postgres 控制台</button>
        </div></div></div>
      <div id="st-out" style="margin-top:14px"><div class="muted" style="padding:10px">输入管理员令牌后点“加载”。</div></div>`;
  focusH1();
  let tab = "overview";
  const showTab = (t) => {
    tab = t;
    document.querySelectorAll("#st-tabs button").forEach((b) => { const on = b.dataset.tab === t; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on ? "true" : "false"); });
    if (t === "overview") loadStorage();
    else if (t === "qdrant") $("#st-out").innerHTML = consoleFrame("/dashboard", "Qdrant 控制台 · 官方 dashboard（集合 / 点查询 / 可视化 / Console）");
    else $("#st-out").innerHTML = consoleFrame("/pgweb/", "Postgres 控制台 · pgweb（只读：浏览 / SQL 查询 / 导出）");
  };
  $("#st-tabs").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) showTab(b.dataset.tab); });
  $("#st-load").onclick = () => dbproxyAuth().then(() => showTab(tab));
  if (tok) dbproxyAuth().then(() => showTab("overview"));
}
async function dbproxyAuth() {
  const t = (($("#adm3") && $("#adm3").value) || localStorage.getItem("kb_admin_token") || "").trim();
  if (!t) return;
  localStorage.setItem("kb_admin_token", t);
  try { await API.post("/dbproxy/auth", { token: t }); } catch (_) {}
}
function consoleFrame(src, title) {
  return `<div class="card"><div class="row" style="margin-bottom:10px"><h3 style="margin:0">${esc(title)}</h3>
      <a class="btn sm ghost" href="${src}" target="_blank" rel="noopener" style="margin-left:auto">新窗口打开 ↗</a></div>
    <iframe src="${src}" title="${esc(title)}" referrerpolicy="no-referrer"
      style="width:100%;height:74vh;border:1px solid var(--line);border-radius:var(--r-sm);background:#fff"></iframe></div>`;
}
async function loadStorage() {
  const t = ($("#adm3").value || "").trim(); localStorage.setItem("kb_admin_token", t);
  const hdr = t ? { "x-codekb-admin-token": t } : {};
  const out = $("#st-out"); out.innerHTML = loading();
  let d; try { d = await API.get("/admin/storage", hdr); } catch (e) { out.innerHTML = errBox(e); return; }
  const m = d.models || {}, qd = d.qdrant || {}, pg = d.postgres || {}, col = qd.collections || [];
  let html = `<div class="grid c4">
    ${stat("Embedder", esc((m.embedder || {}).model || (m.embedder || {}).provider || "—"))}
    ${stat("Reranker", esc((m.reranker || {}).model || (m.reranker || {}).provider || "—"))}
    ${stat("生成 LLM", esc((m.llm || {}).model || "—"))}
    ${stat("检索器", esc(m.retriever || "—"))}</div>`;
  html += `<div class="grid c2" style="margin-top:14px">
    <div class="card"><h3>模型配置</h3>${friendly(m)}</div>
    <div class="card"><h3>Postgres <span class="muted">原子库</span></h3>
      <div class="grid c2">${stat("总原子", num(pg.total))}${stat("子库数", num((pg.by_sub_kb || []).length))}</div>
      ${pg.by_sub_kb ? `<div style="margin-top:10px">${tableOf(pg.by_sub_kb)}</div>` : (pg.error ? errBox(new Error(pg.error)) : "")}</div></div>`;
  const tele = qd.telemetry || {};
  html += `<div class="card" style="margin-top:14px"><h3>Qdrant 向量库 <span class="muted">${esc(qd.url || "")}</span>${tele.version ? " " + badge("v" + tele.version, "cyan") : ""}</h3>
    ${col.length ? tableOf(col) : (qd.error ? errBox(new Error(qd.error)) : empty("无集合"))}</div>`;
  html += `<div class="card" style="margin-top:14px"><h3>语义搜索 <span class="muted">向量召回 · 本地 embedder（官方控制台做不到）</span></h3>
    <div class="row" style="margin-bottom:10px">
      <input id="vs-q" class="field" style="flex:1;min-width:240px" placeholder="输入自然语言 / 符号 / 错误码，向量召回">
      <select id="vs-sub" class="field" style="max-width:170px"><option value="">全部子库</option><option>code</option><option>docs</option><option>testing</option><option>release</option><option>incident</option></select>
      <button class="btn primary" id="vs-go">搜索</button></div>
    <div id="vs-out"><div class="muted">在向量库里语义检索，返回命中片段 + 相关度分数。</div></div></div>`;
  if (d.index && Object.keys(d.index).length) html += `<div class="card" style="margin-top:14px"><h3>本地索引 (SQLite)</h3>${friendly(d.index)}</div>`;
  html += `<div class="card" style="margin-top:14px"><h3>内容采样 <span class="muted">Qdrant scroll · payload</span></h3>
    <div class="row" style="margin-bottom:10px">
      <input id="sm-sub" class="field" style="max-width:220px" placeholder="子库过滤（如 code，可空）">
      <button class="btn" id="sm-go">采样 10 条</button></div>
    <div id="sm-out"><div class="muted">点“采样”预览向量库内容。</div></div></div>`;
  html += `<div class="card" style="margin-top:14px"><h3>官方控制台 / 高级工具 <span class="muted">SSH 隧道安全访问</span></h3>
    <p class="muted">Qdrant 绑定在服务器本地 (127.0.0.1:6333)。隧道转发到本地后访问官方面板（集合管理 / 可视化 / 快照 / Console）：</p>
    <pre>ssh -L 6333:127.0.0.1:6333 root@your-server.example.com -p &lt;PORT&gt;</pre>
    <p class="muted">然后浏览器打开 <code>http://localhost:6333/dashboard</code>。Postgres 可同法接入 pgweb / CloudBeaver（需要的话我来部署）。</p></div>`;
  out.innerHTML = html;
  const vsgo = $("#vs-go");
  if (vsgo) vsgo.onclick = async () => {
    const q = ($("#vs-q").value || "").trim(); if (!q) return;
    const sub = $("#vs-sub").value, o = $("#vs-out"); o.innerHTML = loading();
    try {
      const r = await API.post("/code/search", { query: q, top_k: 8, ...(sub ? { sub_kbs: [sub] } : {}) });
      const hits = r.hits || [];
      o.innerHTML = hits.length ? hits.map((h) => `<div class="cite"><span class="n">${(Number(h.score) || 0).toFixed(2)}</span><div><div class="loc">${esc(h.file_path ? h.file_path + ":L" + h.start_line + "-" + h.end_line : (h.docid || ""))} ${h.symbol ? badge(h.symbol, "spark") : ""}</div><pre style="margin-top:8px">${esc((h.snippet || "").slice(0, 500))}</pre></div></div>`).join("") : empty("无匹配");
    } catch (e) { o.innerHTML = errBox(e); }
  };
  $("#sm-go").onclick = async () => {
    const sub = ($("#sm-sub").value || "").trim(), o = $("#sm-out");
    o.innerHTML = loading();
    try {
      const s = await API.get(`/admin/qdrant/sample?collection=codekb_atoms&limit=10${sub ? "&sub_kb=" + encodeURIComponent(sub) : ""}`, hdr);
      const pts = s.points || [];
      o.innerHTML = pts.length ? pts.map((p) => `<div class="cite" style="grid-template-columns:1fr">
        <div class="loc">${esc(p.location || p.source_docid || p.id)} ${p.sub_kb_id ? badge(p.sub_kb_id, "cyan") : ""}</div>
        ${p.snippet ? `<pre style="margin-top:8px">${esc(p.snippet)}</pre>` : friendly(p)}</div>`).join("") : empty("无数据", s.error || "");
    } catch (e) { o.innerHTML = errBox(e); }
  };
}

/* ---- 观测(可观测性) ---- */
function _bars(rows, key, label) {
  if (!rows || !rows.length) return '<span class="muted">无数据</span>';
  const max = Math.max(...rows.map((r) => r[key] || 0), 1);
  return rows.map((r) => `<div class="row" style="gap:8px;margin:3px 0"><span class="muted" style="width:96px;font-size:12px">${esc(r[label])}</span><span style="height:10px;border-radius:4px;background:var(--spark);width:${Math.max(3, Math.round((r[key] / max) * 200))}px"></span><span class="mono" style="font-size:12px">${num(r[key])}</span></div>`).join("");
}
async function viewUsage() {
  setActive("usage");
  view.innerHTML = pageH("观测", "用量观测", "技能 / MCP / 网页对知识库的调用频次与效果（测试期）。") + loading();
  try {
    const d = await API.get("/usage/summary?limit=60");
    if (!d.configured) { view.innerHTML = pageH("观测", "用量观测", "") + empty("未启用记录", "在服务端设置 CODEKB_USAGE_LOG 后开始采集"); return; }
    const tools = d.by_tool || [];
    const totCount = tools.reduce((a, t) => a + (t.count || 0), 0);
    const totEmpty = tools.reduce((a, t) => a + (t.empty || 0), 0);
    const latW = tools.reduce((a, t) => a + ((t.avg_latency_ms || 0) * (t.count || 0)), 0);
    const avgLat = totCount ? Math.round(latW / totCount) : 0;
    let html = pageH("观测", "用量观测", "技能 / MCP / 网页对知识库的调用频次与效果（测试期）。");
    html += `<div class="grid c4">
      ${stat("总调用", num(d.total))}
      ${stat("使用工具", num(tools.length))}
      ${stat("平均延迟", avgLat + " ms", true)}
      ${stat("空结果率", totCount ? Math.round(totEmpty / totCount * 100) + "%" : "—")}</div>`;
    html += `<div class="grid c2" style="margin-top:14px">
      <div class="card"><h3>按工具</h3><table><thead><tr><th>工具</th><th>调用</th><th>平均延迟</th><th>空结果率</th></tr></thead><tbody>${tools.map((t) => `<tr><td class="mono">${esc(t.tool)}</td><td>${num(t.count)}</td><td class="mono">${t.avg_latency_ms == null ? "—" : t.avg_latency_ms + "ms"}</td><td>${Math.round((t.empty_rate || 0) * 100)}%</td></tr>`).join("") || "<tr><td class='muted'>无</td></tr>"}</tbody></table></div>
      <div class="card"><h3>按来源</h3>${friendly(d.by_source)}<h3 style="margin-top:16px">按天调用</h3>${_bars(d.by_day, "count", "day")}</div></div>`;
    const recent = d.recent || [];
    html += `<div class="card" style="margin-top:14px"><h3>近期事件 · ${recent.length}</h3><table><thead><tr><th>时间</th><th>工具</th><th>来源</th><th>查询</th><th>命中</th><th>延迟</th></tr></thead><tbody>${recent.map((e) => `<tr><td class="mono" style="font-size:11px">${esc((e.ts || "").slice(5, 19).replace("T", " "))}</td><td class="mono">${esc(e.tool)}</td><td>${badge(esc(e.source || ""), e.source === "web" ? "" : "cyan")}</td><td>${esc((e.query || "").slice(0, 56))}</td><td>${e.refused ? badge("拒答", "warn") : (e.results == null ? "—" : e.results)}</td><td class="mono" style="font-size:11px">${e.latency_ms == null ? "—" : Math.round(e.latency_ms) + "ms"}</td></tr>`).join("") || "<tr><td class='muted'>无</td></tr>"}</tbody></table></div>`;
    view.innerHTML = html; focusH1();
  } catch (e) { view.innerHTML = pageH("观测", "用量观测", "") + errBox(e); }
}

/* ---------- 路由 ---------- */
const ROUTES = { ask: viewAsk, code: viewCode, review: viewReview, diagnose: viewDiagnose, govern: viewGovern, usage: viewUsage, system: viewSystem, storage: viewStorage, integrations: viewIntegrations };
function route() {
  const id = (location.hash.replace(/^#\//, "") || "ask").split("/")[0];
  (ROUTES[id] || viewAsk)();
}
window.addEventListener("hashchange", route);
buildNav(); route(); pollHealth(); setInterval(pollHealth, 30000);
