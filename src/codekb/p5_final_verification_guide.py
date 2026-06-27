from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any

from .diagnosis_acceptance import build_p5_acceptance_report, build_p5_external_input_plan


def build_p5_final_verification_guide(
    readiness_report: dict[str, Any],
    external_state_report: dict[str, Any],
    *,
    api_base_url: str,
    env_file: str,
) -> dict[str, Any]:
    base_url = api_base_url.rstrip("/")
    acceptance = build_p5_acceptance_report(readiness_report, api_base_url=base_url)
    input_plan = build_p5_external_input_plan(
        readiness_report,
        api_base_url=base_url,
        env_file=env_file,
        external_state_report=external_state_report,
    )
    phases = _verification_phases(
        base_url=base_url,
        env_file=env_file,
        accepted=bool(acceptance.get("accepted")),
        external_checks={str(check.get("id", "")): dict(check) for check in external_state_report.get("checks") or []},
    )
    pending_tasks = [dict(task) for task in input_plan.get("tasks") or [] if isinstance(task, dict)]
    return {
        "status": "accepted" if acceptance.get("accepted") else input_plan.get("status", "pending_external_inputs"),
        "accepted": bool(acceptance.get("accepted")),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "api_base_url": base_url,
        "readiness_status": str(readiness_report.get("status", "") or ""),
        "pending_count": len(pending_tasks),
        "links": {
            "setup": f"{base_url}/auth/im/mcp/setup",
            "setup_status": f"{base_url}/auth/im/mcp/setup/status",
            "im_configure_page": f"{base_url}/auth/im/configure/page",
            "token_binding_page": f"{base_url}/auth/im/token-bindings/page",
            "external_inputs": f"{base_url}/diagnose/external-inputs",
            "external_inputs_markdown": f"{base_url}/diagnose/external-inputs.md",
            "external_inputs_page": f"{base_url}/diagnose/external-inputs/page",
            "final_verification": f"{base_url}/diagnose/final-verification",
            "final_verification_page": f"{base_url}/diagnose/final-verification/page",
            "current_user_smoke": f"{base_url}/auth/im/current-user/smoke",
            "confirmations": f"{base_url}/auth/im/confirmations/page",
        },
        "mcp_auth_strategy": input_plan.get("mcp_auth_strategy") or {},
        "operator_handoff": dict(input_plan.get("operator_handoff") or {}),
        "pending_tasks": pending_tasks,
        "external_state": _public_external_state(external_state_report),
        "phases": phases,
        "final_verification": input_plan.get("final_verification") or [],
        "secret_handling": {
            "env_file": env_file,
            "do_not_commit": True,
            "output_contains_secret_values": False,
        },
        "secret_values_written": False,
    }


def render_p5_final_verification_page(guide: dict[str, Any]) -> str:
    links = dict(guide.get("links") or {})
    phases = [phase for phase in guide.get("phases") or [] if isinstance(phase, dict)]
    tasks = [task for task in guide.get("pending_tasks") or [] if isinstance(task, dict)]
    checks = [check for check in (guide.get("external_state") or {}).get("checks", []) if isinstance(check, dict)]
    handoff = dict(guide.get("operator_handoff") or {})
    next_action = dict(handoff.get("next_action") or {})
    phase_html = "\n".join(_render_phase(phase) for phase in phases)
    task_html = "\n".join(_render_task(task) for task in tasks) or '<p class="ok">No pending external input tasks.</p>'
    checks_html = "\n".join(_render_check(check) for check in checks) or '<p class="muted">No external state checks.</p>'
    handoff_html = _render_handoff(handoff, next_action)
    final_html = "\n".join(
        f"<li><strong>{_escape(item.get('id'))}</strong><code>{_escape(item.get('command'))}</code></li>"
        for item in guide.get("final_verification") or []
        if isinstance(item, dict)
    )
    strategy = dict(guide.get("mcp_auth_strategy") or {})
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Code-KB P5 Final Verification</title>
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
      max-width: 1040px;
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
    .summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 8px 16px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 12px;
    }}
    dl {{
      display: grid;
      grid-template-columns: minmax(130px, 190px) 1fr;
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
  <h1>Code-KB P5 Final Verification</h1>
  <section class="panel">
    <div class="summary">
      <div><span class="muted">Status</span><br>{_escape(guide.get("status"))}</div>
      <div><span class="muted">Readiness</span><br>{_escape(guide.get("readiness_status"))}</div>
      <div><span class="muted">Accepted</span><br>{_escape(str(bool(guide.get("accepted"))).lower())}</div>
      <div><span class="muted">Pending</span><br>{_escape(guide.get("pending_count"))}</div>
    </div>
  </section>
  <section class="panel">
    <h2>Current User Strategy</h2>
    <dl>
      <dt>Auth required</dt><dd>{_escape(_bool_text(strategy.get("current_user_auth_required", True)))}</dd>
      <dt>Token argument</dt><dd>{_escape(strategy.get("auth_token_argument") or "auth_token")}</dd>
      <dt>Confirmation target</dt><dd>{_escape(strategy.get("confirmation_target") or "current_authenticated_user")}</dd>
      <dt>Interface lookup</dt><dd>{_escape(_bool_text(strategy.get("interface_person_lookup_enabled", False)))}</dd>
    </dl>
  </section>
  <section class="panel">
    <h2>Links</h2>
    <dl>
      <dt>IM config</dt><dd><a href="{_escape_attr(links.get("im_configure_page"))}">{_escape(links.get("im_configure_page"))}</a></dd>
      <dt>Token binding fallback</dt><dd><a href="{_escape_attr(links.get("token_binding_page"))}">{_escape(links.get("token_binding_page"))}</a></dd>
      <dt>MCP setup</dt><dd><a href="{_escape_attr(links.get("setup"))}">{_escape(links.get("setup"))}</a></dd>
      <dt>Setup status</dt><dd><a href="{_escape_attr(links.get("setup_status"))}">{_escape(links.get("setup_status"))}</a></dd>
      <dt>External inputs</dt><dd><a href="{_escape_attr(links.get("external_inputs_page"))}">{_escape(links.get("external_inputs_page"))}</a></dd>
      <dt>JSON</dt><dd><a href="{_escape_attr(links.get("final_verification"))}">{_escape(links.get("final_verification"))}</a></dd>
    </dl>
  </section>
  <section class="panel">
    <h2>External State</h2>
    <div class="grid">{checks_html}</div>
  </section>
  <section class="panel">
    <h2>Verification Phases</h2>
    {phase_html}
  </section>
  <section class="panel">
    <h2>Operator Handoff</h2>
    {handoff_html}
  </section>
  <section class="panel">
    <h2>Pending External Inputs</h2>
    {task_html}
  </section>
  <section class="panel">
    <h2>Final Commands</h2>
    <ul>{final_html}</ul>
  </section>
</main>
</body>
</html>
"""


def _verification_phases(
    *,
    base_url: str,
    env_file: str,
    accepted: bool,
    external_checks: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        _phase(
            "state_snapshot",
            "Check current runtime state",
            "ok",
            [
                f"curl -sS {base_url}/diagnose/readiness",
                f"curl -sS {base_url}/diagnose/external-state",
                f"curl -sS {base_url}/diagnose/external-inputs",
            ],
        ),
        _phase(
            "im_oauth",
            "Configure self-service binding or verify IM OAuth",
            _check_status(external_checks, "im_env"),
            [
                f"open {base_url}/auth/im/self-bindings/page",
                f"open {base_url}/auth/im/configure/page",
                f"curl -sS {base_url}/auth/im/mcp/setup/status",
                f"PYTHONPATH=src python3 -m codekb diagnose-im-oauth-smoke --env-file {env_file} --api-base-url {base_url} --json",
                "/data/codekb/current/deploy/codekb-api restart",
            ],
        ),
        _phase(
            "current_user_auth",
            "Authorize the current MCP user",
            _check_status(external_checks, "mcp_auth"),
            [
                f"open {base_url}/auth/im/mcp/setup",
                f"open {base_url}/auth/im/self-bindings/page",
                f"open {base_url}/auth/im/token-bindings/page",
                f"curl -sS -X POST {base_url}/auth/im/current-user/smoke -H 'Content-Type: application/json' -d '{{\"auth_token\":\"<current-user-token>\"}}'",
                "PYTHONPATH=src CODEKB_USER_AUTH_TOKEN=<current-user-token> python3 -m codekb diagnose-current-user-smoke --respond --json",
            ],
        ),
        _phase(
            "im_delivery",
            "Verify current-user web inbox delivery",
            _check_status(external_checks, "im_delivery"),
            [
                f"open {base_url}/auth/im/confirmations/page",
                "PYTHONPATH=src CODEKB_USER_AUTH_TOKEN=<current-user-token> python3 -m codekb diagnose-im-smoke "
                f"--env-file {env_file} --json",
                f"curl -sS -X POST {base_url}/auth/im/confirmations/request -H 'Content-Type: application/json' "
                "-d '{\"auth_token\":\"<current-user-token>\",\"reason\":\"problem_solved\",\"message\":\"please confirm\"}'",
                "/data/codekb/current/deploy/codekb-confirmation-worker once",
            ],
        ),
        _phase(
            "external_samples",
            "Activate real platform samples",
            _check_status(external_checks, "external_platform_samples"),
            [
                "PYTHONPATH=src python3 -m codekb diagnose-webhook-sample-suite --json",
                f"PYTHONPATH=src python3 -m codekb diagnose-readiness --env-file {env_file} --json",
            ],
        ),
        _phase(
            "final_gate",
            "Run the final P5 gate",
            "ok" if accepted else "pending",
            [
                f"PYTHONPATH=src python3 -m codekb diagnose-p5-final-verify --env-file {env_file} --api-base-url {base_url} --output /data/codekb/logs/p5-final-verify-report.json --json",
                f"curl -sS {base_url}/diagnose/acceptance",
            ],
        ),
    ]


def _public_external_state(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report.get("status", ""),
        "ok": bool(report.get("ok")),
        "pending_checks": list(report.get("pending_checks") or []),
        "checks": [_public_check(check) for check in report.get("checks") or [] if isinstance(check, dict)],
        "secret_values_written": False,
    }


def _public_check(check: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "id",
        "status",
        "message",
        "missing_keys",
        "token_store_exists",
        "total_tokens",
        "active_tokens",
        "revoked_tokens",
        "expired_tokens",
        "token_store_status",
        "real_samples_exists",
        "samples_active",
    }
    return {key: value for key, value in check.items() if key in allowed}


def _phase(phase_id: str, title: str, status: str, commands: list[str]) -> dict[str, Any]:
    return {"id": phase_id, "title": title, "status": status, "commands": commands}


def _check_status(checks: dict[str, dict[str, Any]], check_id: str) -> str:
    return "ok" if str(checks.get(check_id, {}).get("status", "")) == "ok" else "pending"


def _render_phase(phase: dict[str, Any]) -> str:
    commands = "\n".join(f"<li><code>{_escape(command)}</code></li>" for command in phase.get("commands") or [])
    status_class = "ok" if phase.get("status") == "ok" else "warn"
    return f"""<article class="panel">
  <h3>{_escape(phase.get("title"))}</h3>
  <dl>
    <dt>Phase</dt><dd>{_escape(phase.get("id"))}</dd>
    <dt>Status</dt><dd class="{status_class}">{_escape(phase.get("status"))}</dd>
  </dl>
  <ul>{commands}</ul>
</article>"""


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


def _render_task(task: dict[str, Any]) -> str:
    return f"""<article class="panel">
  <h3>{_escape(task.get("title"))}</h3>
  <dl>
    <dt>Check</dt><dd>{_escape(task.get("check_id"))}</dd>
    <dt>Owner</dt><dd>{_escape(task.get("owner"))}</dd>
    <dt>Status</dt><dd>{_escape(task.get("status"))}</dd>
    <dt>Evidence</dt><dd>{_escape(task.get("evidence_needed"))}</dd>
  </dl>
</article>"""


def _render_check(check: dict[str, Any]) -> str:
    details = []
    for key in ("missing_keys", "active_tokens", "token_store_status", "real_samples_exists", "samples_active"):
        if key in check:
            details.append(f"<dt>{_escape(key)}</dt><dd>{_escape(check.get(key))}</dd>")
    detail_html = "\n".join(details)
    status_class = "ok" if check.get("status") == "ok" else "warn"
    return f"""<article class="panel">
  <h3>{_escape(check.get("id"))}</h3>
  <dl>
    <dt>Status</dt><dd class="{status_class}">{_escape(check.get("status"))}</dd>
    <dt>Message</dt><dd>{_escape(check.get("message"))}</dd>
    {detail_html}
  </dl>
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


def _escape(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def _escape_attr(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)
