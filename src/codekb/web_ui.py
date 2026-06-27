from __future__ import annotations

import html
from collections.abc import Iterable, Sequence


NavItem = tuple[str, str, str]
Action = tuple[str, str]
Metric = tuple[str, str, str]


NAV_ITEMS: tuple[NavItem, ...] = (
    ("ask", "智能问答", "/console"),
    ("hub", "工作台", "/hub"),
    ("audit", "审核队列", "/audit/page"),
    ("webhook", "诊断入口", "/demo/webhook"),
    ("confirmations", "确认收件箱", "/auth/im/confirmations/page"),
    ("current-user", "当前用户", "/demo/current-user"),
    ("qdrant", "向量库", "/storage/qdrant/page"),
    ("verification", "最终验收", "/diagnose/final-verification/page"),
)

# 内联描边图标(Feather 风格,继承 currentColor):不依赖任何静态资源管线,
# 让控制台看起来像个产品,而不是一排开发按钮。
_ICON = {
    "ask": '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/>',
    "hub": '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/>',
    "audit": '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
    "current-user": '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    "webhook": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "confirmations": '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    "qdrant": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>',
    "verification": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.6"/>',
}
_NAV_LABEL = {key: label for key, label, _ in NAV_ITEMS}


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _icon(key: str) -> str:
    body = _ICON.get(key, _ICON["hub"])
    return (
        '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        f"{body}</svg>"
    )


def render_metric_cards(metrics: Iterable[Metric]) -> str:
    cards = []
    for label, value, metric_id in metrics:
        safe_metric = esc(metric_id)
        cards.append(
            f"""<div class="metric" data-metric="{safe_metric}">
  <span>{esc(label)}</span>
  <strong>{esc(value)}</strong>
</div>"""
        )
    return f"""<div class="metric-grid">
{''.join(cards)}
</div>"""


def render_app_shell(
    *,
    title: str,
    body: str,
    active: str,
    subtitle: str = "",
    actions: Sequence[Action] = (),
    extra_script: str = "",
    max_width: str = "1180px",
) -> str:
    nav = "\n".join(_render_nav_item(item, active) for item in NAV_ITEMS)
    action_html = "\n".join(
        f'<a class="button secondary" href="{esc(href)}">{esc(label)}</a>' for href, label in actions
    )
    subtitle_html = f'<p class="lede">{esc(subtitle)}</p>' if subtitle else ""
    eyebrow = _NAV_LABEL.get(active, "Code-KB")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>{esc(title)} · Code-KB</title>
  <style>{_base_css(max_width=max_width)}</style>
</head>
<body data-ui-version="3">
<div class="app">
  <aside class="sidebar">
    <a class="brand" href="/hub" aria-label="Code-KB">
      <span class="brand-mark">KB</span>
      <span class="brand-text"><strong>Code-KB</strong><small>研发知识中枢</small></span>
    </a>
    <nav class="nav" aria-label="主导航">
{nav}
    </nav>
    <div class="sidebar-foot"><span class="dot"></span>系统在线 · 可引用 · 可治理</div>
  </aside>
  <div class="main">
    <header class="page-head">
      <div class="page-head-text">
        <p class="eyebrow">{esc(eyebrow)}</p>
        <h1>{esc(title)}</h1>
        {subtitle_html}
      </div>
      <div class="page-actions">
{action_html}
      </div>
    </header>
    <div class="content">
{body}
    </div>
  </div>
</div>
{extra_script}
</body>
</html>"""


def render_legacy_page_in_shell(
    *,
    legacy_html: str,
    title: str,
    active: str,
    subtitle: str = "",
    actions: Sequence[Action] = (),
    max_width: str = "1180px",
) -> str:
    body = _extract_between(legacy_html, "<main", "</main>")
    body = body[body.find(">") + 1 :] if ">" in body else body
    body = _strip_first_h1(body)
    script = _extract_between(legacy_html, "<script>", "</script>")
    extra_script = f"<script>{script}</script>" if script else ""
    return render_app_shell(
        title=title,
        subtitle=subtitle,
        active=active,
        body=body,
        actions=actions,
        extra_script=extra_script,
        max_width=max_width,
    )


def _extract_between(text: str, start: str, end: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        return ""
    end_index = text.rfind(end)
    if end_index < 0 or end_index <= start_index:
        return ""
    return text[start_index:end_index]


def _strip_first_h1(body: str) -> str:
    start = body.find("<h1")
    if start < 0:
        return body
    close = body.find("</h1>", start)
    if close < 0:
        return body
    return body[:start] + body[close + len("</h1>") :]


def _render_nav_item(item: NavItem, active: str) -> str:
    key, label, href = item
    current = ' aria-current="page"' if key == active else ""
    return (
        f'      <a class="nav-item" href="{esc(href)}"{current}>'
        f'<span class="nav-ico">{_icon(key)}</span>'
        f'<span class="nav-label">{esc(label)}</span></a>'
    )


def _base_css(*, max_width: str) -> str:
    return f"""
    :root {{
      color-scheme: light dark;
      --bg: #eef1f6;
      --surface: #ffffff;
      --surface-soft: #f7f9fc;
      --surface-muted: #eef2f8;
      --text: #1a2233;
      --muted: #5b6577;
      --subtle: #8a93a6;
      --line: #e4e8f0;
      --line-strong: #d3d9e6;
      --brand: #5b5bef;
      --brand-2: #8b5cf6;
      --brand-strong: #4338ca;
      --brand-soft: #eef0ff;
      --ok: #0f9d6b;
      --warn: #c2740a;
      --danger: #d83a3a;
      --ink: #0e1422;
      --focus: rgba(91, 91, 239, 0.30);
      --radius: 14px;
      --radius-sm: 10px;
      --shadow-sm: 0 1px 2px rgba(16, 24, 40, 0.05);
      --shadow: 0 10px 30px rgba(33, 37, 80, 0.08);
      --shadow-lg: 0 18px 50px rgba(33, 37, 80, 0.14);
      --sidebar-w: 248px;
      --content-max: {esc(max_width)};
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #0b0f17;
        --surface: #141a25;
        --surface-soft: #10151f;
        --surface-muted: #1c2433;
        --text: #eef2f8;
        --muted: #a3adbf;
        --subtle: #7785a0;
        --line: #232c3b;
        --line-strong: #313c4f;
        --brand: #8b8bf8;
        --brand-2: #a78bfa;
        --brand-strong: #c4c2ff;
        --brand-soft: rgba(139, 139, 248, 0.14);
        --ink: #05070c;
        --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.3);
        --shadow: 0 12px 32px rgba(0, 0, 0, 0.36);
        --shadow-lg: 0 22px 54px rgba(0, 0, 0, 0.5);
      }}
    }}
    * {{ box-sizing: border-box; }}
    html {{ min-width: 320px; }}
    body {{
      margin: 0;
      background:
        radial-gradient(1200px 600px at 80% -10%, rgba(139, 92, 246, 0.10), transparent 60%),
        radial-gradient(900px 500px at -10% 10%, rgba(91, 91, 239, 0.10), transparent 55%),
        var(--bg);
      color: var(--text);
      font-family: "Inter", "PingFang SC", "Microsoft YaHei", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.55;
      -webkit-font-smoothing: antialiased;
    }}
    a {{ color: inherit; text-decoration: none; }}
    button, input, select, textarea {{ font: inherit; }}

    .app {{
      display: grid;
      grid-template-columns: var(--sidebar-w) minmax(0, 1fr);
      min-height: 100vh;
    }}

    /* ---------- sidebar ---------- */
    .sidebar {{
      position: sticky;
      top: 0;
      align-self: start;
      height: 100vh;
      display: flex;
      flex-direction: column;
      gap: 6px;
      padding: 20px 14px;
      background: color-mix(in srgb, var(--surface) 80%, transparent);
      border-right: 1px solid var(--line);
      backdrop-filter: blur(14px);
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 11px;
      padding: 8px 10px 16px;
      color: var(--text);
    }}
    .brand-mark {{
      display: inline-grid;
      place-items: center;
      width: 40px;
      height: 40px;
      border-radius: 12px;
      background: linear-gradient(135deg, var(--brand), var(--brand-2));
      color: #fff;
      font-weight: 800;
      letter-spacing: 0.5px;
      box-shadow: 0 6px 16px rgba(91, 91, 239, 0.4);
    }}
    .brand-text strong {{ display: block; font-size: 15px; letter-spacing: 0.2px; }}
    .brand-text small {{ display: block; margin-top: 1px; color: var(--muted); font-size: 11.5px; }}
    .nav {{ display: flex; flex-direction: column; gap: 3px; }}
    .nav-item {{
      display: flex;
      align-items: center;
      gap: 11px;
      padding: 10px 12px;
      border-radius: var(--radius-sm);
      color: var(--muted);
      font-weight: 600;
      font-size: 13.5px;
      transition: background .15s, color .15s;
    }}
    .nav-ico {{ display: inline-grid; place-items: center; width: 20px; height: 20px; color: var(--subtle); }}
    .ico {{ width: 18px; height: 18px; }}
    .nav-item:hover {{ background: var(--surface-muted); color: var(--text); }}
    .nav-item:hover .nav-ico {{ color: var(--brand); }}
    .nav-item[aria-current="page"] {{
      background: var(--brand-soft);
      color: var(--brand-strong);
      box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--brand) 30%, transparent);
    }}
    .nav-item[aria-current="page"] .nav-ico {{ color: var(--brand); }}
    .sidebar-foot {{
      margin-top: auto;
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 12px 12px 4px;
      color: var(--subtle);
      font-size: 11.5px;
    }}
    .dot {{ width: 8px; height: 8px; border-radius: 50%; background: var(--ok); box-shadow: 0 0 0 3px color-mix(in srgb, var(--ok) 22%, transparent); }}

    /* ---------- main ---------- */
    .main {{ min-width: 0; display: flex; flex-direction: column; }}
    .content {{
      width: 100%;
      max-width: var(--content-max);
      margin: 0 auto;
      padding: 0 28px 48px;
    }}
    .page-head {{
      position: relative;
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: flex-end;
      width: 100%;
      max-width: var(--content-max);
      margin: 0 auto;
      padding: 30px 28px 22px;
    }}
    .page-head-text {{ min-width: 0; }}
    .eyebrow {{
      margin: 0 0 7px !important;
      display: inline-block;
      color: var(--brand-strong) !important;
      background: var(--brand-soft);
      border-radius: 999px;
      padding: 3px 11px;
      font-size: 11.5px !important;
      font-weight: 700;
      letter-spacing: 0.3px;
    }}
    .page-head h1 {{
      margin: 0;
      font-size: 30px;
      line-height: 1.15;
      font-weight: 800;
      letter-spacing: -0.4px;
      background: linear-gradient(120deg, var(--text), color-mix(in srgb, var(--brand) 55%, var(--text)));
      -webkit-background-clip: text;
      background-clip: text;
      color: transparent;
    }}
    .lede {{ max-width: 720px; margin: 9px 0 0; color: var(--muted); font-size: 14.5px; }}
    .page-actions, .actions {{ display: flex; flex-wrap: wrap; gap: 9px; align-items: center; }}

    /* ---------- buttons ---------- */
    .button, button, a.button {{
      min-height: 40px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      border: 1px solid var(--line-strong);
      border-radius: 10px;
      padding: 9px 15px;
      background: var(--surface);
      color: var(--text);
      cursor: pointer;
      font-weight: 650;
      box-shadow: var(--shadow-sm);
      transition: transform .12s, box-shadow .15s, border-color .15s, background .15s;
    }}
    .button.primary, button.primary, a.button.primary {{
      border-color: transparent;
      background: linear-gradient(135deg, var(--brand), var(--brand-2));
      color: #fff;
      box-shadow: 0 8px 20px rgba(91, 91, 239, 0.32);
    }}
    .button.secondary {{ background: var(--surface); }}
    button.danger {{ border-color: color-mix(in srgb, var(--danger) 45%, var(--line)); color: var(--danger); }}
    button:disabled {{ cursor: not-allowed; opacity: 0.5; box-shadow: none; }}
    .button:hover, button:hover, a.button:hover {{ transform: translateY(-1px); border-color: var(--brand); box-shadow: var(--shadow); }}
    .button.primary:hover {{ border-color: transparent; }}
    .button:focus-visible, button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible {{
      outline: 3px solid var(--focus);
      outline-offset: 2px;
    }}

    /* ---------- panels / cards ---------- */
    .panel {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 20px;
      box-shadow: var(--shadow-sm);
    }}
    .panel.emphasis {{
      border-color: transparent;
      box-shadow: var(--shadow);
      background:
        linear-gradient(var(--surface), var(--surface)) padding-box,
        linear-gradient(135deg, var(--brand), var(--brand-2)) border-box;
      border: 1px solid transparent;
    }}
    .section-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
      margin-top: 18px;
    }}
    .wide {{ grid-column: 1 / -1; }}
    h2, h3 {{ margin: 0 0 13px; line-height: 1.3; letter-spacing: -0.2px; }}
    h2 {{ font-size: 18px; font-weight: 750; }}
    h3 {{ font-size: 15px; font-weight: 700; }}
    .muted {{ color: var(--muted); }}
    .ok {{ color: var(--ok); }}
    .warn {{ color: var(--warn); }}
    .error {{ color: var(--danger); }}

    /* ---------- metrics ---------- */
    .metric-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin-top: 18px;
    }}
    .metric {{
      position: relative;
      min-height: 92px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      padding: 16px;
      box-shadow: var(--shadow-sm);
      overflow: hidden;
      transition: transform .12s, box-shadow .15s;
    }}
    .metric::before {{
      content: "";
      position: absolute; inset: 0 auto 0 0; width: 3px;
      background: linear-gradient(var(--brand), var(--brand-2));
      opacity: .85;
    }}
    .metric:hover {{ transform: translateY(-2px); box-shadow: var(--shadow); }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; font-weight: 600; }}
    .metric strong {{
      display: block;
      margin-top: 6px;
      color: var(--text);
      font-size: 26px;
      font-weight: 780;
      line-height: 1.1;
      letter-spacing: -0.6px;
      overflow-wrap: anywhere;
    }}

    /* ---------- actions / lanes ---------- */
    .action-grid, .lane {{ display: grid; gap: 10px; }}
    .action-card, .action {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: center;
      min-height: 58px;
      padding: 13px 15px;
      border: 1px solid var(--line);
      border-radius: var(--radius-sm);
      background: var(--surface-soft);
      transition: border-color .15s, background .15s, transform .12s;
    }}
    .action-card:hover, .action:hover {{ border-color: var(--brand); background: var(--surface); transform: translateY(-1px); }}
    .action-card strong, .action strong {{ display: block; font-size: 14px; line-height: 1.3; }}
    .action-card span, .action span span {{
      display: block;
      margin-top: 3px;
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }}
    .endpoint, code {{
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
      color: var(--muted);
      font-size: 12px;
      background: var(--surface-muted);
      padding: 2px 7px;
      border-radius: 6px;
      white-space: nowrap;
    }}

    /* ---------- forms ---------- */
    label {{ display: block; margin: 14px 0 6px; color: var(--text); font-weight: 650; font-size: 13px; }}
    input, select, textarea {{
      width: 100%;
      min-height: 42px;
      border: 1px solid var(--line-strong);
      border-radius: 10px;
      padding: 9px 12px;
      background: var(--surface);
      color: var(--text);
      transition: border-color .15s, box-shadow .15s;
    }}
    input:focus, select:focus, textarea:focus {{ border-color: var(--brand); box-shadow: 0 0 0 3px var(--focus); outline: none; }}
    textarea {{ min-height: 120px; resize: vertical; white-space: pre-wrap; }}
    input[type="checkbox"] {{ width: 18px; min-height: 18px; margin: 0; accent-color: var(--brand); }}
    .check {{ display: flex; align-items: center; gap: 8px; color: var(--muted); font-weight: 520; }}
    .status {{ min-height: 24px; margin-top: 12px; overflow-wrap: anywhere; }}

    /* ---------- data ---------- */
    dl {{ display: grid; grid-template-columns: minmax(130px, 190px) 1fr; gap: 9px 14px; margin: 0; }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; overflow-wrap: anywhere; }}
    pre {{
      margin: 0;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--ink);
      color: #e8edf6;
      overflow: auto;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font: 12px/1.55 "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-top: 1px solid var(--line); padding: 11px 10px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }}
    th {{ color: var(--muted); font-weight: 700; font-size: 12.5px; }}
    tbody tr:hover {{ background: var(--surface-soft); }}

    .two-column {{
      display: grid;
      grid-template-columns: minmax(300px, 420px) minmax(0, 1fr);
      gap: 18px;
      align-items: start;
      margin-top: 18px;
    }}
    .stack {{ display: grid; gap: 16px; }}
    .flow {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; margin-top: 8px; }}
    .step {{
      min-height: 84px;
      border: 1px solid var(--line);
      border-radius: var(--radius-sm);
      padding: 13px;
      background: var(--surface-soft);
    }}
    .step b {{ display: block; color: var(--brand-strong); font-size: 13px; }}
    .step span {{ display: block; margin-top: 5px; color: var(--muted); font-size: 12px; }}

    /* ---------- ask console ---------- */
    .ask-box {{ background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 18px; box-shadow: var(--shadow); }}
    .ask-row {{ display: flex; gap: 10px; }}
    #ask-q {{ flex: 1; min-height: 48px; font-size: 15px; }}
    #ask-sub {{ width: auto; min-width: 148px; min-height: 48px; }}
    .ask-row .button {{ min-height: 48px; padding: 9px 22px; }}
    .ask-chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 13px; }}
    .ask-chip {{ border: 1px dashed var(--line-strong); background: var(--surface-soft); color: var(--muted); border-radius: 999px; padding: 6px 14px; font-size: 13px; cursor: pointer; transition: all .15s; }}
    .ask-chip:hover {{ border-style: solid; border-color: var(--brand); color: var(--brand-strong); }}
    .ask-out {{ margin-top: 18px; }}
    .ask-loading {{ display: flex; align-items: center; gap: 11px; color: var(--muted); padding: 22px; }}
    .spin {{ width: 16px; height: 16px; border: 2px solid var(--line-strong); border-top-color: var(--brand); border-radius: 50%; animation: spin .8s linear infinite; }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    .answer-card {{ border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); padding: 22px; box-shadow: var(--shadow); }}
    .answer-card.error-card {{ border-color: color-mix(in srgb, var(--danger) 45%, var(--line)); color: var(--danger); }}
    .answer-card.refused {{ border-left: 4px solid var(--warn); }}
    .answer-head {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }}
    .badge {{ display: inline-flex; align-items: center; padding: 3px 11px; border-radius: 999px; font-size: 12px; font-weight: 650; background: var(--surface-muted); color: var(--muted); }}
    .badge.ai {{ background: var(--brand-soft); color: var(--brand-strong); }}
    .badge.ok {{ background: color-mix(in srgb, var(--ok) 16%, transparent); color: var(--ok); }}
    .badge.warn {{ background: color-mix(in srgb, var(--warn) 18%, transparent); color: var(--warn); }}
    .answer-body {{ font-size: 15.5px; line-height: 1.72; color: var(--text); white-space: pre-wrap; overflow-wrap: anywhere; }}
    .cite-ref {{ color: var(--brand); font-weight: 700; }}
    .cites {{ margin-top: 18px; border-top: 1px solid var(--line); padding-top: 15px; }}
    .cites h3 {{ color: var(--muted); font-size: 12.5px; margin-bottom: 11px; }}
    .cite-list {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }}
    .cite {{ display: grid; grid-template-columns: auto 1fr; gap: 12px; padding: 13px; border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--surface-soft); }}
    .cite-n {{ display: grid; place-items: center; width: 24px; height: 24px; border-radius: 7px; background: var(--brand-soft); color: var(--brand-strong); font-weight: 700; font-size: 12px; }}
    .cite-main strong {{ display: block; font-size: 13.5px; }}
    .cite-meta {{ display: block; color: var(--subtle); font-size: 12px; margin-top: 2px; }}
    .cite-quote {{ margin: 8px 0 0; color: var(--muted); font-size: 13px; line-height: 1.55; }}
    .fb {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 18px; padding-top: 15px; border-top: 1px solid var(--line); }}
    .fb .button {{ min-height: 34px; padding: 6px 13px; font-size: 13px; }}
    .fb-done {{ font-size: 13px; font-weight: 600; }}

    /* ---------- responsive ---------- */
    @media (max-width: 980px) {{
      .app {{ grid-template-columns: 1fr; }}
      .sidebar {{
        position: sticky; top: 0; height: auto; flex-direction: row; flex-wrap: wrap;
        align-items: center; gap: 8px; padding: 10px 12px; z-index: 20;
      }}
      .brand {{ padding: 4px 6px; }}
      .nav {{ flex-direction: row; flex-wrap: wrap; gap: 6px; flex: 1; }}
      .nav-item {{ padding: 8px 11px; }}
      .nav-label {{ display: none; }}
      .nav-item[aria-current="page"] .nav-label {{ display: inline; }}
      .sidebar-foot {{ display: none; }}
      .page-head {{ padding: 22px 18px 16px; }}
      .content {{ padding: 0 18px 36px; }}
      .metric-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .section-grid, .flow {{ grid-template-columns: 1fr; }}
      .two-column {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 560px) {{
      .page-head {{ flex-direction: column; align-items: stretch; gap: 14px; }}
      .page-head h1 {{ font-size: 25px; }}
      .metric-grid {{ grid-template-columns: 1fr; }}
      .nav-label {{ display: none; }}
      .action-card, .action {{ grid-template-columns: 1fr; }}
      .endpoint {{ white-space: normal; }}
      dl {{ grid-template-columns: 1fr; }}
    }}
    """
