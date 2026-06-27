from __future__ import annotations

import html
from typing import Any


def render_p5_external_input_page(plan: dict[str, Any]) -> str:
    status = _text(plan.get("status"))
    readiness = _text(plan.get("readiness_status"))
    pending_count = _text(plan.get("pending_count"))
    external_state = dict(plan.get("external_state") or {})
    external_state_status = _text(external_state.get("status") or "unknown")
    external_state_pending_count = _text(plan.get("external_state_pending_count") or 0)
    setup_url = _text(plan.get("setup_url"))
    setup_status_url = _text(plan.get("setup_status_url"))
    external_inputs_markdown_url = _text(plan.get("external_inputs_markdown_url"))
    final_verification_page_url = _text(plan.get("final_verification_page_url"))
    final_verification_url = _text(plan.get("final_verification_url"))
    self_binding_page_url = _text(plan.get("self_binding_page_url"))
    token_binding_page_url = _text(plan.get("token_binding_page_url"))
    web_push_inbox_url = _text(plan.get("web_push_inbox_url"))
    current_user_demo_url = _text(plan.get("current_user_demo_url"))
    webhook_demo_url = _text(plan.get("webhook_demo_url"))
    current_user_smoke_url = _text(plan.get("current_user_smoke_url"))
    tasks = [task for task in plan.get("tasks") or [] if isinstance(task, dict)]
    final_verification = [item for item in plan.get("final_verification") or [] if isinstance(item, dict)]
    handoff = dict(plan.get("operator_handoff") or {})
    next_action = dict(handoff.get("next_action") or {})
    strategy = dict(plan.get("mcp_auth_strategy") or {})
    strategy_items = [
        ("Current user auth", _bool_text(strategy.get("current_user_auth_required", True))),
        ("Confirmation target", _text(strategy.get("confirmation_target") or "current_authenticated_user")),
        ("Interface-person lookup", _bool_text(strategy.get("interface_person_lookup_enabled", False))),
        ("MCP token argument", _text(strategy.get("auth_token_argument") or "auth_token")),
    ]
    if strategy.get("setup_page_required") is not None:
        strategy_items.insert(1, ("Setup page required", _bool_text(strategy.get("setup_page_required"))))

    task_html = "\n".join(_render_task(task, index=index) for index, task in enumerate(tasks, start=1))
    if not task_html:
        task_html = '<p class="ok">No external input tasks remain.</p>'
    verification_html = "\n".join(
        f"<li><strong>{_escape(item.get('id'))}</strong><code>{_escape(item.get('command'))}</code></li>"
        for item in final_verification
    )
    handoff_html = _render_handoff(handoff, next_action)
    strategy_html = "\n".join(
        f"<dt>{_escape(label)}</dt><dd>{_escape(value)}</dd>" for label, value in strategy_items
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Code-KB P5 External Inputs</title>
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
      --warn: #b54708;
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
      max-width: 980px;
      margin: 0 auto;
      padding: 24px 16px;
    }}
    h1 {{
      font-size: 24px;
      line-height: 1.25;
      margin: 0 0 16px;
    }}
    h2 {{
      font-size: 18px;
      line-height: 1.3;
      margin: 0 0 12px;
    }}
    h3 {{
      font-size: 16px;
      line-height: 1.35;
      margin: 0 0 8px;
    }}
    .panel {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      margin-top: 16px;
    }}
    .tasks {{
      margin-top: 16px;
    }}
    .tasks > h2 {{
      margin-bottom: 12px;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 8px 16px;
    }}
    dl {{
      display: grid;
      grid-template-columns: minmax(130px, 180px) 1fr;
      gap: 8px 12px;
      margin: 0;
    }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; overflow-wrap: anywhere; }}
    a {{ color: var(--primary); }}
    ul {{
      margin: 8px 0 0;
      padding-left: 20px;
    }}
    li {{ margin: 6px 0; }}
    code {{
      display: block;
      margin-top: 4px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--bg);
      color: var(--text);
      overflow-wrap: anywhere;
      white-space: pre-wrap;
    }}
    .muted {{ color: var(--muted); }}
    .ok {{ color: var(--ok); }}
    .warn {{ color: var(--warn); }}
  </style>
</head>
<body>
<main>
  <h1>Code-KB P5 External Inputs</h1>
  <section class="panel">
    <div class="summary">
      <div><span class="muted">Status</span><br>{_escape(status)}</div>
      <div><span class="muted">Readiness</span><br>{_escape(readiness)}</div>
      <div><span class="muted">Pending</span><br>{_escape(pending_count)}</div>
      <div><span class="muted">External state</span><br>{_escape(external_state_status)}</div>
      <div><span class="muted">State checks</span><br>{_escape(external_state_pending_count)}</div>
    </div>
  </section>
  <section class="panel">
    <h2>Current User Auth Strategy</h2>
    <dl>{strategy_html}</dl>
  </section>
  <section class="panel">
    <h2>Links</h2>
    <dl>
      <dt>Setup</dt><dd><a href="{_escape_attr(setup_url)}">{_escape(setup_url)}</a></dd>
      <dt>Setup status</dt><dd><a href="{_escape_attr(setup_status_url)}">{_escape(setup_status_url)}</a></dd>
      <dt>Markdown</dt><dd><a href="{_escape_attr(external_inputs_markdown_url)}">{_escape(external_inputs_markdown_url)}</a></dd>
      <dt>Final verification</dt><dd><a href="{_escape_attr(final_verification_page_url)}">{_escape(final_verification_page_url)}</a></dd>
      <dt>Final verification JSON</dt><dd><a href="{_escape_attr(final_verification_url)}">{_escape(final_verification_url)}</a></dd>
      <dt>Self binding</dt><dd><a href="{_escape_attr(self_binding_page_url)}">{_escape(self_binding_page_url)}</a></dd>
      <dt>Token binding fallback</dt><dd><a href="{_escape_attr(token_binding_page_url)}">{_escape(token_binding_page_url)}</a></dd>
      <dt>Web push inbox</dt><dd><a href="{_escape_attr(web_push_inbox_url)}">{_escape(web_push_inbox_url)}</a></dd>
      <dt>Current user demo</dt><dd><a href="{_escape_attr(current_user_demo_url)}">{_escape(current_user_demo_url)}</a></dd>
      <dt>Webhook demo</dt><dd><a href="{_escape_attr(webhook_demo_url)}">{_escape(webhook_demo_url)}</a></dd>
      <dt>Current user smoke</dt><dd>{_escape(current_user_smoke_url)}</dd>
    </dl>
  </section>
  <section class="panel">
    <h2>Operator Handoff</h2>
    {handoff_html}
  </section>
  <section class="tasks">
    <h2>Pending Tasks</h2>
    {task_html}
  </section>
  <section class="panel">
    <h2>Final Verification</h2>
    <ul>{verification_html}</ul>
  </section>
</main>
</body>
</html>
"""


def _render_handoff(handoff: dict[str, Any], next_action: dict[str, Any]) -> str:
    ordered = ", ".join(str(item) for item in handoff.get("ordered_task_ids") or []) or "-"
    owners = dict(handoff.get("by_owner") or {})
    owner_items = []
    for owner, task_ids in sorted(owners.items()):
        owner_items.append(f"<li><strong>{_escape(owner)}</strong>: {_escape(', '.join(task_ids))}</li>")
    owners_html = "\n".join(owner_items) or '<li class="muted">-</li>'
    criteria_html = _list_items(handoff.get("completion_criteria"), code=False)
    safe_commands = _list_items(next_action.get("safe_commands"), code=True)
    verification_commands = _list_items(next_action.get("verification_commands"), code=True)
    return f"""<dl>
  <dt>Ordered tasks</dt><dd>{_escape(ordered)}</dd>
  <dt>Next action</dt><dd>{_escape(next_action.get("check_id"))} {_escape(next_action.get("title"))}</dd>
  <dt>Next owner</dt><dd>{_escape(next_action.get("owner"))}</dd>
  <dt>Evidence</dt><dd>{_escape(next_action.get("evidence_needed"))}</dd>
</dl>
<h3>Tasks By Owner</h3>
<ul>{owners_html}</ul>
<h3>Next Safe Commands</h3>
<ul>{safe_commands}</ul>
<h3>Next Verification Commands</h3>
<ul>{verification_commands}</ul>
<h3>Completion Criteria</h3>
<ul>{criteria_html}</ul>"""


def _render_task(task: dict[str, Any], *, index: int) -> str:
    required_inputs = _list_items(task.get("required_inputs"), code=True)
    safe_commands = _list_items(task.get("safe_commands"), code=True)
    verification_commands = _list_items(task.get("verification_commands"), code=True)
    notes = _list_items(task.get("notes"), code=False)
    return f"""<article class="panel">
  <h3>{index}. {_escape(task.get("title"))}</h3>
  <dl>
    <dt>Check</dt><dd>{_escape(task.get("check_id"))}</dd>
    <dt>Owner</dt><dd>{_escape(task.get("owner"))}</dd>
    <dt>Status</dt><dd>{_escape(task.get("status"))}</dd>
    <dt>Evidence</dt><dd>{_escape(task.get("evidence_needed"))}</dd>
    <dt>Remediation</dt><dd>{_escape(task.get("remediation"))}</dd>
  </dl>
  <h3>Required Inputs</h3>
  <ul>{required_inputs}</ul>
  <h3>Safe Commands</h3>
  <ul>{safe_commands}</ul>
  <h3>Verification Commands</h3>
  <ul>{verification_commands}</ul>
  <h3>Notes</h3>
  <ul>{notes}</ul>
</article>"""


def _list_items(value: object, *, code: bool) -> str:
    items = [str(item) for item in value or []]
    if not items:
        return '<li class="muted">-</li>'
    if code:
        return "\n".join(f"<li><code>{_escape(item)}</code></li>" for item in items)
    return "\n".join(f"<li>{_escape(item)}</li>" for item in items)


def _bool_text(value: object) -> str:
    return "yes" if bool(value) else "no"


def _text(value: object) -> str:
    return str(value if value is not None else "")


def _escape(value: object) -> str:
    return html.escape(_text(value), quote=False)


def _escape_attr(value: object) -> str:
    return html.escape(_text(value), quote=True)
