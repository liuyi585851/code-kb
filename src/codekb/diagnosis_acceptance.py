from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .diagnosis_integrations import DEFAULT_API_BASE_URL


def build_p5_acceptance_report(
    readiness_report: dict[str, Any],
    *,
    api_base_url: str = DEFAULT_API_BASE_URL,
) -> dict[str, Any]:
    readiness_status = str(readiness_report.get("status", "") or "")
    accepted = readiness_status == "ready"
    pending_checks = list(readiness_report.get("required_actions") or [])
    return {
        "status": "accepted" if accepted else _acceptance_status(readiness_status),
        "accepted": accepted,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "readiness_status": readiness_status,
        "readiness_summary": dict(readiness_report.get("summary") or {}),
        "satisfied_checks": [
            check["id"]
            for check in readiness_report.get("checks", [])
            if isinstance(check, dict) and check.get("status") == "ok"
        ],
        "pending_checks": pending_checks,
        "external_inputs": _external_inputs(pending_checks),
        "final_verification": _final_verification(api_base_url=api_base_url),
    }


def build_p5_external_input_plan(
    readiness_report: dict[str, Any],
    *,
    api_base_url: str = DEFAULT_API_BASE_URL,
    env_file: str = "/data/codekb/state/p5-secrets.env",
    external_state_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    acceptance = build_p5_acceptance_report(readiness_report, api_base_url=api_base_url)
    external_state = _public_external_state(external_state_report)
    external_inputs = list(acceptance["external_inputs"])
    external_inputs.extend(_external_state_inputs(external_state, existing_check_ids={item["check_id"] for item in external_inputs}))
    external_inputs.sort(key=_external_input_priority)
    tasks = [
        _external_input_task(item, api_base_url=api_base_url, env_file=env_file, external_state=external_state)
        for item in external_inputs
    ]
    final_verification = acceptance["final_verification"]
    return {
        "status": "complete" if not tasks else "pending_external_inputs",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "api_base_url": api_base_url.rstrip("/"),
        "readiness_status": acceptance["readiness_status"],
        "pending_count": len(tasks),
        "setup_url": f"{api_base_url.rstrip('/')}/auth/im/mcp/setup",
        "setup_status_url": f"{api_base_url.rstrip('/')}/auth/im/mcp/setup/status",
        "external_inputs_markdown_url": f"{api_base_url.rstrip('/')}/diagnose/external-inputs.md",
        "external_inputs_page_url": f"{api_base_url.rstrip('/')}/diagnose/external-inputs/page",
        "final_verification_url": f"{api_base_url.rstrip('/')}/diagnose/final-verification",
        "final_verification_page_url": f"{api_base_url.rstrip('/')}/diagnose/final-verification/page",
        "self_binding_page_url": f"{api_base_url.rstrip('/')}/auth/im/self-bindings/page",
        "token_binding_page_url": f"{api_base_url.rstrip('/')}/auth/im/token-bindings/page",
        "web_push_inbox_url": f"{api_base_url.rstrip('/')}/auth/im/confirmations/page",
        "current_user_demo_url": f"{api_base_url.rstrip('/')}/demo/current-user",
        "webhook_demo_url": f"{api_base_url.rstrip('/')}/demo/webhook",
        "current_user_smoke_url": f"{api_base_url.rstrip('/')}/auth/im/current-user/smoke",
        "mcp_auth_strategy": _mcp_auth_strategy(),
        "external_state": external_state,
        "external_state_pending_count": len(external_state.get("pending_checks", [])),
        "tasks": tasks,
        "operator_handoff": _operator_handoff(tasks, final_verification=final_verification),
        "final_verification": final_verification,
        "secret_handling": {
            "env_file": env_file,
            "do_not_commit": True,
            "output_contains_secret_values": False,
        },
    }


def render_p5_external_input_plan_markdown(plan: dict[str, Any]) -> str:
    lines = [
        "# Code-KB P5 External Input Plan",
        "",
        f"Status: `{plan['status']}`",
        f"Readiness: `{plan['readiness_status']}`",
        f"Pending tasks: `{plan['pending_count']}`",
        f"External state: `{plan.get('external_state', {}).get('status', 'unknown')}`",
        f"External state pending checks: `{plan.get('external_state_pending_count', 0)}`",
        f"Setup URL: `{plan['setup_url']}`",
        f"Setup status URL: `{plan['setup_status_url']}`",
        f"External inputs Markdown: `{plan['external_inputs_markdown_url']}`",
        f"External inputs page: `{plan['external_inputs_page_url']}`",
        f"Final verification JSON: `{plan['final_verification_url']}`",
        f"Final verification page: `{plan['final_verification_page_url']}`",
        f"Self binding page: `{plan['self_binding_page_url']}`",
        f"Token binding fallback page: `{plan['token_binding_page_url']}`",
        f"Web push inbox: `{plan['web_push_inbox_url']}`",
        f"Current user demo: `{plan['current_user_demo_url']}`",
        f"Webhook demo: `{plan['webhook_demo_url']}`",
        f"Current user smoke URL: `{plan['current_user_smoke_url']}`",
        f"Server env file: `{plan['secret_handling']['env_file']}`",
        "",
        "MCP auth strategy: current user must authorize first; MCP uses the bound `auth_token`; confirmations go to the current authenticated user, not an interface-person lookup.",
        "",
        "This plan lists variable names, commands, and evidence only. It does not contain secret values.",
        "",
    ]
    handoff = dict(plan.get("operator_handoff") or {})
    next_action = dict(handoff.get("next_action") or {})
    lines.extend(
        [
            "## Operator handoff",
            "",
            f"- Ordered task ids: `{','.join(handoff.get('ordered_task_ids') or [])}`",
            f"- Next action: `{next_action.get('check_id', '')}` {next_action.get('title', '')}".rstrip(),
            "",
        ]
    )
    owner_groups = dict(handoff.get("by_owner") or {})
    if owner_groups:
        lines.append("Tasks by owner:")
        for owner, task_ids in sorted(owner_groups.items()):
            lines.append(f"- `{owner}`: `{','.join(task_ids)}`")
        lines.append("")
    completion_criteria = list(handoff.get("completion_criteria") or [])
    if completion_criteria:
        lines.append("Completion criteria:")
        lines.extend(f"- {criterion}" for criterion in completion_criteria)
        lines.append("")
    for index, task in enumerate(plan["tasks"], start=1):
        lines.extend(
            [
                f"## {index}. {task['title']}",
                "",
                f"- Check: `{task['check_id']}`",
                f"- Owner: `{task['owner']}`",
                f"- Status: `{task['status']}`",
                f"- Evidence needed: {task['evidence_needed']}",
                f"- Remediation: {task['remediation']}",
                "",
            ]
        )
        if task["required_inputs"]:
            lines.append("Required inputs:")
            lines.extend(f"- `{item}`" for item in task["required_inputs"])
            lines.append("")
        if task["safe_commands"]:
            lines.append("Safe commands:")
            lines.extend(f"- `{command}`" for command in task["safe_commands"])
            lines.append("")
        if task["verification_commands"]:
            lines.append("Verification commands:")
            lines.extend(f"- `{command}`" for command in task["verification_commands"])
            lines.append("")
        if task["notes"]:
            lines.append("Notes:")
            lines.extend(f"- {note}" for note in task["notes"])
            lines.append("")
    if not plan["tasks"]:
        lines.extend(["No external input tasks remain.", ""])
    lines.append("Final verification:")
    lines.extend(f"- `{item['command']}`" for item in plan["final_verification"])
    lines.append("")
    return "\n".join(lines)


def _acceptance_status(readiness_status: str) -> str:
    if readiness_status == "blocked":
        return "blocked"
    return "pending_external_inputs"


def _external_input_task(
    item: dict[str, Any],
    *,
    api_base_url: str,
    env_file: str,
    external_state: dict[str, Any],
) -> dict[str, Any]:
    check_id = str(item.get("check_id", "") or "")
    template = _EXTERNAL_INPUT_TASKS.get(check_id, _default_external_input_task())
    base_url = api_base_url.rstrip("/")
    paths = dict(external_state.get("paths") or {})
    replacements = {
        "api_base_url": base_url,
        "setup_url": f"{base_url}/auth/im/mcp/setup",
        "setup_status_url": f"{base_url}/auth/im/mcp/setup/status",
        "current_user_smoke_url": f"{base_url}/auth/im/current-user/smoke",
        "current_user_demo_url": f"{base_url}/demo/current-user",
        "webhook_demo_url": f"{base_url}/demo/webhook",
        "self_binding_page_url": f"{base_url}/auth/im/self-bindings/page",
        "token_binding_page_url": f"{base_url}/auth/im/token-bindings/page",
        "web_push_inbox_url": f"{base_url}/auth/im/confirmations/page",
        "oauth_callback_url": f"{base_url}/auth/im/oauth/callback",
        "env_file": env_file,
        "im_template": str(paths.get("im_template") or "/data/codekb/state/p5-handoff/im-config.todo.env"),
    }
    return {
        "check_id": check_id,
        "title": template["title"],
        "owner": str(item.get("owner", "") or template["owner"]),
        "status": str(item.get("status", "") or ""),
        "evidence_needed": str(item.get("evidence_needed", "") or template["evidence_needed"]),
        "message": str(item.get("message", "") or ""),
        "remediation": str(item.get("remediation", "") or template["remediation"]),
        "required_inputs": [_format_template(value, replacements) for value in template["required_inputs"]],
        "safe_commands": [_format_template(value, replacements) for value in template["safe_commands"]],
        "verification_commands": [_format_template(value, replacements) for value in template["verification_commands"]],
        "notes": [_format_template(value, replacements) for value in template["notes"]],
    }


def _external_inputs(pending_checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    inputs: list[dict[str, Any]] = []
    for check in pending_checks:
        check_id = str(check.get("id", "") or "")
        if not check_id:
            continue
        known = _KNOWN_EXTERNAL_INPUTS.get(check_id, {})
        inputs.append(
            {
                "check_id": check_id,
                "status": str(check.get("status", "") or ""),
                "owner": known.get("owner", "engineering"),
                "evidence_needed": known.get("evidence_needed", "Resolve the readiness remediation and rerun diagnose-acceptance."),
                "message": str(check.get("message", "") or ""),
                "remediation": str(check.get("remediation", "") or ""),
            }
        )
    return inputs


def _external_input_priority(item: dict[str, Any]) -> tuple[int, str]:
    check_id = str(item.get("check_id", "") or "")
    return (_TASK_ORDER.get(check_id, 100), check_id)


def _operator_handoff(tasks: list[dict[str, Any]], *, final_verification: list[dict[str, Any]]) -> dict[str, Any]:
    ordered_task_ids = [str(task.get("check_id", "") or "") for task in tasks if task.get("check_id")]
    by_owner: dict[str, list[str]] = {}
    for task in tasks:
        owner = str(task.get("owner", "") or "engineering")
        check_id = str(task.get("check_id", "") or "")
        if not check_id:
            continue
        by_owner.setdefault(owner, []).append(check_id)
    next_action = _next_action(tasks)
    return {
        "ordered_task_ids": ordered_task_ids,
        "by_owner": by_owner,
        "next_action": next_action,
        "completion_criteria": [
            "All external input tasks are resolved and /diagnose/external-inputs returns pending_count=0.",
            "GET /diagnose/external-state returns status=ready and secret_values_written=false.",
            "GET /auth/im/mcp/setup/status reports at least one active current-user token binding.",
            "diagnose-p5-final-verify returns accepted=true with failed_required=[] and pending_required=[].",
            "Current-user confirmation can be created, routed to the auth_token user, and responded to by that same user.",
        ],
        "final_gate_commands": [
            item["command"]
            for item in final_verification
            if isinstance(item, dict)
            and item.get("id") in {"acceptance", "external_state", "final_verify", "http_acceptance", "http_external_state"}
        ],
        "secret_values_written": False,
    }


def _next_action(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    if not tasks:
        return {}
    task = tasks[0]
    return {
        "check_id": str(task.get("check_id", "") or ""),
        "title": str(task.get("title", "") or ""),
        "owner": str(task.get("owner", "") or ""),
        "evidence_needed": str(task.get("evidence_needed", "") or ""),
        "safe_commands": list(task.get("safe_commands") or [])[:2],
        "verification_commands": list(task.get("verification_commands") or [])[:2],
    }


def _external_state_inputs(
    external_state: dict[str, Any],
    *,
    existing_check_ids: set[str],
) -> list[dict[str, Any]]:
    if not external_state:
        return []
    covered_ids = set(existing_check_ids)
    if "im_oauth" in covered_ids:
        covered_ids.add("im_env")
    inputs: list[dict[str, Any]] = []
    for check in external_state.get("checks") or []:
        if not isinstance(check, dict) or check.get("status") == "ok":
            continue
        check_id = str(check.get("id", "") or "")
        if not check_id or check_id in covered_ids:
            continue
        known = _KNOWN_EXTERNAL_INPUTS.get(check_id, {})
        inputs.append(
            {
                "check_id": check_id,
                "status": str(check.get("status", "") or ""),
                "owner": known.get("owner", "engineering"),
                "evidence_needed": known.get("evidence_needed", "Resolve the external-state evidence and rerun final verification."),
                "message": str(check.get("message", "") or ""),
                "remediation": known.get("remediation", ""),
            }
        )
        covered_ids.add(check_id)
    return inputs


def _public_external_state(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {
            "status": "unknown",
            "ok": False,
            "pending_checks": [],
            "checks": [],
            "paths": {},
            "secret_values_written": False,
        }
    return {
        "status": str(report.get("status", "") or ""),
        "ok": bool(report.get("ok")),
        "pending_checks": list(report.get("pending_checks") or []),
        "checks": [_public_external_state_check(check) for check in report.get("checks") or [] if isinstance(check, dict)],
        "paths": _public_external_state_paths(dict(report.get("paths") or {})),
        "secret_values_written": False,
    }


def _public_external_state_check(check: dict[str, Any]) -> dict[str, Any]:
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


def _public_external_state_paths(paths: dict[str, Any]) -> dict[str, str]:
    return {
        key: str(paths.get(key, "") or "")
        for key in ("env_file", "im_template", "token_store", "real_samples")
        if paths.get(key, "") not in (None, "")
    }


def _format_template(value: str, replacements: dict[str, str]) -> str:
    formatted = value
    for key, replacement in replacements.items():
        formatted = formatted.replace("{" + key + "}", replacement)
    return formatted


def _mcp_auth_strategy() -> dict[str, Any]:
    return {
        "current_user_auth_required": True,
        "setup_page_required": True,
        "auth_token_argument": "auth_token",
        "token_binding": "im_oauth_or_web_setup",
        "confirmation_target": "current_authenticated_user",
        "interface_person_lookup_enabled": False,
        "static_mcp_token_production_allowed": False,
    }


def _final_verification(*, api_base_url: str) -> list[dict[str, Any]]:
    base_url = api_base_url.rstrip("/")
    return [
        {
            "id": "unit_tests",
            "description": "Run the repository test suite on the deployed release.",
            "command": "PYTHONPATH=src python3 -m unittest discover -s tests",
        },
        {
            "id": "quality_gate",
            "description": "Run the answer quality gate against the P1 Wiki manifest.",
            "command": (
                "PYTHONPATH=src python3 -m codekb quality-check "
                "--fixtures data/fixtures/sample_corpus.jsonl "
                "--prefix REL --prefix TST --prefix INC --skip-missing-expected"
            ),
        },
        {
            "id": "readiness",
            "description": "Confirm readiness is fully ready with no required actions.",
            "command": "PYTHONPATH=src python3 -m codekb diagnose-readiness --env-file /data/codekb/state/p5-secrets.env --json",
        },
        {
            "id": "acceptance",
            "description": "Run this final acceptance gate; it exits non-zero until P5 is accepted.",
            "command": "PYTHONPATH=src python3 -m codekb diagnose-acceptance --env-file /data/codekb/state/p5-secrets.env --json",
        },
        {
            "id": "external_state",
            "description": "Confirm all external-state evidence is ready and no secret values are exposed.",
            "command": "PYTHONPATH=src python3 -m codekb diagnose-p5-external-state --env-file /data/codekb/state/p5-secrets.env --json",
        },
        {
            "id": "final_verify",
            "description": "Run the consolidated P5 final verification runner and keep the JSON evidence.",
            "command": (
                "PYTHONPATH=src python3 -m codekb diagnose-p5-final-verify "
                f"--env-file /data/codekb/state/p5-secrets.env --api-base-url {base_url} --json"
            ),
        },
        {
            "id": "http_readiness",
            "description": "Check the deployed API readiness endpoint.",
            "command": f"curl -sS {base_url}/diagnose/readiness",
        },
        {
            "id": "http_acceptance",
            "description": "Check the deployed API acceptance endpoint.",
            "command": f"curl -sS {base_url}/diagnose/acceptance",
        },
        {
            "id": "http_external_state",
            "description": "Check the deployed external-state evidence endpoint.",
            "command": f"curl -sS {base_url}/diagnose/external-state",
        },
        {
            "id": "http_mcp_setup_status",
            "description": "Check the current-user MCP setup status endpoint used by users and IM admins.",
            "command": f"curl -sS {base_url}/auth/im/mcp/setup/status",
        },
        {
            "id": "http_current_user_smoke",
            "description": "After a real user has a token, run the browser/API self-test that creates a current-user confirmation and validates dry-run routing.",
            "command": (
                f"curl -sS -X POST {base_url}/auth/im/current-user/smoke "
                "-H 'Content-Type: application/json' "
                "-d '{\"auth_token\":\"<current-user-token>\"}'"
            ),
        },
        {
            "id": "http_confirmation_request",
            "description": "After a real user has a token, verify the explicit current-user confirmation request endpoint used after problem solved or interaction complete moments.",
            "command": (
                f"curl -sS -X POST {base_url}/auth/im/confirmations/request "
                "-H 'Content-Type: application/json' "
                "-d '{\"auth_token\":\"<current-user-token>\",\"reason\":\"problem_solved\","
                "\"message\":\"请确认本次问题是否已解决\",\"payload\":{\"source\":\"manual_acceptance\"}}'"
            ),
        },
        {
            "id": "sample_suite",
            "description": "Validate the active real webhook sample suite.",
            "command": "PYTHONPATH=src python3 -m codekb diagnose-webhook-sample-suite --json",
        },
        {
            "id": "confirmation_worker",
            "description": "Validate or execute current-user IM confirmation delivery after credentials are configured.",
            "command": "/data/codekb/current/deploy/codekb-confirmation-worker once",
        },
        {
            "id": "im_oauth_smoke",
            "description": "Verify IM OAuth setup URL, state signing, and token-store status without exposing secrets.",
            "command": "PYTHONPATH=src python3 -m codekb diagnose-im-oauth-smoke --env-file /data/codekb/state/p5-secrets.env --json",
        },
        {
            "id": "im_smoke",
            "description": "Verify IM credentials and current-user confirmation delivery without exposing secrets.",
            "command": (
                "PYTHONPATH=src CODEKB_USER_AUTH_TOKEN=<current-user-token> "
                "python3 -m codekb diagnose-im-smoke "
                "--env-file /data/codekb/state/p5-secrets.env --json"
            ),
        },
        {
            "id": "current_user_smoke",
            "description": "A real current user completes IM OAuth, runs MCP diagnosis, receives a confirmation, and records a response.",
            "command": (
                "Open https://<kb-host>/auth/im/mcp/setup, then run "
                "PYTHONPATH=src CODEKB_USER_AUTH_TOKEN=<current-user-token> "
                "python3 -m codekb diagnose-current-user-smoke --respond --json"
            ),
        },
    ]


_KNOWN_EXTERNAL_INPUTS = {
    "im_template": {
        "owner": "im_admin",
        "evidence_needed": "The IM configuration template or server env contains all required OAuth keys before applying to the server env file.",
        "remediation": "Generate or complete the IM config template, then apply it to the server-only env file.",
    },
    "mcp_auth": {
        "owner": "current_user",
        "evidence_needed": "At least one real user completes self-service binding or IM OAuth/setup and the token store has an active current-user binding.",
    },
    "im_oauth": {
        "owner": "im_admin",
        "evidence_needed": "IM app corp id, agent id, app secret, redirect URI, and OAuth state secret are configured in the server env file.",
    },
    "im_delivery": {
        "owner": "im_admin",
        "evidence_needed": "Web push inbox is available now; IM/TOF send is enabled later only after approval.",
    },
    "webhook_security": {
        "owner": "platform_admin",
        "evidence_needed": "Diagnose webhook shared token is configured and platform callers send X-CodeKB-Token.",
    },
    "auth_admin_security": {
        "owner": "platform_admin",
        "evidence_needed": "Auth admin token is configured for sample import and token-management endpoints.",
    },
    "external_platform_samples": {
        "owner": "platform_integrator",
        "evidence_needed": "Sanitized real platform webhook samples are active and the sample suite passes.",
    },
}


_TASK_ORDER = {
    "webhook_security": 10,
    "auth_admin_security": 20,
    "im_template": 30,
    "im_oauth": 40,
    "mcp_auth": 50,
    "im_delivery": 60,
    "external_platform_samples": 70,
}


def _default_external_input_task() -> dict[str, Any]:
    return {
        "title": "Resolve readiness action",
        "owner": "engineering",
        "evidence_needed": "Resolve the readiness remediation and rerun diagnose-acceptance.",
        "remediation": "Follow the readiness remediation for this check.",
        "required_inputs": [],
        "safe_commands": [
            "PYTHONPATH=src python3 -m codekb diagnose-readiness --env-file {env_file} --json",
        ],
        "verification_commands": [
            "PYTHONPATH=src python3 -m codekb diagnose-acceptance --env-file {env_file} --json",
        ],
        "notes": [],
    }


_EXTERNAL_INPUT_TASKS = {
    "im_template": {
        "title": "Complete the IM config template",
        "owner": "im_admin",
        "evidence_needed": _KNOWN_EXTERNAL_INPUTS["im_template"]["evidence_needed"],
        "remediation": _KNOWN_EXTERNAL_INPUTS["im_template"]["remediation"],
        "required_inputs": [
            "CODEKB_IM_CORP_ID",
            "CODEKB_IM_AGENT_ID",
            "CODEKB_IM_APP_SECRET",
            "CODEKB_IM_OAUTH_STATE_SECRET",
            "Template path: {im_template}",
            "Allowed callback URL: {oauth_callback_url}",
        ],
        "safe_commands": [
            "PYTHONPATH=src python3 -m codekb diagnose-im-configure --env-file {env_file} --template-output {im_template} --api-base-url {api_base_url} --json",
            "chmod 600 {im_template}",
            "open {api_base_url}/auth/im/configure/page",
        ],
        "verification_commands": [
            "PYTHONPATH=src python3 -m codekb diagnose-p5-external-state --env-file {env_file} --im-template {im_template} --json",
            "PYTHONPATH=src python3 -m codekb diagnose-im-configure --env-file {env_file} --from-template {im_template} --json",
        ],
        "notes": [
            "The template is server-only handoff material and must not be committed.",
            "The external input plan prints key names and paths only, never secret values.",
        ],
    },
    "mcp_auth": {
        "title": "Authorize one real current user for MCP",
        "owner": "current_user",
        "evidence_needed": _KNOWN_EXTERNAL_INPUTS["mcp_auth"]["evidence_needed"],
        "remediation": "Open the setup page, complete IM OAuth or web token binding, then run the current-user smoke with that user's token.",
        "required_inputs": [
            "A real current user who can access {setup_url}",
            "The user's one-time MCP auth_token from the setup page",
        ],
        "safe_commands": [
            "curl -sS {setup_status_url}",
            "open {setup_url}",
            "open {self_binding_page_url}",
            "open {token_binding_page_url}",
            "open {web_push_inbox_url}",
            "curl -sS -X POST {current_user_smoke_url} -H 'Content-Type: application/json' -d '{\"auth_token\":\"<current-user-token>\"}'",
            "PYTHONPATH=src CODEKB_USER_AUTH_TOKEN=<current-user-token> python3 -m codekb diagnose-current-user-smoke --respond --json",
        ],
        "verification_commands": [
            "curl -sS {setup_status_url}",
            "curl -sS -X POST {current_user_smoke_url} -H 'Content-Type: application/json' -d '{\"auth_token\":\"<current-user-token>\"}'",
            "PYTHONPATH=src python3 -m codekb diagnose-readiness --env-file {env_file} --json",
            "PYTHONPATH=src python3 -m codekb diagnose-acceptance --env-file {env_file} --json",
        ],
        "notes": [
            "The token is shown to the user once and the server stores only its hash.",
            "If IM OAuth is unavailable, use the self-service binding page with a short binding code.",
            "Use the web push inbox to see and respond to messages while IM/TOF delivery approval is pending.",
            "MCP clients should show JSON-RPC error.data.setup_url when auth_token is missing or invalid.",
        ],
    },
    "im_oauth": {
        "title": "Configure IM OAuth",
        "owner": "im_admin",
        "evidence_needed": _KNOWN_EXTERNAL_INPUTS["im_oauth"]["evidence_needed"],
        "remediation": "Add IM OAuth variables to the server-only env file and allow the callback URL in the IM app console.",
        "required_inputs": [
            "CODEKB_IM_CORP_ID",
            "CODEKB_IM_AGENT_ID",
            "CODEKB_IM_APP_SECRET",
            "CODEKB_IM_OAUTH_STATE_SECRET",
            "Allowed callback URL: {oauth_callback_url}",
        ],
        "safe_commands": [
            "curl -sS {setup_status_url}",
            "PYTHONPATH=src python3 -m codekb diagnose-im-configure --env-file {env_file} --template-output /data/codekb/state/im-config.todo.env --api-base-url {api_base_url} --json",
            "PYTHONPATH=src python3 -m codekb diagnose-im-configure --env-file {env_file} --from-template /data/codekb/state/im-config.todo.env --apply --json",
            "PYTHONPATH=src python3 -m codekb diagnose-im-oauth-smoke --env-file {env_file} --json",
            "/data/codekb/current/deploy/codekb-api restart",
        ],
        "verification_commands": [
            "curl -sS {setup_status_url}",
            "PYTHONPATH=src python3 -m codekb diagnose-im-oauth-smoke --env-file {env_file} --check-credentials --json",
            "curl -sS -I '{api_base_url}/auth/im/oauth/login?next=/auth/im/mcp/setup'",
        ],
        "notes": [
            "Do not paste app_secret into chat or commit it.",
            "The OAuth smoke reports hashes and statuses only.",
        ],
    },
    "im_delivery": {
        "title": "Enable IM/TOF confirmation delivery",
        "owner": "im_admin",
        "evidence_needed": _KNOWN_EXTERNAL_INPUTS["im_delivery"]["evidence_needed"],
        "remediation": "Keep using the web push inbox until real IM/TOF sends are approved, then enable real sends explicitly.",
        "required_inputs": [
            "CODEKB_ENABLE_IM_SEND=1",
            "CODEKB_IM_CORP_ID",
            "CODEKB_IM_AGENT_ID",
            "CODEKB_IM_APP_SECRET",
            "Optional CODEKB_IM_CONFIRM_URL_BASE={api_base_url}/auth/im/confirmations/page",
        ],
        "safe_commands": [
            "open {web_push_inbox_url}",
            "PYTHONPATH=src python3 -m codekb diagnose-im-configure --env-file {env_file} --enable-send --confirm-real-send --apply --json",
            "PYTHONPATH=src CODEKB_USER_AUTH_TOKEN=<current-user-token> python3 -m codekb diagnose-im-smoke --env-file {env_file} --json",
            "/data/codekb/current/deploy/codekb-confirmation-worker once",
        ],
        "verification_commands": [
            "PYTHONPATH=src CODEKB_USER_AUTH_TOKEN=<current-user-token> python3 -m codekb diagnose-im-smoke --env-file {env_file} --execute --json",
            "/data/codekb/current/deploy/codekb-confirmation-worker start",
        ],
        "notes": [
            "Real send execution still requires CODEKB_ENABLE_IM_SEND=1.",
            "Delivery log prevents repeated worker loops from resending the same confirmation.",
        ],
    },
    "external_platform_samples": {
        "title": "Activate real external webhook samples",
        "owner": "platform_integrator",
        "evidence_needed": _KNOWN_EXTERNAL_INPUTS["external_platform_samples"]["evidence_needed"],
        "remediation": "Confirm the generated real sample suite is sanitized real platform data, activate it, and restart the API.",
        "required_inputs": [
            "One sanitized real webhook payload per enabled platform source",
            "/data/codekb/state/diagnose-webhook-samples.real.yaml confirmed as real platform data",
        ],
        "safe_commands": [
            "PYTHONPATH=src python3 -m codekb diagnose-webhook-sample-suite --samples /data/codekb/state/diagnose-webhook-samples.real.yaml --json",
            "PYTHONPATH=src python3 -m codekb diagnose-webhook-sample-activate --env-file {env_file} --apply --confirm-real-samples --json",
            "/data/codekb/current/deploy/codekb-api restart",
        ],
        "verification_commands": [
            "PYTHONPATH=src python3 -m codekb diagnose-webhook-sample-suite --json",
            "PYTHONPATH=src python3 -m codekb diagnose-readiness --env-file {env_file} --json",
        ],
        "notes": [
            "Raw payloads and secrets must not be committed.",
            "Activation writes only server-only env names and paths.",
        ],
    },
    "webhook_security": {
        "title": "Configure diagnose webhook shared token",
        "owner": "platform_admin",
        "evidence_needed": _KNOWN_EXTERNAL_INPUTS["webhook_security"]["evidence_needed"],
        "remediation": "Generate or set the shared webhook token in the server-only env file before exposing webhook endpoints.",
        "required_inputs": ["CODEKB_DIAGNOSE_WEBHOOK_TOKEN"],
        "safe_commands": [
            "PYTHONPATH=src python3 -m codekb diagnose-security-bootstrap --output {env_file} --force --json",
        ],
        "verification_commands": [
            "PYTHONPATH=src python3 -m codekb diagnose-readiness --env-file {env_file} --json",
        ],
        "notes": ["Platform callers must send X-CodeKB-Token."],
    },
    "auth_admin_security": {
        "title": "Configure admin token for controlled management endpoints",
        "owner": "platform_admin",
        "evidence_needed": _KNOWN_EXTERNAL_INPUTS["auth_admin_security"]["evidence_needed"],
        "remediation": "Set an admin token before using token-management, confirmation summary, or real sample import endpoints.",
        "required_inputs": ["CODEKB_AUTH_ADMIN_TOKEN"],
        "safe_commands": [
            "PYTHONPATH=src python3 -m codekb diagnose-security-bootstrap --output {env_file} --force --json",
        ],
        "verification_commands": [
            "PYTHONPATH=src python3 -m codekb diagnose-readiness --env-file {env_file} --json",
        ],
        "notes": ["Do not share the admin token with ordinary users."],
    },
}
