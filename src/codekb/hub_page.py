from __future__ import annotations

from .ask_page import ASK_SCRIPT, render_ask_widget
from .web_ui import render_app_shell, render_metric_cards


def _shortcut(href: str, title: str, desc: str) -> str:
    return (
        f'<a class="action-card" href="{href}"><span><strong>{title}</strong>'
        f"<span>{desc}</span></span><span class=\"go\">→</span></a>"
    )


def render_hub_page() -> str:
    operations = "".join(
        _shortcut(href, title, desc)
        for href, title, desc in (
            ("/audit/page", "审核队列", "候选知识审核、修订与发布前确认"),
            ("/index/status", "索引状态", "Atom 索引加载与更新时间"),
            ("/storage/qdrant/page", "向量库", "Collection、points 与向量维度巡检"),
        )
    )
    integrate = "".join(
        _shortcut(href, title, desc)
        for href, title, desc in (
            ("/demo/webhook", "诊断入口", "code review / CI / MR / ISSUE_TRACKER / Crash 事件诊断"),
            ("/diagnose/external-inputs/page", "外部接入", "查看仍需补齐的接入参数与凭据"),
            ("/demo/current-user", "当前用户", "用用户 token 跑诊断并生成确认消息"),
        )
    )
    settle = "".join(
        _shortcut(href, title, desc)
        for href, title, desc in (
            ("/auth/im/confirmations/page", "确认收件箱", "IM推送前的网页兜底通道"),
            ("/auth/im/mcp/setup", "MCP 设置", "用户 token 的生成、绑定与使用说明"),
            ("/diagnose/final-verification/page", "最终验收", "P5 主链路验收说明与执行入口"),
        )
    )
    body = f"""
<section class="panel emphasis hero-ask">
  <h2>有研发问题？直接问。</h2>
  <p class="muted" style="margin:-4px 0 14px">基于权威知识返回带引用的可信答案，无法回答时自动沉淀为知识缺口。</p>
  {render_ask_widget(autofocus=False)}
</section>

{render_metric_cards(
    (
        ("服务", "检查中", "health"),
        ("索引", "检查中", "index"),
        ("诊断", "检查中", "diagnose"),
        ("验收", "检查中", "acceptance"),
    )
)}

<div class="section-grid">
  <section class="panel"><h2>知识运营</h2><div class="action-grid">{operations}</div></section>
  <section class="panel"><h2>集成与诊断</h2><div class="action-grid">{integrate}</div></section>
  <section class="panel"><h2>确认与设置</h2><div class="action-grid">{settle}</div></section>
  <section class="panel">
    <h2>主链路</h2>
    <div class="lane">
      <div class="action"><span><strong>1 · 问题进入</strong><span>问答、IDE、MR、CI、ISSUE_TRACKER、Crash</span></span></div>
      <div class="action"><span><strong>2 · 引用回答</strong><span>优先给出带来源的可信答案</span></span></div>
      <div class="action"><span><strong>3 · 缺口沉淀</strong><span>低置信或无法回答时生成候选知识</span></span></div>
      <div class="action"><span><strong>4 · 人工确认</strong><span>推送给当前用户确认或进入审核</span></span></div>
      <div class="action"><span><strong>5 · 发布治理</strong><span>进入 pending docs、索引与治理状态</span></span></div>
    </div>
  </section>
</div>"""
    probe = """
<script>
  async function probe(metric, url, pick) {
    const el = document.querySelector(`[data-metric="${metric}"] strong`);
    const card = document.querySelector(`[data-metric="${metric}"]`);
    if (!el) return;
    try {
      const response = await fetch(url, { cache: "no-store" });
      const data = await response.json();
      card.classList.add(response.ok ? "ok" : "warn");
      el.textContent = pick(data, response);
    } catch (error) { card.classList.add("error"); el.textContent = "不可用"; }
  }
  probe("health", "/healthz", (data) => data.status || "ok");
  probe("index", "/index/status", (data) => data.status || data.retriever || "已加载");
  probe("diagnose", "/diagnose/readiness", (data) => data.status || "就绪");
  probe("acceptance", "/diagnose/acceptance", (data) => data.status || (data.accepted ? "已通过" : "待处理"));
</script>"""
    return render_app_shell(
        title="工作台",
        subtitle="研发问题进入、知识检索、人工确认与知识沉淀的统一入口。",
        active="hub",
        body=body,
        extra_script=ASK_SCRIPT + probe,
    )
