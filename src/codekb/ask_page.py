"""智能问答控制台 —— 面向最终用户的主入口,简单直接。

一个搜索框,调用 ``POST /ask`` 并渲染带引用的答案、置信度/模式徽标、引用来源,
以及内联的 👍/👎 反馈(``POST /feedback``)。组件和脚本在仪表盘上复用,所以核心动作
("提个问,拿到带引用的答案")在任何页面都只差一步。
"""

from __future__ import annotations

from .web_ui import render_app_shell

EXAMPLE_QUESTIONS = (
    "DEVICE_SEQ 是什么？",
    "多台手机一起跑测试要注意什么？",
    "压测出问题怎么定位是哪台机器？",
)

SUB_KBS = (
    ("all", "全部知识库"),
    ("testing", "测试 / 设备"),
    ("release", "发布 / 构建"),
    ("incident", "故障 / 应急"),
)


def render_ask_widget(*, autofocus: bool = True) -> str:
    chips = "".join(f'<button type="button" class="ask-chip">{q}</button>' for q in EXAMPLE_QUESTIONS)
    options = "".join(f'<option value="{value}">{label}</option>' for value, label in SUB_KBS)
    af = " autofocus" if autofocus else ""
    return f"""
<form id="ask-form" class="ask-box" autocomplete="off">
  <div class="ask-row">
    <input id="ask-q" name="q" type="text" placeholder="输入你的研发问题，回车获取带引用的答案…"{af}>
    <select id="ask-sub" aria-label="知识库范围">{options}</select>
    <button class="button primary" type="submit">提问</button>
  </div>
  <div class="ask-chips">{chips}</div>
</form>
<div id="ask-out" class="ask-out" aria-live="polite"></div>
"""


# 自包含,无外部依赖。在 /console 和仪表盘上复用。
ASK_SCRIPT = """
<script>
(function () {
  const form = document.getElementById('ask-form');
  if (!form) return;
  const input = document.getElementById('ask-q');
  const sub = document.getElementById('ask-sub');
  const out = document.getElementById('ask-out');
  const esc = (s) => { const d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; };
  const badge = (t, c) => `<span class="badge ${c || ''}">${t}</span>`;
  const refs = (t) => esc(t).replace(/\\[(\\d+)\\]/g, '<sup class="cite-ref">[$1]</sup>');

  async function ask(q) {
    out.innerHTML = '<div class="ask-loading"><span class="spin"></span>正在检索权威知识并生成带引用的答案…</div>';
    const body = { query: q, top_k: 4 };
    const s = sub && sub.value; if (s && s !== 'all') body.sub_kbs = [s];
    let data;
    try {
      const r = await fetch('/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      data = await r.json();
      if (!r.ok) throw new Error(data.detail || ('HTTP ' + r.status));
    } catch (e) { out.innerHTML = `<div class="answer-card error-card">请求出错：${esc(e.message)}</div>`; return; }
    render(data);
  }

  function feedbackHtml() {
    return `<div class="fb"><span class="muted">这个答案有用吗？</span>
      <button type="button" class="button fb-up">👍 有用</button>
      <button type="button" class="button fb-down">👎 没用</button>
      <span class="fb-done ok"></span></div>`;
  }
  function bindFeedback(d) {
    const box = out.querySelector('.fb'); if (!box) return;
    const done = box.querySelector('.fb-done');
    async function send(rating, reason) {
      try {
        await fetch('/feedback', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ answer_id: d.answer_id, trace_id: d.trace_id, rating, reason: reason || '' }) });
        done.textContent = '✓ 已记录，感谢反馈';
        box.querySelectorAll('button').forEach(b => b.disabled = true);
      } catch (e) { done.textContent = '提交失败'; }
    }
    box.querySelector('.fb-up').onclick = () => send(1);
    box.querySelector('.fb-down').onclick = () => send(-1, (prompt('哪里不对？（可选，帮助我们改进）') || ''));
  }

  function render(d) {
    if (d.refused) {
      out.innerHTML = `<div class="answer-card refused"><div class="answer-head">${badge('暂无可信答案', 'warn')}${badge('已记录为知识缺口', 'muted')}</div>
        <p class="answer-body">没有足够的权威引用来回答这个问题。原因：${esc(d.refusal_reason || '未命中相关知识')}</p>${feedbackHtml()}</div>`;
      bindFeedback(d); return;
    }
    const cites = (d.citations || []).map((c, i) =>
      `<li class="cite"><span class="cite-n">${i + 1}</span><div class="cite-main">
        <strong>${esc(c.title || c.docid)}</strong>
        <span class="cite-meta">${esc((c.section_path || []).join(' › ') || c.docid)}</span>
        <p class="cite-quote">${esc(c.quote || '')}</p></div></li>`).join('');
    const mode = d.generation_mode === 'generative' ? badge('AI 生成', 'ai') : badge('原文摘录', 'muted');
    const cv = Math.round((d.confidence || 0) * 100);
    out.innerHTML = `<div class="answer-card"><div class="answer-head">${mode}
        ${badge('置信度 ' + cv + '%', cv >= 60 ? 'ok' : 'warn')}
        ${badge((d.model || 'model'), 'muted')}${badge(Math.round(d.latency_ms || 0) + ' ms', 'muted')}</div>
      <div class="answer-body">${refs(d.answer || '')}</div>
      ${cites ? `<div class="cites"><h3>引用来源 · ${d.citations.length}</h3><ol class="cite-list">${cites}</ol></div>` : ''}
      ${feedbackHtml()}</div>`;
    bindFeedback(d);
  }

  form.addEventListener('submit', (e) => { e.preventDefault(); const q = (input.value || '').trim(); if (q) ask(q); });
  document.querySelectorAll('.ask-chip').forEach((c) =>
    c.addEventListener('click', () => { input.value = c.textContent; form.requestSubmit(); }));
})();
</script>
"""


def render_ask_page() -> str:
    return render_app_shell(
        title="智能问答",
        subtitle="问一个研发问题，获取带来源引用的可信答案；无法回答时会自动记录为知识缺口。",
        active="ask",
        body=render_ask_widget(),
        extra_script=ASK_SCRIPT,
        max_width="920px",
    )
