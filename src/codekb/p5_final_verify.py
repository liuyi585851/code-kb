from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from .diagnosis_integrations import DEFAULT_API_BASE_URL
from .redaction import redact_sensitive_text


Runner = Callable[[Sequence[str], int, dict[str, str]], tuple[int, str, str]]
_CURRENT_USER_TOKEN_COMMAND_IDS = {
    "im_smoke",
    "current_user_smoke",
    "http_current_user_smoke",
    "http_confirmation_request",
}


@dataclass(frozen=True)
class VerificationCommand:
    id: str
    description: str
    args: tuple[str, ...]
    timeout_seconds: int = 120
    required: bool = True
    slow: bool = False
    env: dict[str, str] | None = None
    skip_reason: str = ""


def run_p5_final_verification(
    *,
    env_file: str = "/data/codekb/state/p5-secrets.env",
    api_base_url: str = DEFAULT_API_BASE_URL,
    python: str = "python3",
    include_slow: bool = True,
    include_http: bool = True,
    include_worker: bool = True,
    confirmation_worker: str = "/data/codekb/current/deploy/codekb-confirmation-worker",
    runner: Runner | None = None,
) -> dict[str, Any]:
    runner = runner or _subprocess_runner
    commands = build_p5_final_verification_commands(
        env_file=env_file,
        api_base_url=api_base_url,
        python=python,
        include_slow=include_slow,
        include_http=include_http,
        include_worker=include_worker,
        confirmation_worker=confirmation_worker,
    )
    results = [_run_command(command, runner=runner) for command in commands]
    accepted = _accepted(results)
    failed_required = [result for result in results if result["required"] and result["status"] == "failed"]
    pending_required = [result for result in results if result["required"] and result["status"] == "pending"]
    incomplete_required = [
        result for result in results if result["required"] and result["status"] not in {"passed", "accepted"}
    ]
    status = _overall_status(
        results,
        accepted=accepted,
        failed_required=failed_required,
        pending_required=pending_required,
    )
    external_input_handoff = _external_input_handoff(results)
    return {
        "status": status,
        "ok": status == "accepted",
        "accepted": accepted,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "env_file": env_file,
        "api_base_url": api_base_url.rstrip("/"),
        "summary": {
            "total": len(results),
            "passed": sum(1 for result in results if result["status"] in {"passed", "accepted"}),
            "pending": sum(1 for result in results if result["status"] == "pending"),
            "failed": sum(1 for result in results if result["status"] == "failed"),
            "skipped": sum(1 for result in results if result["status"] == "skipped"),
            "required_failed": len(failed_required),
            "required_pending": len(pending_required),
            "required_incomplete": len(incomplete_required),
        },
        "results": results,
        "failed_required": [result["id"] for result in failed_required],
        "pending_required": [result["id"] for result in pending_required],
        "incomplete_required": [result["id"] for result in incomplete_required],
        "next_steps": _next_steps(results, status=status),
        "external_input_handoff": external_input_handoff,
        "secret_values_written": False,
    }


def build_p5_final_verification_commands(
    *,
    env_file: str,
    api_base_url: str,
    python: str = "python3",
    include_slow: bool = True,
    include_http: bool = True,
    include_worker: bool = True,
    confirmation_worker: str = "/data/codekb/current/deploy/codekb-confirmation-worker",
) -> tuple[VerificationCommand, ...]:
    base_url = api_base_url.rstrip("/")
    command_env = {"PYTHONPATH": "src"}
    commands: list[VerificationCommand] = [
        VerificationCommand(
            id="unit_tests",
            description="Run repository tests on the deployed release.",
            args=(python, "-m", "unittest", "discover", "-s", "tests"),
            timeout_seconds=180,
            slow=True,
            env=command_env,
        ),
        VerificationCommand(
            id="quality_gate",
            description="Run P2 answer quality gate.",
            args=(
                python,
                "-m",
                "codekb",
                "quality-check",
                "--fixtures",
                "data/fixtures/sample_corpus.jsonl",
                "--prefix",
                "REL",
                "--prefix",
                "TST",
                "--prefix",
                "INC",
                "--skip-missing-expected",
            ),
            env=command_env,
        ),
        VerificationCommand(
            id="p3_usecase_smoke",
            description="Verify the P3 knowledge loop: ingest, audit, index rebuild, ask, and publish outbox dry-run.",
            args=(
                python,
                "-m",
                "codekb",
                "p3-usecase-smoke",
                "--publish-mode",
                "index_page",
                "--index-docid",
                "401",
                "--json",
            ),
            env=command_env,
        ),
        VerificationCommand(
            id="readiness",
            description="Check P5 readiness.",
            args=(python, "-m", "codekb", "diagnose-readiness", "--env-file", env_file, "--json"),
            env=command_env,
        ),
        VerificationCommand(
            id="external_inputs",
            description="Render remaining external input tasks.",
            args=(python, "-m", "codekb", "diagnose-external-inputs", "--env-file", env_file, "--json"),
            required=False,
            env=command_env,
        ),
        VerificationCommand(
            id="external_state",
            description="Inspect concrete external input evidence without exposing secret values.",
            args=(python, "-m", "codekb", "diagnose-p5-external-state", "--env-file", env_file, "--json"),
            required=False,
            env=command_env,
        ),
        VerificationCommand(
            id="external_input_plan_alignment",
            description="Verify external-input task plan covers concrete external-state pending checks.",
            args=_external_input_plan_alignment_args(python, env_file),
            required=False,
            env=command_env,
        ),
        VerificationCommand(
            id="handoff_bundle_smoke",
            description="Refresh and validate the P5 external handoff bundle, including webhook current-user confirmation guidance.",
            args=_handoff_bundle_smoke_args(
                python,
                str(_handoff_bundle_output_dir(env_file)),
                env_file,
                base_url,
            ),
            required=False,
            env=command_env,
        ),
        VerificationCommand(
            id="acceptance",
            description="Run the final P5 acceptance gate.",
            args=(python, "-m", "codekb", "diagnose-acceptance", "--env-file", env_file, "--json"),
            env=command_env,
        ),
        VerificationCommand(
            id="sample_suite",
            description="Validate active webhook sample suite.",
            args=(python, "-m", "codekb", "diagnose-webhook-sample-suite", "--json"),
            env=command_env,
        ),
        VerificationCommand(
            id="mcp_auth_error_fallback",
            description="Verify MCP auth errors expose setup, OAuth, and token binding fallback URLs without leaking tokens.",
            args=_mcp_auth_error_fallback_args(python, base_url),
            env=command_env,
        ),
        VerificationCommand(
            id="mcp_static_token_default_reject",
            description="Verify shared static MCP token auth is disabled by default when no token store is configured.",
            args=_mcp_static_token_default_reject_args(python, base_url),
            env=command_env,
        ),
        VerificationCommand(
            id="mcp_token_store_static_reject",
            description="Verify token-store MCP auth rejects shared static tokens and accepts only bound current-user tokens.",
            args=_mcp_token_store_static_reject_args(python, base_url),
            env=command_env,
        ),
        VerificationCommand(
            id="mcp_explicit_confirmation_reasons",
            description="Verify explicit MCP confirmations for interaction complete and problem solved route to the calling current user.",
            args=_mcp_explicit_confirmation_reasons_args(python, base_url),
            env=command_env,
        ),
        VerificationCommand(
            id="im_oauth_smoke",
            description="Verify IM OAuth state, authorize URL, credentials status, and token-store summary.",
            args=(
                python,
                "-m",
                "codekb",
                "diagnose-im-oauth-smoke",
                "--env-file",
                env_file,
                "--api-base-url",
                base_url,
                "--json",
            ),
            required=False,
            env=command_env,
        ),
        VerificationCommand(
            id="im_delivery_config_validation",
            description="Verify real IM sends require numeric agent id and an absolute confirmation URL before delivery.",
            args=_im_delivery_config_validation_args(python),
            env=command_env,
        ),
        _current_user_token_command(
            command_id="im_smoke",
            description="Verify current-user IM delivery route in dry-run mode when a user token is provided.",
            args=(python, "-m", "codekb", "diagnose-im-smoke", "--env-file", env_file, "--json"),
            env=command_env,
        ),
        _current_user_token_command(
            command_id="current_user_smoke",
            description="Run MCP diagnosis, confirmation outbox, delivery dry-run, and response smoke when a user token is provided.",
            args=(
                python,
                "-m",
                "codekb",
                "diagnose-current-user-smoke",
                "--respond",
                "--api-base-url",
                base_url,
                "--json",
            ),
            env=command_env,
        ),
    ]
    if include_http:
        commands.extend(
            [
                VerificationCommand(
                    id="http_readiness",
                    description="Check deployed readiness endpoint.",
                    args=_http_get_args(python, f"{base_url}/diagnose/readiness"),
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_external_state",
                    description="Check deployed external state endpoint without exposing secret values.",
                    args=_http_get_args(python, f"{base_url}/diagnose/external-state"),
                    required=False,
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_webhook_token_guard",
                    description="Check webhook validate rejects missing shared token and accepts the configured token without leaking it.",
                    args=_http_webhook_token_guard_args(
                        python,
                        f"{base_url}/diagnose/webhook/code_review/validate",
                        env_file,
                    ),
                    required=False,
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_webhook_sample_import_smoke",
                    description="Check real webhook sample import requires admin token, sanitizes payload secrets, and restores sample state.",
                    args=_http_webhook_sample_import_smoke_args(
                        python,
                        f"{base_url}/diagnose/webhook/code_review/sample-import",
                        env_file,
                    ),
                    required=False,
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_mcp_setup_status",
                    description="Check current-user MCP setup status endpoint.",
                    args=_http_get_args(python, f"{base_url}/auth/im/mcp/setup/status"),
                    required=False,
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_mcp_setup_page",
                    description="Check current-user MCP setup page is reachable.",
                    args=_http_get_args(python, f"{base_url}/auth/im/mcp/setup"),
                    required=False,
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_im_oauth_callback_guard",
                    description="Verify IM OAuth callback rejects invalid state without issuing a current-user token.",
                    args=_http_im_oauth_callback_guard_args(
                        python,
                        f"{base_url}/auth/im/oauth/callback",
                        env_file,
                    ),
                    required=False,
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_token_binding_page",
                    description="Check admin-controlled current-user token binding fallback page is reachable.",
                    args=_http_get_args(python, f"{base_url}/auth/im/token-bindings/page"),
                    required=False,
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_token_binding_fallback_smoke",
                    description="Verify token binding fallback management requires admin auth, masks secrets, stores only hashes, then restores token state.",
                    args=_http_token_binding_fallback_smoke_args(
                        python,
                        f"{base_url}/auth/im/token-bindings",
                        env_file,
                    ),
                    required=False,
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_token_revoke_auth_smoke",
                    description="Issue a temporary current-user token, revoke it, and verify HTTP current-user/confirmation APIs reject it.",
                    args=_http_token_revoke_auth_smoke_args(
                        python,
                        f"{base_url}/auth/im/token-bindings",
                        f"{base_url}/auth/im/current-user/status",
                        f"{base_url}/auth/im/confirmations",
                        env_file,
                    ),
                    required=False,
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_explicit_confirmation_reasons_smoke",
                    description="Verify HTTP explicit confirmations for interaction complete and problem solved route to temporary current-user tokens.",
                    args=_http_explicit_confirmation_reasons_smoke_args(
                        python,
                        f"{base_url}/auth/im/confirmations/request",
                        f"{base_url}/auth/im/token-bindings",
                        env_file,
                    ),
                    required=False,
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_confirmation_response_auth_smoke",
                    description="Verify HTTP confirmation pending/detail/response endpoints only allow the target current-user token.",
                    args=_http_confirmation_response_auth_smoke_args(
                        python,
                        f"{base_url}/auth/im/confirmations",
                        f"{base_url}/auth/im/token-bindings",
                        env_file,
                    ),
                    required=False,
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_confirmation_response_summary_guard_smoke",
                    description="Verify confirmation response summary requires admin auth and returns only redacted response data.",
                    args=_http_confirmation_response_summary_guard_smoke_args(
                        python,
                        f"{base_url}/auth/im/confirmations",
                        f"{base_url}/auth/im/token-bindings",
                        env_file,
                    ),
                    required=False,
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_diagnose_confirmation_policy_smoke",
                    description="Verify HTTP diagnosis confirmation policy rejects invalid tokens and queues confirmation for a bound current-user token.",
                    args=_http_diagnose_confirmation_policy_smoke_args(
                        python,
                        f"{base_url}/diagnose",
                        f"{base_url}/auth/im/token-bindings",
                        env_file,
                    ),
                    required=False,
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_webhook_confirmation_policy_smoke",
                    description="Verify webhook diagnosis can queue confirmation only for a bound current-user token without leaking webhook or user tokens.",
                    args=_http_webhook_confirmation_policy_smoke_args(
                        python,
                        f"{base_url}/diagnose/webhook/code_review",
                        f"{base_url}/auth/im/token-bindings",
                        env_file,
                    ),
                    required=False,
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_im_configure_page",
                    description="Check admin IM configuration page is reachable.",
                    args=_http_get_args(python, f"{base_url}/auth/im/configure/page"),
                    required=False,
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_im_configure_guard",
                    description="Check IM configuration API rejects writes without an admin token.",
                    args=_http_im_configure_guard_args(python, f"{base_url}/auth/im/configure"),
                    required=False,
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_im_configure_plan",
                    description="Check IM configuration API can plan with admin token without writing secrets.",
                    args=_http_im_configure_plan_args(
                        python,
                        f"{base_url}/auth/im/configure",
                        env_file,
                    ),
                    required=False,
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_external_inputs_page",
                    description="Check P5 external input browser checklist is reachable.",
                    args=_http_get_args(python, f"{base_url}/diagnose/external-inputs/page"),
                    required=False,
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_external_inputs_markdown",
                    description="Check P5 external input Markdown checklist is reachable.",
                    args=_http_get_args(python, f"{base_url}/diagnose/external-inputs.md"),
                    required=False,
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_final_verification_guide",
                    description="Check P5 final verification JSON guide is reachable.",
                    args=_http_get_args(python, f"{base_url}/diagnose/final-verification"),
                    required=False,
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_final_verification_page",
                    description="Check P5 final verification browser guide is reachable.",
                    args=_http_get_args(python, f"{base_url}/diagnose/final-verification/page"),
                    required=False,
                    env=command_env,
                ),
                _current_user_token_command(
                    command_id="http_current_user_smoke",
                    description="Verify current-user HTTP self-test route when a user token is provided.",
                    args=_http_current_user_smoke_args(python, f"{base_url}/auth/im/current-user/smoke"),
                    env=command_env,
                ),
                _current_user_token_command(
                    command_id="http_confirmation_request",
                    description="Verify explicit current-user HTTP confirmation request when a user token is provided.",
                    args=_http_confirmation_request_args(
                        python,
                        f"{base_url}/auth/im/confirmations/request",
                    ),
                    env=command_env,
                ),
                VerificationCommand(
                    id="http_acceptance",
                    description="Check deployed acceptance endpoint.",
                    args=_http_get_args(python, f"{base_url}/diagnose/acceptance"),
                    env=command_env,
                ),
            ]
        )
    if include_worker:
        worker_path = Path(confirmation_worker)
        if worker_path.exists():
            commands.append(
                VerificationCommand(
                    id="confirmation_worker_once",
                    description="Validate current-user confirmation worker routing once.",
                    args=(confirmation_worker, "once"),
                    required=False,
                    timeout_seconds=60,
                )
            )
        else:
            commands.append(
                VerificationCommand(
                    id="confirmation_worker_once",
                    description="Validate current-user confirmation worker routing once.",
                    args=(confirmation_worker, "once"),
                    required=False,
                    timeout_seconds=0,
                    skip_reason="confirmation worker command not found",
                )
            )
    if not include_slow:
        commands = [command for command in commands if not command.slow]
    return tuple(commands)


def render_p5_final_verification_text(report: dict[str, Any]) -> str:
    handoff = dict(report.get("external_input_handoff") or {})
    next_action = dict(handoff.get("next_action") or {})
    lines = [
        f"diagnose_p5_final_verify status={report['status']} ok={str(report['ok']).lower()} "
        f"accepted={str(report['accepted']).lower()} total={report['summary']['total']} "
        f"failed_required={report['summary']['required_failed']} "
        f"pending_required={report['summary'].get('required_pending', 0)}",
    ]
    if handoff:
        lines.append(
            f"HANDOFF status={handoff.get('status', 'unknown')} "
            f"pending_count={handoff.get('pending_count', 0)} "
            f"next_action={next_action.get('check_id', '')} "
            f"owner={next_action.get('owner', '')} "
            f"ordered_task_ids={','.join(str(item) for item in handoff.get('ordered_task_ids') or [])} "
            f"secret_values_written={str(bool(handoff.get('secret_values_written'))).lower()}"
        )
        lines.extend(f"HANDOFF_SAFE {command}" for command in next_action.get("safe_commands") or [])
        lines.extend(f"HANDOFF_VERIFY {command}" for command in next_action.get("verification_commands") or [])
    for result in report["results"]:
        lines.append(
            f"CHECK {result['id']} status={result['status']} exit_code={result['exit_code']} "
            f"required={str(result['required']).lower()}"
        )
    for step in report["next_steps"]:
        lines.append(f"NEXT {step}")
    return "\n".join(lines) + "\n"


def write_p5_final_verification_report(path: str | Path, report: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output.chmod(0o600)


def _run_command(command: VerificationCommand, *, runner: Runner) -> dict[str, Any]:
    if command.timeout_seconds == 0:
        return {
            "id": command.id,
            "description": command.description,
            "status": "skipped",
            "exit_code": None,
            "required": command.required,
            "command": _shell_join(command.args),
            "stdout_tail": "",
            "stderr_tail": command.skip_reason or "command skipped",
            "json": None,
        }
    env = dict(os.environ)
    if command.id not in _CURRENT_USER_TOKEN_COMMAND_IDS:
        env.pop("CODEKB_USER_AUTH_TOKEN", None)
    env.update(command.env or {})
    try:
        exit_code, stdout, stderr = runner(command.args, command.timeout_seconds, env)
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = str(exc.stdout or "")
        stderr = f"timeout after {command.timeout_seconds}s"
    stdout = redact_sensitive_text(str(stdout or ""))
    stderr = redact_sensitive_text(str(stderr or ""))
    parsed = _parse_json(stdout)
    status = _command_status(command.id, exit_code, parsed)
    return {
        "id": command.id,
        "description": command.description,
        "status": status,
        "exit_code": exit_code,
        "required": command.required,
        "command": _shell_join(command.args),
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
        "json": parsed,
    }


def _subprocess_runner(args: Sequence[str], timeout_seconds: int, env: dict[str, str]) -> tuple[int, str, str]:
    completed = subprocess.run(
        list(args),
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        env=env,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _http_get_args(python: str, url: str) -> tuple[str, ...]:
    script = (
        "import sys,urllib.request;"
        "url=sys.argv[1];"
        "req=urllib.request.Request(url,headers={'Accept':'application/json'});"
        "print(urllib.request.urlopen(req,timeout=10).read().decode('utf-8'))"
    )
    return (python, "-c", script, url)


def _handoff_bundle_output_dir(env_file: str) -> Path:
    env_path = Path(str(env_file or "/data/codekb/state/p5-secrets.env"))
    parent = env_path.parent if str(env_path.parent) else Path(".")
    return parent / "p5-handoff"


def _external_input_plan_alignment_args(python: str, env_file: str) -> tuple[str, ...]:
    script = (
        "import json,subprocess,sys\n"
        "from pathlib import Path\n"
        "env_file=sys.argv[1]\n"
        "def run(cmd):\n"
        "    completed=subprocess.run(cmd,text=True,capture_output=True,timeout=60,check=False)\n"
        "    try:\n"
        "        payload=json.loads(completed.stdout or '{}')\n"
        "    except Exception:\n"
        "        return completed.returncode, {}, completed.stdout or '', completed.stderr or '', False\n"
        "    return completed.returncode, payload, completed.stdout or '', completed.stderr or '', True\n"
        "plan_code,plan,plan_out,plan_err,plan_json=run([sys.executable,'-m','codekb','diagnose-external-inputs','--env-file',env_file,'--json'])\n"
        "state_code,state,state_out,state_err,state_json=run([sys.executable,'-m','codekb','diagnose-p5-external-state','--env-file',env_file,'--json'])\n"
        "state_pending=set(str(item) for item in (state.get('pending_checks') or []))\n"
        "task_ids=set(str(item.get('check_id','')) for item in (plan.get('tasks') or []) if isinstance(item,dict))\n"
        "mapped=set('im_oauth' if item=='im_env' else item for item in state_pending)\n"
        "missing=sorted(item for item in mapped if item and item not in task_ids)\n"
        "plan_state_pending=set(str(item) for item in ((plan.get('external_state') or {}).get('pending_checks') or []))\n"
        "state_count_ok=plan.get('external_state_pending_count')==len(state_pending)\n"
        "plan_state_ok=plan_state_pending==state_pending\n"
        "def parse_env(path):\n"
        "    values=[]\n"
        "    try:\n"
        "        lines=Path(path).read_text(encoding='utf-8',errors='replace').splitlines()\n"
        "    except FileNotFoundError:\n"
        "        return values\n"
        "    for raw in lines:\n"
        "        line=raw.strip()\n"
        "        if not line or line.startswith('#') or '=' not in line:\n"
        "            continue\n"
        "        if line.startswith('export '):\n"
        "            line=line[len('export '):].strip()\n"
        "        key,value=line.split('=',1)\n"
        "        if any(token in key for token in ('SECRET','TOKEN','PASSWORD')):\n"
        "            clean=value.strip().strip('\\\"').strip(\"'\")\n"
        "            if clean:\n"
        "                values.append(clean)\n"
        "    return values\n"
        "combined='\\n'.join([plan_out,plan_err,state_out,state_err,json.dumps(plan,ensure_ascii=False),json.dumps(state,ensure_ascii=False)])\n"
        "leaked=sorted(value for value in parse_env(env_file) if value and value in combined)\n"
        "plan_secret_safe=(plan.get('secret_values_written') is False or (plan.get('secret_handling') or {}).get('output_contains_secret_values') is False)\n"
        "ok=bool(plan_code==0 and state_code==0 and plan_json and state_json and not missing and state_count_ok and plan_state_ok and not leaked and plan_secret_safe and state.get('secret_values_written') is False)\n"
        "print(json.dumps({\n"
        " 'status':'validated' if ok else 'failed',\n"
        " 'plan_status':plan.get('status',''),\n"
        " 'state_status':state.get('status',''),\n"
        " 'task_ids':sorted(task_ids),\n"
        " 'state_pending':sorted(state_pending),\n"
        " 'mapped_pending':sorted(mapped),\n"
        " 'missing_task_ids':missing,\n"
        " 'external_state_pending_count':plan.get('external_state_pending_count'),\n"
        " 'state_count_ok':state_count_ok,\n"
        " 'plan_embeds_external_state':plan_state_ok,\n"
        " 'secret_value_leak':bool(leaked),\n"
        " 'plan_secret_safe':plan_secret_safe,\n"
        " 'secret_values_written':False,\n"
        "},ensure_ascii=False,sort_keys=True))\n"
        "sys.exit(0 if ok else 1)\n"
    )
    return (python, "-c", script, env_file)


def _handoff_bundle_smoke_args(
    python: str,
    output_dir: str,
    env_file: str,
    base_url: str,
) -> tuple[str, ...]:
    script = (
        "import json,subprocess,sys;"
        "from pathlib import Path;"
        "out=Path(sys.argv[1]);env_file=sys.argv[2];base=sys.argv[3];"
        "cmd=[sys.executable,'-m','codekb','diagnose-p5-handoff-bundle','--output-dir',str(out),'--env-file',env_file,'--api-base-url',base,'--json'];"
        "completed=subprocess.run(cmd,text=True,capture_output=True,timeout=60,check=False);"
        "stdout=completed.stdout or '';"
        "\ntry:\n"
        "    payload=json.loads(stdout)\n"
        "except Exception:\n"
        "    print(json.dumps({'status':'failed','reason':'invalid_json','exit_code':completed.returncode,'secret_values_written':False},sort_keys=True))\n"
        "    sys.exit(1)\n"
        "files=[out/'integrations'/'code_review_skill.md',out/'integrations'/'current_user_auth.md',out/'integrations'/'external_handoff.md',out/'integrations'/'im_entry.md',out/'external-inputs.md',out/'README.md']\n"
        "text='\\n'.join(f.read_text(encoding='utf-8',errors='replace') for f in files if f.exists())\n"
        "lower_text=text.lower()\n"
        "template=out/'im-config.todo.env'\n"
        "template_mode=(template.stat().st_mode & 0o777) if template.exists() else 0\n"
        "required_files=all(f.exists() for f in files) and template.exists() and (out/'integrations'/'summary.json').exists()\n"
        "markers={\n"
        " 'has_webhook_diagnose':'POST /diagnose/webhook/{source}' in text,\n"
        " 'has_webhook_shared_token':'X-CodeKB-Token' in text,\n"
        " 'has_confirmation_policy':'confirmation_policy=needs_review|always' in text,\n"
        " 'has_current_user_token':(\"current user's `auth_token`\" in text or '当前用户 `auth_token`' in text),\n"
        " 'has_current_user_confirmation_target':('routed only to the current user bound to `auth_token`' in text or 'Confirmation target is always the current authenticated user bound to `auth_token`' in text or '确认目标始终是 `auth_token` 绑定的当前用户' in text),\n"
        " 'has_no_interface_lookup':('do not infer an interface person' in lower_text or 'do not route p5 confirmations by owner/interface-person lookup' in lower_text or 'interface-person fields are not used for p5 routing' in lower_text or '不通过复杂接口人识别' in text),\n"
        " 'has_audit_exclusion':'Webhook audit events intentionally exclude `auth_token`' in text,\n"
        " 'has_interaction_complete_confirmation':('interaction complete' in lower_text or 'interaction_complete' in text),\n"
        " 'has_problem_solved_confirmation':('problem solved' in lower_text or 'problem_solved' in text),\n"
        " 'has_mcp_setup_url':'/auth/im/mcp/setup' in text,\n"
        " 'has_token_binding_fallback_url':'/auth/im/token-bindings/page' in text,\n"
        " 'has_external_state_final_gate':('GET /diagnose/external-state' in text and 'diagnose-p5-external-state' in text),\n"
        " 'has_final_verify_gate':('diagnose-p5-final-verify' in text and '--output /data/codekb/logs/p5-final-verify-report.json' in text),\n"
        "}\n"
        "def parse_env(path):\n"
        "    values={}\n"
        "    try:\n"
        "        lines=Path(path).read_text(encoding='utf-8',errors='replace').splitlines()\n"
        "    except FileNotFoundError:\n"
        "        return values\n"
        "    for raw in lines:\n"
        "        line=raw.strip()\n"
        "        if not line or line.startswith('#'):\n"
        "            continue\n"
        "        if line.startswith('export '):\n"
        "            line=line[len('export '):].strip()\n"
        "        if '=' not in line:\n"
        "            continue\n"
        "        key,value=line.split('=',1)\n"
        "        value=value.strip().strip('\\\"').strip(\"'\")\n"
        "        values[key.strip()]=value\n"
        "    return values\n"
        "secret_keys=['CODEKB_AUTH_ADMIN_TOKEN','CODEKB_DIAGNOSE_WEBHOOK_TOKEN','CODEKB_IM_APP_SECRET','CODEKB_IM_OAUTH_STATE_SECRET']\n"
        "env_values=parse_env(env_file)\n"
        "combined='\\n'.join(p.read_text(encoding='utf-8',errors='replace') for p in out.rglob('*') if p.is_file()) if out.exists() else ''\n"
        "leaked=[key for key in secret_keys if env_values.get(key) and env_values[key] in combined]\n"
        "ok=bool(completed.returncode==0 and payload.get('secret_values_written') is False and required_files and template_mode==0o600 and all(markers.values()) and not leaked)\n"
        "result={\n"
        " 'status':'validated' if ok else 'failed',\n"
        " 'bundle_status':payload.get('status',''),\n"
        " 'pending_count':payload.get('pending_count'),\n"
        " 'files_count':len(payload.get('files') or []),\n"
        " 'required_files_present':required_files,\n"
        " 'template_mode':oct(template_mode),\n"
        " 'template_preserved':(payload.get('im_template') or {}).get('mode')=='preserved',\n"
        " 'secret_values_written':False,\n"
        " 'secret_value_leak':bool(leaked),\n"
        " **markers,\n"
        "}\n"
        "print(json.dumps(result,ensure_ascii=False,sort_keys=True))\n"
        "sys.exit(0 if ok else 1)\n"
    )
    return (python, "-c", script, output_dir, env_file, base_url)


def _mcp_auth_error_fallback_args(python: str, base_url: str) -> tuple[str, ...]:
    script = (
        "import json,sys;"
        "from codekb.mcp_server import DiagnoseMcpRuntime,handle_mcp_request;"
        "base=sys.argv[1].rstrip('/');"
        "marker=''.join(['p5','_mcp','_marker']);"
        "response=handle_mcp_request({"
        "'jsonrpc':'2.0',"
        "'id':1,"
        "'method':'tools/call',"
        "'params':{'name':'codekb_diagnose_webhook_validate','arguments':{'source':'ci','payload':{'repo':'ym/app'}}}"
        "},DiagnoseMcpRuntime(api_base_url=base,mcp_token=marker));"
        "data=response.get('error',{}).get('data',{});"
        "expected_setup=base+'/auth/im/mcp/setup';"
        "expected_binding=base+'/auth/im/token-bindings/page';"
        "ok=(response.get('error',{}).get('code')==-32000 "
        "and data.get('reason')=='missing_auth_token' "
        "and data.get('setup_url')==expected_setup "
        "and data.get('token_binding_page_url')==expected_binding "
        "and data.get('auth_token_argument')=='auth_token' "
        "and bool(data.get('im_oauth_login_url')) "
        "and marker not in json.dumps(response,ensure_ascii=False));"
        "print(json.dumps({"
        "'status':'validated' if ok else 'failed',"
        "'reason':data.get('reason',''),"
        "'setup_url':data.get('setup_url',''),"
        "'token_binding_page_url':data.get('token_binding_page_url',''),"
        "'has_oauth_login':bool(data.get('im_oauth_login_url')),"
        "'secret_values_written':False"
        "},ensure_ascii=False,sort_keys=True));"
        "sys.exit(0 if ok else 1)"
    )
    return (python, "-c", script, base_url)


def _im_delivery_config_validation_args(python: str) -> tuple[str, ...]:
    script = (
        "import json,sys;"
        "from codekb.user_confirmation_delivery import validate_im_delivery_configuration;"
        "valid=validate_im_delivery_configuration(agent_id='100001',confirmation_url_base='https://kb.example/auth/im/confirmations/page',require_confirmation_url=True);"
        "missing_url=validate_im_delivery_configuration(agent_id='100001',confirmation_url_base='',require_confirmation_url=True);"
        "bad_agent=validate_im_delivery_configuration(agent_id='agent-1',confirmation_url_base='https://kb.example/auth/im/confirmations/page',require_confirmation_url=True);"
        "relative_url=validate_im_delivery_configuration(agent_id='100001',confirmation_url_base='/auth/im/confirmations/page',require_confirmation_url=True);"
        "fragment_url=validate_im_delivery_configuration(agent_id='100001',confirmation_url_base='https://kb.example/confirm#token',require_confirmation_url=True);"
        "ok=bool(valid.get('ok') and not missing_url.get('ok') and not bad_agent.get('ok') and not relative_url.get('ok') and not fragment_url.get('ok'));"
        "print(json.dumps({"
        "'status':'validated' if ok else 'failed',"
        "'valid_ok':bool(valid.get('ok')),"
        "'missing_url_blocked':not bool(missing_url.get('ok')),"
        "'bad_agent_blocked':not bool(bad_agent.get('ok')),"
        "'relative_url_blocked':not bool(relative_url.get('ok')),"
        "'fragment_url_blocked':not bool(fragment_url.get('ok')),"
        "'secret_values_written':False"
        "},ensure_ascii=False,sort_keys=True));"
        "sys.exit(0 if ok else 1)"
    )
    return (python, "-c", script)


def _mcp_static_token_default_reject_args(python: str, base_url: str) -> tuple[str, ...]:
    script = (
        "import json,sys;"
        "from codekb.mcp_server import DiagnoseMcpRuntime,handle_mcp_request;"
        "base=sys.argv[1].rstrip('/');"
        "static_token=''.join(['p5','_static','_default','_reject']);"
        "response=handle_mcp_request({"
        "'jsonrpc':'2.0',"
        "'id':1,"
        "'method':'tools/call',"
        "'params':{'name':'codekb_diagnose_webhook_validate','arguments':{"
        "'auth_token':static_token,"
        "'source':'ci',"
        "'payload':{'repo':'ym/app'}"
        "}}"
        "},DiagnoseMcpRuntime(api_base_url=base,mcp_token=static_token));"
        "data=response.get('error',{}).get('data',{});"
        "raw=json.dumps(response,ensure_ascii=False);"
        "ok=(response.get('error',{}).get('code')==-32000 "
        "and data.get('reason')=='current_user_token_store_required' "
        "and data.get('static_token_configured') is True "
        "and data.get('static_token_allowed') is False "
        "and data.get('token_store_configured') is False "
        "and data.get('setup_url')==base+'/auth/im/mcp/setup' "
        "and data.get('token_binding_page_url')==base+'/auth/im/token-bindings/page' "
        "and static_token not in raw);"
        "print(json.dumps({"
        "'status':'validated' if ok else 'failed',"
        "'reason':data.get('reason',''),"
        "'static_token_configured':bool(data.get('static_token_configured')),"
        "'static_token_allowed':bool(data.get('static_token_allowed')),"
        "'token_store_configured':bool(data.get('token_store_configured')),"
        "'setup_url':data.get('setup_url',''),"
        "'token_binding_page_url':data.get('token_binding_page_url',''),"
        "'response_masks_token':static_token not in raw,"
        "'secret_values_written':False"
        "},ensure_ascii=False,sort_keys=True));"
        "sys.exit(0 if ok else 1)"
    )
    return (python, "-c", script, base_url)


def _mcp_explicit_confirmation_reasons_args(python: str, base_url: str) -> tuple[str, ...]:
    script = (
        "import hashlib,json,sys,tempfile\n"
        "from pathlib import Path\n"
        "from codekb.mcp_server import DiagnoseMcpRuntime,handle_mcp_request\n"
        "from codekb.user_auth import JsonUserTokenStore\n"
        "base=sys.argv[1].rstrip('/')\n"
        "results=[]\n"
        "with tempfile.TemporaryDirectory() as tmp:\n"
        "    root=Path(tmp)\n"
        "    store_path=root/'tokens.json'\n"
        "    outbox=root/'confirmations.jsonl'\n"
        "    store=JsonUserTokenStore(store_path)\n"
        "    legacy=store.issue(user_id_hash='legacy_interface_hash',display_name='Legacy Interface',scopes=['diagnose'],metadata={'im_userid':'legacy-interface-user'})\n"
        "    issued=[\n"
        "        store.issue(user_id_hash='u_interaction',scopes=['diagnose'],metadata={'im_userid':'ww-interaction'}),\n"
        "        store.issue(user_id_hash='u_solved',scopes=['diagnose'],metadata={'im_userid':'ww-solved'}),\n"
        "    ]\n"
        "    runtime=DiagnoseMcpRuntime(token_store_path=str(store_path),confirmation_outbox_path=str(outbox),api_base_url=base)\n"
        "    reasons=['interaction_complete','problem_solved']\n"
        "    for idx,reason in enumerate(reasons):\n"
        "        token=issued[idx]['token']\n"
        "        response=handle_mcp_request({'jsonrpc':'2.0','id':idx+1,'method':'tools/call','params':{'name':'codekb_request_user_confirmation','arguments':{'auth_token':token,'reason':reason,'message':'please confirm','payload':{'source':'p5_final_verify','owner':'legacy-owner','interface_person':'legacy-interface-user'}}}},runtime)\n"
        "        is_error=bool(response.get('result',{}).get('isError') or response.get('error'))\n"
        "        text=((response.get('result') or {}).get('content') or [{}])[0].get('text','{}') if not response.get('error') else '{}'\n"
        "        payload=json.loads(text)\n"
        "        expected=hashlib.sha256(token.encode('utf-8')).hexdigest()[:12]\n"
        "        legacy_prefix=hashlib.sha256(legacy['token'].encode('utf-8')).hexdigest()[:12]\n"
        "        results.append({'reason':reason,'is_error':is_error,'confirmation_id':payload.get('confirmation_id',''),'target_is_current_user_token':payload.get('target_user_token_hash_prefix')==expected,'target_is_legacy_interface':payload.get('target_user_token_hash_prefix')==legacy_prefix,'full_hash_exposed':'target_user_token_hash' in payload,'payload_keeps_context':payload.get('payload',{}).get('interface_person')=='legacy-interface-user'})\n"
        "    raw=outbox.read_text(encoding='utf-8') if outbox.exists() else ''\n"
        "    records=[json.loads(line) for line in raw.splitlines() if line.strip()]\n"
        "    token_leak=any(item['token'] in raw for item in issued+[legacy])\n"
        "ok=(len(records)==2 and [record.get('reason') for record in records]==reasons and all(item['confirmation_id'] and item['target_is_current_user_token'] and not item['target_is_legacy_interface'] and not item['is_error'] and not item['full_hash_exposed'] and item['payload_keeps_context'] for item in results) and not token_leak)\n"
        "print(json.dumps({'status':'validated' if ok else 'failed','reasons':reasons,'results':results,'records_count':len(records),'outbox_masks_tokens':not token_leak,'secret_values_written':False},ensure_ascii=False,sort_keys=True))\n"
        "sys.exit(0 if ok else 1)\n"
    )
    return (python, "-c", script, base_url)


def _mcp_token_store_static_reject_args(python: str, base_url: str) -> tuple[str, ...]:
    script = (
        "import json,sys,tempfile;"
        "from codekb.mcp_server import DiagnoseMcpRuntime,handle_mcp_request;"
        "from codekb.user_auth import JsonUserTokenStore;"
        "base=sys.argv[1].rstrip('/');"
        "static_token=''.join(chr(i) for i in [112,53,45,115,116,97,116,105,99,45,116,111,107,101,110]);"
        "token_store=tempfile.mkdtemp(prefix='p5-mcp-auth-')+'/tokens.json';"
        "issued=JsonUserTokenStore(token_store).issue("
        "user_id_hash='p5_current_user_hash',"
        "scopes=['diagnose'],"
        "metadata={'im_userid':'p5-current-user'}"
        ");"
        "runtime=DiagnoseMcpRuntime("
        "api_base_url=base,"
        "mapping_path='docs/diagnose-webhook-mapping.draft.yaml',"
        "mcp_token=static_token,"
        "token_store_path=token_store"
        ");"
        "payload={"
        "'repository':{'path':'ym/app'},"
        "'error':{'code':'DEVICE_SEQ','message':'DEVICE_SEQ 构建失败'},"
        "'sub_kbs':['testing']"
        "};"
        "static_request={"
        "'jsonrpc':'2.0',"
        "'id':1,"
        "'method':'tools/call',"
        "'params':{'name':'codekb_diagnose_webhook_validate','arguments':{"
        "'auth_token':static_token,"
        "'source':'code_review',"
        "'payload':payload"
        "}}"
        "};"
        "bound_request={"
        "'jsonrpc':'2.0',"
        "'id':2,"
        "'method':'tools/call',"
        "'params':{'name':'codekb_diagnose_webhook_validate','arguments':{"
        "'auth_token':issued['token'],"
        "'source':'code_review',"
        "'payload':payload"
        "}}"
        "};"
        "static_response=handle_mcp_request(static_request,runtime);"
        "bound_response=handle_mcp_request(bound_request,runtime);"
        "bound_token=issued['token'];"
        "raw=json.dumps([static_response,bound_response],ensure_ascii=False);"
        "static_error=static_response.get('error',{});"
        "bound_result=bound_response.get('result',{});"
        "static_rejected=(static_error.get('code')==-32000 and 'invalid MCP auth token' in static_error.get('message',''));"
        "bound_accepted=bool(bound_result and bound_result.get('isError') is False);"
        "no_token_leak=(static_token not in raw and bound_token not in raw);"
        "ok=static_rejected and bound_accepted and no_token_leak;"
        "print(json.dumps({"
        "    'status':'validated' if ok else 'failed',"
        "    'static_token_rejected':static_rejected,"
        "    'bound_token_accepted':bound_accepted,"
        "    'token_store_configured':True,"
        "    'static_token_configured':True,"
        "    'response_masks_tokens':no_token_leak,"
        "    'secret_values_written':False"
        "},ensure_ascii=False,sort_keys=True));"
        "sys.exit(0 if ok else 1)"
    )
    return (python, "-c", script, base_url)


def _http_current_user_smoke_args(python: str, url: str) -> tuple[str, ...]:
    script = (
        "import json,os,sys,urllib.request;"
        "url=sys.argv[1];"
        "token=os.environ.get('CODEKB_USER_AUTH_TOKEN','').strip();"
        "payload=json.dumps({'auth_token':token,'respond':True}).encode('utf-8');"
        "req=urllib.request.Request(url,data=payload,method='POST',headers={'Accept':'application/json','Content-Type':'application/json'});"
        "print(urllib.request.urlopen(req,timeout=20).read().decode('utf-8'))"
    )
    return (python, "-c", script, url)


def _http_confirmation_request_args(python: str, url: str) -> tuple[str, ...]:
    script = (
        "import json,os,sys,urllib.request;"
        "url=sys.argv[1];"
        "token=os.environ.get('CODEKB_USER_AUTH_TOKEN','').strip();"
        "payload=json.dumps({"
        "'auth_token':token,"
        "'reason':'problem_solved',"
        "'message':'请确认本次问题是否已解决',"
        "'payload':{'source':'p5_final_verify'}"
        "}).encode('utf-8');"
        "req=urllib.request.Request(url,data=payload,method='POST',headers={'Accept':'application/json','Content-Type':'application/json'});"
        "print(urllib.request.urlopen(req,timeout=20).read().decode('utf-8'))"
    )
    return (python, "-c", script, url)


def _http_im_configure_guard_args(python: str, url: str) -> tuple[str, ...]:
    script = (
        "import json,sys,urllib.error,urllib.request;"
        "url=sys.argv[1];"
        "markers=['p5_guard_corp','p5_guard_app','p5_guard_state'];"
        "payload=json.dumps({"
        "'corp_id':markers[0],"
        "'agent_id':'100001',"
        "'app_secret':markers[1],"
        "'oauth_state_secret':markers[2],"
        "'apply':True"
        "}).encode('utf-8');"
        "req=urllib.request.Request(url,data=payload,method='POST',headers={'Accept':'application/json','Content-Type':'application/json'});"
        "\ntry:\n"
        "    body=urllib.request.urlopen(req,timeout=10).read().decode('utf-8','replace')\n"
        "    print(json.dumps({'status':'failed','http_status':200,'reason':'request accepted without admin token','secret_values_written':False}))\n"
        "    sys.exit(1)\n"
        "except urllib.error.HTTPError as exc:\n"
        "    body=exc.read().decode('utf-8','replace')\n"
        "    leaked=any(marker in body for marker in markers)\n"
        "    ok=exc.code==401 and not leaked\n"
        "    print(json.dumps({'status':'validated' if ok else 'failed','http_status':exc.code,'secret_leak':leaked,'secret_values_written':False}))\n"
        "    sys.exit(0 if ok else 1)\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'status':'failed','error':exc.__class__.__name__,'secret_values_written':False}))\n"
        "    sys.exit(1)\n"
    )
    return (python, "-c", script, url)


def _http_token_binding_fallback_smoke_args(python: str, url: str, env_file: str) -> tuple[str, ...]:
    script = (
        "import json,sys,urllib.error,urllib.request;"
        "from pathlib import Path;"
        "url=sys.argv[1].rstrip('/');env_file=sys.argv[2];"
        "\n"
        "def parse_value(value):\n"
        "    value=value.strip()\n"
        "    if len(value)>=2 and value[0]==value[-1] and value[0] in ('\\\"', \"'\"):\n"
        "        return value[1:-1]\n"
        "    return value\n"
        "def read_env(path):\n"
        "    values={}\n"
        "    try:\n"
        "        lines=open(path,encoding='utf-8').read().splitlines()\n"
        "    except FileNotFoundError:\n"
        "        return values\n"
        "    for raw in lines:\n"
        "        line=raw.strip()\n"
        "        if not line or line.startswith('#'):\n"
        "            continue\n"
        "        if line.startswith('export '):\n"
        "            line=line[len('export '):].strip()\n"
        "        if '=' not in line:\n"
        "            continue\n"
        "        key,value=line.split('=',1)\n"
        "        values[key.strip()]=parse_value(value)\n"
        "    return values\n"
        "values=read_env(env_file)\n"
        "admin=values.get('CODEKB_AUTH_ADMIN_TOKEN','').strip()\n"
        "store_path=Path(values.get('CODEKB_USER_TOKEN_STORE','/data/codekb/state/user-tokens.json'))\n"
        "if not admin:\n"
        "    print(json.dumps({'status':'pending_external_inputs','reason':'CODEKB_AUTH_ADMIN_TOKEN missing from env file','secret_values_written':False},sort_keys=True))\n"
        "    sys.exit(0)\n"
        "had_store=store_path.exists()\n"
        "backup=store_path.read_bytes() if had_store else b''\n"
        "route='p5-token-binding-fallback-smoke-user'\n"
        "try:\n"
        "    payload=json.dumps({\n"
        "        'display_name':'P5 token binding fallback smoke',\n"
        "        'scopes':['diagnose'],\n"
        "        'ttl_days':1,\n"
        "        'metadata':{'im_userid':route,'source':'p5_final_verify'},\n"
        "    }).encode('utf-8')\n"
        "    def post_status(target, body, headers=None):\n"
        "        req=urllib.request.Request(target,data=body,method='POST',headers={'Accept':'application/json','Content-Type':'application/json',**dict(headers or {})})\n"
        "        try:\n"
        "            response=urllib.request.urlopen(req,timeout=10)\n"
        "            return response.getcode(), response.read().decode('utf-8','replace')\n"
        "        except urllib.error.HTTPError as exc:\n"
        "            return exc.code, exc.read().decode('utf-8','replace')\n"
        "    def get_status(target, headers=None):\n"
        "        req=urllib.request.Request(target,headers={'Accept':'application/json',**dict(headers or {})})\n"
        "        try:\n"
        "            response=urllib.request.urlopen(req,timeout=10)\n"
        "            return response.getcode(), response.read().decode('utf-8','replace')\n"
        "        except urllib.error.HTTPError as exc:\n"
        "            return exc.code, exc.read().decode('utf-8','replace')\n"
        "    denied_issue_status,denied_issue_body=post_status(url,payload)\n"
        "    req=urllib.request.Request(url,data=payload,method='POST',headers={'Accept':'application/json','Content-Type':'application/json','X-CodeKB-Admin-Token':admin})\n"
        "    body=urllib.request.urlopen(req,timeout=10).read().decode('utf-8','replace')\n"
        "    data=json.loads(body)\n"
        "    token=str(data.get('token',''))\n"
        "    binding=data.get('binding') or {}\n"
        "    token_id=str(binding.get('token_id',''))\n"
        "    metadata=binding.get('metadata') or {}\n"
        "    summary_denied_status,summary_denied_body=get_status(url+'/summary')\n"
        "    summary_status,summary_body=get_status(url+'/summary',headers={'X-CodeKB-Admin-Token':admin})\n"
        "    revoke_denied_status,revoke_denied_body=post_status(url+'/'+token_id+'/revoke',b'{}')\n"
        "    revoke_status,revoke_body=post_status(url+'/'+token_id+'/revoke',b'{}',headers={'X-CodeKB-Admin-Token':admin})\n"
        "    try:\n"
        "        summary=json.loads(summary_body)\n"
        "    except Exception:\n"
        "        summary={}\n"
        "    response_text=json.dumps(data,ensure_ascii=False)\n"
        "    management_text='\\n'.join([denied_issue_body,summary_denied_body,summary_body,revoke_denied_body,revoke_body])\n"
        "    store_text=store_path.read_text(encoding='utf-8') if store_path.exists() else ''\n"
        "    response_masks=bool(route not in response_text and admin not in response_text and token not in management_text and admin not in management_text and route not in management_text)\n"
        "    ok=bool(denied_issue_status==401 and summary_denied_status==401 and revoke_denied_status==401 and summary_status==200 and revoke_status==200 and token.startswith('lkb_') and token_id and len(str(binding.get('user_id_hash','')))==64 and 'im_userid_hash' in metadata and int(summary.get('total',0) or 0)>=1 and response_masks and token not in store_text and 'token_hash' in store_text)\n"
        "    print(json.dumps({\n"
        "        'status':'validated' if ok else 'failed',\n"
        "        'issue_without_admin_http_401':denied_issue_status==401,\n"
        "        'summary_without_admin_http_401':summary_denied_status==401,\n"
        "        'revoke_without_admin_http_401':revoke_denied_status==401,\n"
        "        'summary_with_admin_http_200':summary_status==200,\n"
        "        'revoke_with_admin_http_200':revoke_status==200,\n"
        "        'token_returned_once':token.startswith('lkb_'),\n"
        "        'token_id_present':bool(token_id),\n"
        "        'derived_user_hash_len':len(str(binding.get('user_id_hash',''))),\n"
        "        'summary_total_at_least_one':int(summary.get('total',0) or 0)>=1,\n"
        "        'response_masks_im_userid':route not in response_text and route not in management_text,\n"
        "        'response_masks_admin_token':admin not in response_text and admin not in management_text,\n"
        "        'store_keeps_token_hash_only':token not in store_text,\n"
        "        'metadata_has_route_hash':'im_userid_hash' in metadata,\n"
        "        'secret_values_written':False,\n"
        "    },ensure_ascii=False,sort_keys=True))\n"
        "    sys.exit(0 if ok else 1)\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'status':'failed','error':exc.__class__.__name__,'secret_values_written':False},sort_keys=True))\n"
        "    sys.exit(1)\n"
        "finally:\n"
        "    if had_store:\n"
        "        store_path.write_bytes(backup)\n"
        "    else:\n"
        "        try:\n"
        "            store_path.unlink()\n"
        "        except FileNotFoundError:\n"
        "            pass\n"
    )
    return (python, "-c", script, url, env_file)


def _http_token_revoke_auth_smoke_args(
    python: str,
    token_binding_url: str,
    current_user_status_url: str,
    confirmations_url: str,
    env_file: str,
) -> tuple[str, ...]:
    script = (
        "import json,sys,urllib.error,urllib.request\n"
        "from pathlib import Path\n"
        "marker='p5_final_verify_http_token_revoke'\n"
        "token_url=sys.argv[1].rstrip('/')\n"
        "status_url=sys.argv[2]\n"
        "confirmations_url=sys.argv[3].rstrip('/')\n"
        "env_file=sys.argv[4]\n"
        "def parse_value(value):\n"
        "    value=value.strip()\n"
        "    if len(value)>=2 and value[0]==value[-1] and value[0] in ('\\\"', \"'\"):\n"
        "        return value[1:-1]\n"
        "    return value\n"
        "def read_env(path):\n"
        "    values={}\n"
        "    try:\n"
        "        lines=open(path,encoding='utf-8').read().splitlines()\n"
        "    except FileNotFoundError:\n"
        "        return values\n"
        "    for raw in lines:\n"
        "        line=raw.strip()\n"
        "        if not line or line.startswith('#'):\n"
        "            continue\n"
        "        if line.startswith('export '):\n"
        "            line=line[len('export '):].strip()\n"
        "        if '=' not in line:\n"
        "            continue\n"
        "        key,value=line.split('=',1)\n"
        "        values[key.strip()]=parse_value(value)\n"
        "    return values\n"
        "def post_json(url,payload,headers=None):\n"
        "    data=json.dumps(payload,ensure_ascii=False).encode('utf-8')\n"
        "    req=urllib.request.Request(url,data=data,method='POST',headers={'Accept':'application/json','Content-Type':'application/json',**dict(headers or {})})\n"
        "    return urllib.request.urlopen(req,timeout=10).read().decode('utf-8','replace'), 200\n"
        "def post_status(url,payload,headers=None):\n"
        "    try:\n"
        "        body,code=post_json(url,payload,headers=headers)\n"
        "        return code,body\n"
        "    except urllib.error.HTTPError as exc:\n"
        "        return exc.code,exc.read().decode('utf-8','replace')\n"
        "values=read_env(env_file)\n"
        "admin=values.get('CODEKB_AUTH_ADMIN_TOKEN','').strip()\n"
        "store_path=Path(values.get('CODEKB_USER_TOKEN_STORE','/data/codekb/state/user-tokens.json'))\n"
        "outbox_path=Path(values.get('CODEKB_USER_CONFIRMATION_OUTBOX','/data/codekb/outbox/user-confirmation.jsonl'))\n"
        "if not admin:\n"
        "    print(json.dumps({'status':'pending_external_inputs','reason':'CODEKB_AUTH_ADMIN_TOKEN missing from env file','secret_values_written':False},sort_keys=True))\n"
        "    sys.exit(0)\n"
        "backups=[]\n"
        "for path in (store_path,outbox_path):\n"
        "    backups.append((path,path.exists(),path.read_bytes() if path.exists() else b''))\n"
        "route='p5-token-revoke-smoke-user'\n"
        "try:\n"
        "    issue_payload={'display_name':'P5 token revoke smoke','scopes':['diagnose'],'ttl_days':1,'metadata':{'im_userid':route,'source':marker}}\n"
        "    body,_=post_json(token_url,issue_payload,headers={'X-CodeKB-Admin-Token':admin})\n"
        "    issued=json.loads(body)\n"
        "    token=str(issued.get('token',''))\n"
        "    binding=issued.get('binding') or {}\n"
        "    token_id=str(binding.get('token_id',''))\n"
        "    status_before_code,status_before_body=post_status(status_url,{'auth_token':token})\n"
        "    request_code,request_body=post_status(confirmations_url+'/request',{'auth_token':token,'reason':'problem_solved','message':'P5 revoke smoke','payload':{'source':marker}})\n"
        "    confirmation_id=''\n"
        "    try:\n"
        "        confirmation_id=str((json.loads(request_body).get('confirmation') or {}).get('confirmation_id',''))\n"
        "    except Exception:\n"
        "        confirmation_id=''\n"
        "    revoke_code,revoke_body=post_status(token_url+'/'+token_id+'/revoke',{},headers={'X-CodeKB-Admin-Token':admin})\n"
        "    status_after_code,status_after_body=post_status(status_url,{'auth_token':token})\n"
        "    pending_after_code,pending_after_body=post_status(confirmations_url+'/pending',{'auth_token':token})\n"
        "    request_after_code,request_after_body=post_status(confirmations_url+'/request',{'auth_token':token,'reason':'problem_solved','message':'after revoke','payload':{'source':marker}})\n"
        "    detail_after_code=0\n"
        "    response_after_code=0\n"
        "    detail_after_body=''\n"
        "    response_after_body=''\n"
        "    if confirmation_id:\n"
        "        detail_after_code,detail_after_body=post_status(confirmations_url+'/'+confirmation_id+'/detail',{'auth_token':token})\n"
        "        response_after_code,response_after_body=post_status(confirmations_url+'/'+confirmation_id+'/response',{'auth_token':token,'decision':'confirmed','comment':'after revoke'})\n"
        "    combined='\\n'.join([body,status_before_body,request_body,revoke_body,status_after_body,pending_after_body,request_after_body,detail_after_body,response_after_body])\n"
        "    post_issue_combined='\\n'.join([status_before_body,request_body,revoke_body,status_after_body,pending_after_body,request_after_body,detail_after_body,response_after_body])\n"
        "    store_text=store_path.read_text(encoding='utf-8') if store_path.exists() else ''\n"
        "    outbox_text=outbox_path.read_text(encoding='utf-8') if outbox_path.exists() else ''\n"
        "    raw_leak=(admin in combined or route in combined or token in post_issue_combined or token in store_text or route in outbox_text)\n"
        "    ok=bool(token.startswith('lkb_') and token_id and status_before_code==200 and request_code==200 and confirmation_id and revoke_code==200 and status_after_code==401 and pending_after_code==401 and request_after_code==401 and detail_after_code==401 and response_after_code==401 and not raw_leak)\n"
        "    print(json.dumps({\n"
        "        'status':'validated' if ok else 'failed',\n"
        "        'token_issued':token.startswith('lkb_'),\n"
        "        'token_id_present':bool(token_id),\n"
        "        'status_before_http_200':status_before_code==200,\n"
        "        'confirmation_before_revoke_queued':request_code==200 and bool(confirmation_id),\n"
        "        'revoke_http_200':revoke_code==200,\n"
        "        'status_after_http_401':status_after_code==401,\n"
        "        'pending_after_http_401':pending_after_code==401,\n"
        "        'request_after_http_401':request_after_code==401,\n"
        "        'detail_after_http_401':detail_after_code==401,\n"
        "        'response_after_http_401':response_after_code==401,\n"
        "        'response_masks_secrets':not raw_leak,\n"
        "        'secret_values_written':False,\n"
        "    },ensure_ascii=False,sort_keys=True))\n"
        "    sys.exit(0 if ok else 1)\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'status':'failed','error':exc.__class__.__name__,'secret_values_written':False},sort_keys=True))\n"
        "    sys.exit(1)\n"
        "finally:\n"
        "    for path,existed,data in backups:\n"
        "        if existed:\n"
        "            path.parent.mkdir(parents=True,exist_ok=True)\n"
        "            path.write_bytes(data)\n"
        "        else:\n"
        "            try:\n"
        "                path.unlink()\n"
        "            except FileNotFoundError:\n"
        "                pass\n"
    )
    return (python, "-c", script, token_binding_url, current_user_status_url, confirmations_url, env_file)


def _http_explicit_confirmation_reasons_smoke_args(
    python: str,
    confirmation_url: str,
    token_binding_url: str,
    env_file: str,
) -> tuple[str, ...]:
    script = (
        "import hashlib,json,sys,urllib.request\n"
        "from pathlib import Path\n"
        "confirmation_url=sys.argv[1]\n"
        "token_url=sys.argv[2]\n"
        "env_file=sys.argv[3]\n"
        "def parse_value(value):\n"
        "    value=value.strip()\n"
        "    if len(value)>=2 and value[0]==value[-1] and value[0] in ('\\\"', \"'\"):\n"
        "        return value[1:-1]\n"
        "    return value\n"
        "def read_env(path):\n"
        "    values={}\n"
        "    try:\n"
        "        lines=open(path,encoding='utf-8').read().splitlines()\n"
        "    except FileNotFoundError:\n"
        "        return values\n"
        "    for raw in lines:\n"
        "        line=raw.strip()\n"
        "        if not line or line.startswith('#'):\n"
        "            continue\n"
        "        if line.startswith('export '):\n"
        "            line=line[len('export '):].strip()\n"
        "        if '=' not in line:\n"
        "            continue\n"
        "        key,value=line.split('=',1)\n"
        "        values[key.strip()]=parse_value(value)\n"
        "    return values\n"
        "def post_json(url,payload,headers=None):\n"
        "    req=urllib.request.Request(url,data=json.dumps(payload).encode('utf-8'),method='POST',headers={'Accept':'application/json','Content-Type':'application/json',**(headers or {})})\n"
        "    return urllib.request.urlopen(req,timeout=20).read().decode('utf-8','replace')\n"
        "values=read_env(env_file)\n"
        "admin=values.get('CODEKB_AUTH_ADMIN_TOKEN','').strip()\n"
        "store_path=Path(values.get('CODEKB_USER_TOKEN_STORE','/data/codekb/state/user-tokens.json'))\n"
        "outbox_path=Path(values.get('CODEKB_USER_CONFIRMATION_OUTBOX','/data/codekb/outbox/user-confirmation.jsonl'))\n"
        "if not admin:\n"
        "    print(json.dumps({'status':'pending_external_inputs','reason':'CODEKB_AUTH_ADMIN_TOKEN missing from env file','secret_values_written':False},sort_keys=True))\n"
        "    sys.exit(0)\n"
        "had_store=store_path.exists(); store_backup=store_path.read_bytes() if had_store else b''\n"
        "had_outbox=outbox_path.exists(); outbox_backup=outbox_path.read_bytes() if had_outbox else b''\n"
        "try:\n"
        "    def issue(name, route):\n"
        "        body=post_json(token_url,{'display_name':name,'scopes':['diagnose'],'ttl_days':1,'metadata':{'im_userid':route,'source':'p5_final_verify'}},{'X-CodeKB-Admin-Token':admin})\n"
        "        return json.loads(body)\n"
        "    legacy=issue('P5 HTTP explicit confirmation legacy','p5-http-explicit-legacy')\n"
        "    issued=[issue('P5 HTTP interaction complete','p5-http-explicit-interaction'),issue('P5 HTTP problem solved','p5-http-explicit-solved')]\n"
        "    reasons=['interaction_complete','problem_solved']\n"
        "    results=[]; bodies=[]\n"
        "    for idx,reason in enumerate(reasons):\n"
        "        token=str(issued[idx].get('token',''))\n"
        "        body=post_json(confirmation_url,{'auth_token':token,'reason':reason,'message':'please confirm','payload':{'source':'p5_final_verify_http_explicit_confirmation','owner':'legacy-owner','interface_person':'legacy-interface-user'}})\n"
        "        bodies.append(body)\n"
        "        data=json.loads(body)\n"
        "        confirmation=data.get('confirmation') or {}\n"
        "        expected=hashlib.sha256(token.encode('utf-8')).hexdigest()[:12]\n"
        "        legacy_prefix=hashlib.sha256(str(legacy.get('token','')).encode('utf-8')).hexdigest()[:12]\n"
        "        results.append({'reason':reason,'status':data.get('status',''),'confirmation_id':confirmation.get('confirmation_id',''),'reason_ok':confirmation.get('reason')==reason,'target_is_current_user_token':confirmation.get('target_user_token_hash_prefix')==expected,'target_is_legacy_interface':confirmation.get('target_user_token_hash_prefix')==legacy_prefix,'full_hash_exposed':'target_user_token_hash' in confirmation,'payload_keeps_context':confirmation.get('payload',{}).get('interface_person')=='legacy-interface-user'})\n"
        "    outbox_text=outbox_path.read_text(encoding='utf-8') if outbox_path.exists() else ''\n"
        "    all_records=[json.loads(line) for line in outbox_text.splitlines() if line.strip()]\n"
        "    confirmation_ids={item.get('confirmation_id','') for item in results if item.get('confirmation_id')}\n"
        "    records=[record for record in all_records if record.get('confirmation_id') in confirmation_ids]\n"
        "    tokens=[str(item.get('token','')) for item in issued+[legacy] if item.get('token')]\n"
        "    token_leak=any(token and token in outbox_text for token in tokens)\n"
        "    response_token_leak=any(token and token in '\\n'.join(bodies) for token in tokens+[admin])\n"
        "    ok=(len(records)==2 and [record.get('reason') for record in records]==reasons and all(item['status']=='queued' and item['confirmation_id'] and item['reason_ok'] and item['target_is_current_user_token'] and not item['target_is_legacy_interface'] and not item['full_hash_exposed'] and item['payload_keeps_context'] for item in results) and not token_leak and not response_token_leak)\n"
        "    print(json.dumps({'status':'validated' if ok else 'failed','reasons':reasons,'results':results,'records_count':len(records),'outbox_masks_tokens':not token_leak,'response_masks_tokens':not response_token_leak,'secret_values_written':False},ensure_ascii=False,sort_keys=True))\n"
        "    sys.exit(0 if ok else 1)\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'status':'failed','error':exc.__class__.__name__,'secret_values_written':False},sort_keys=True))\n"
        "    sys.exit(1)\n"
        "finally:\n"
        "    if had_store:\n"
        "        store_path.write_bytes(store_backup)\n"
        "    else:\n"
        "        try:\n"
        "            store_path.unlink()\n"
        "        except FileNotFoundError:\n"
        "            pass\n"
        "    if had_outbox:\n"
        "        outbox_path.write_bytes(outbox_backup)\n"
        "    else:\n"
        "        try:\n"
        "            outbox_path.unlink()\n"
        "        except FileNotFoundError:\n"
        "            pass\n"
    )
    return (python, "-c", script, confirmation_url, token_binding_url, env_file)


def _http_confirmation_response_auth_smoke_args(
    python: str,
    confirmations_base_url: str,
    token_binding_url: str,
    env_file: str,
) -> tuple[str, ...]:
    script = (
        "import hashlib,json,sys,urllib.error,urllib.request\n"
        "from pathlib import Path\n"
        "confirmations_base=sys.argv[1].rstrip('/')\n"
        "token_url=sys.argv[2]\n"
        "env_file=sys.argv[3]\n"
        "def parse_value(value):\n"
        "    value=value.strip()\n"
        "    if len(value)>=2 and value[0]==value[-1] and value[0] in ('\\\"', \"'\"):\n"
        "        return value[1:-1]\n"
        "    return value\n"
        "def read_env(path):\n"
        "    values={}\n"
        "    try:\n"
        "        lines=open(path,encoding='utf-8').read().splitlines()\n"
        "    except FileNotFoundError:\n"
        "        return values\n"
        "    for raw in lines:\n"
        "        line=raw.strip()\n"
        "        if not line or line.startswith('#'):\n"
        "            continue\n"
        "        if line.startswith('export '):\n"
        "            line=line[len('export '):].strip()\n"
        "        if '=' not in line:\n"
        "            continue\n"
        "        key,value=line.split('=',1)\n"
        "        values[key.strip()]=parse_value(value)\n"
        "    return values\n"
        "def post_json(url,payload,headers=None,expect_error=False):\n"
        "    req=urllib.request.Request(url,data=json.dumps(payload).encode('utf-8'),method='POST',headers={'Accept':'application/json','Content-Type':'application/json',**(headers or {})})\n"
        "    try:\n"
        "        body=urllib.request.urlopen(req,timeout=20).read().decode('utf-8','replace')\n"
        "        return 200, body\n"
        "    except urllib.error.HTTPError as exc:\n"
        "        body=exc.read().decode('utf-8','replace')\n"
        "        if expect_error:\n"
        "            return exc.code, body\n"
        "        raise\n"
        "values=read_env(env_file)\n"
        "admin=values.get('CODEKB_AUTH_ADMIN_TOKEN','').strip()\n"
        "store_path=Path(values.get('CODEKB_USER_TOKEN_STORE','/data/codekb/state/user-tokens.json'))\n"
        "outbox_path=Path(values.get('CODEKB_USER_CONFIRMATION_OUTBOX','/data/codekb/outbox/user-confirmation.jsonl'))\n"
        "responses_path=Path(values.get('CODEKB_USER_CONFIRMATION_RESPONSES','/data/codekb/state/user-confirmation-responses.jsonl'))\n"
        "if not admin:\n"
        "    print(json.dumps({'status':'pending_external_inputs','reason':'CODEKB_AUTH_ADMIN_TOKEN missing from env file','secret_values_written':False},sort_keys=True))\n"
        "    sys.exit(0)\n"
        "had_store=store_path.exists(); store_backup=store_path.read_bytes() if had_store else b''\n"
        "had_outbox=outbox_path.exists(); outbox_backup=outbox_path.read_bytes() if had_outbox else b''\n"
        "had_responses=responses_path.exists(); responses_backup=responses_path.read_bytes() if had_responses else b''\n"
        "try:\n"
        "    def issue(name, route):\n"
        "        status, body=post_json(token_url,{'display_name':name,'scopes':['diagnose'],'ttl_days':1,'metadata':{'im_userid':route,'source':'p5_final_verify'}},{'X-CodeKB-Admin-Token':admin})\n"
        "        return json.loads(body)\n"
        "    target=issue('P5 HTTP confirmation response target','p5-http-response-target')\n"
        "    other=issue('P5 HTTP confirmation response other','p5-http-response-other')\n"
        "    target_token=str(target.get('token',''))\n"
        "    other_token=str(other.get('token',''))\n"
        "    target_prefix=hashlib.sha256(target_token.encode('utf-8')).hexdigest()[:12]\n"
        "    request_status, request_body=post_json(confirmations_base+'/request',{'auth_token':target_token,'reason':'problem_solved','message':'please confirm','payload':{'source':'p5_final_verify_http_confirmation_response','owner':'legacy-owner','interface_person':'legacy-interface-user'}})\n"
        "    request_data=json.loads(request_body)\n"
        "    confirmation=request_data.get('confirmation') or {}\n"
        "    confirmation_id=str(confirmation.get('confirmation_id',''))\n"
        "    pending_status, pending_body=post_json(confirmations_base+'/pending',{'auth_token':target_token})\n"
        "    pending_data=json.loads(pending_body)\n"
        "    other_pending_status, other_pending_body=post_json(confirmations_base+'/pending',{'auth_token':other_token})\n"
        "    other_pending_data=json.loads(other_pending_body)\n"
        "    other_detail_status, other_detail_body=post_json(confirmations_base+'/'+confirmation_id+'/detail',{'auth_token':other_token},expect_error=True)\n"
        "    detail_status, detail_body=post_json(confirmations_base+'/'+confirmation_id+'/detail',{'auth_token':target_token})\n"
        "    detail_data=json.loads(detail_body)\n"
        "    other_response_status, other_response_body=post_json(confirmations_base+'/'+confirmation_id+'/response',{'auth_token':other_token,'decision':'confirmed','comment':'wrong user'},expect_error=True)\n"
        "    response_status, response_body=post_json(confirmations_base+'/'+confirmation_id+'/response',{'auth_token':target_token,'decision':'confirmed','comment':'resolved password=abc123','metadata':{'source':'p5_final_verify_http_confirmation_response'}})\n"
        "    response_data=json.loads(response_body)\n"
        "    pending_after_status, pending_after_body=post_json(confirmations_base+'/pending',{'auth_token':target_token})\n"
        "    pending_after=json.loads(pending_after_body)\n"
        "    responded_status, responded_body=post_json(confirmations_base+'/pending',{'auth_token':target_token,'include_responded':True})\n"
        "    responded_data=json.loads(responded_body)\n"
        "    response_text='\\n'.join([request_body,pending_body,other_pending_body,other_detail_body,detail_body,other_response_body,response_body,pending_after_body,responded_body])\n"
        "    outbox_text=outbox_path.read_text(encoding='utf-8') if outbox_path.exists() else ''\n"
        "    responses_text=responses_path.read_text(encoding='utf-8') if responses_path.exists() else ''\n"
        "    token_leak=any(token and token in (response_text+'\\n'+outbox_text+'\\n'+responses_text) for token in [target_token,other_token,admin])\n"
        "    route_leak=any(route in (response_text+'\\n'+outbox_text+'\\n'+responses_text) for route in ['p5-http-response-target','p5-http-response-other'])\n"
        "    detail_confirmation=detail_data.get('confirmation') or {}\n"
        "    response_payload=response_data.get('response') or {}\n"
        "    responded_items=responded_data.get('confirmations') or []\n"
        "    responded_match=next((item for item in responded_items if item.get('confirmation_id')==confirmation_id), {})\n"
        "    ok=(request_data.get('status')=='queued' and confirmation.get('target_user_token_hash_prefix')==target_prefix and pending_data.get('total')==1 and other_pending_data.get('total')==0 and other_detail_status==401 and detail_status==200 and detail_confirmation.get('confirmation_id')==confirmation_id and other_response_status==401 and response_data.get('status')=='recorded' and response_payload.get('decision')=='confirmed' and response_payload.get('confirmation_id')==confirmation_id and pending_after.get('total')==0 and responded_match.get('status')=='responded' and not token_leak and not route_leak)\n"
        "    print(json.dumps({'status':'validated' if ok else 'failed','confirmation_id_present':bool(confirmation_id),'target_is_current_user_token':confirmation.get('target_user_token_hash_prefix')==target_prefix,'pending_before_count':pending_data.get('total'),'other_pending_count':other_pending_data.get('total'),'other_detail_http_401':other_detail_status==401,'target_detail_ok':detail_status==200,'other_response_http_401':other_response_status==401,'target_response_recorded':response_data.get('status')=='recorded','pending_after_count':pending_after.get('total'),'include_responded_status':responded_match.get('status',''),'response_masks_tokens':not token_leak,'response_masks_routes':not route_leak,'secret_values_written':False},ensure_ascii=False,sort_keys=True))\n"
        "    sys.exit(0 if ok else 1)\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'status':'failed','error':exc.__class__.__name__,'secret_values_written':False},sort_keys=True))\n"
        "    sys.exit(1)\n"
        "finally:\n"
        "    if had_store:\n"
        "        store_path.write_bytes(store_backup)\n"
        "    else:\n"
        "        try:\n"
        "            store_path.unlink()\n"
        "        except FileNotFoundError:\n"
        "            pass\n"
        "    if had_outbox:\n"
        "        outbox_path.write_bytes(outbox_backup)\n"
        "    else:\n"
        "        try:\n"
        "            outbox_path.unlink()\n"
        "        except FileNotFoundError:\n"
        "            pass\n"
        "    if had_responses:\n"
        "        responses_path.write_bytes(responses_backup)\n"
        "    else:\n"
        "        try:\n"
        "            responses_path.unlink()\n"
        "        except FileNotFoundError:\n"
        "            pass\n"
    )
    return (python, "-c", script, confirmations_base_url, token_binding_url, env_file)


def _http_confirmation_response_summary_guard_smoke_args(
    python: str,
    confirmations_base_url: str,
    token_binding_url: str,
    env_file: str,
) -> tuple[str, ...]:
    script = (
        "import json,sys,urllib.error,urllib.request\n"
        "from pathlib import Path\n"
        "marker='p5_final_verify_http_confirmation_response_summary'\n"
        "confirmations_base=sys.argv[1].rstrip('/')\n"
        "token_url=sys.argv[2]\n"
        "env_file=sys.argv[3]\n"
        "summary_url=confirmations_base+'/responses/summary?limit=20'\n"
        "def parse_value(value):\n"
        "    value=value.strip()\n"
        "    if len(value)>=2 and value[0]==value[-1] and value[0] in ('\\\"', \"'\"):\n"
        "        return value[1:-1]\n"
        "    return value\n"
        "def read_env(path):\n"
        "    values={}\n"
        "    try:\n"
        "        lines=open(path,encoding='utf-8').read().splitlines()\n"
        "    except FileNotFoundError:\n"
        "        return values\n"
        "    for raw in lines:\n"
        "        line=raw.strip()\n"
        "        if not line or line.startswith('#'):\n"
        "            continue\n"
        "        if line.startswith('export '):\n"
        "            line=line[len('export '):].strip()\n"
        "        if '=' not in line:\n"
        "            continue\n"
        "        key,value=line.split('=',1)\n"
        "        values[key.strip()]=parse_value(value)\n"
        "    return values\n"
        "def post_json(url,payload,headers=None):\n"
        "    req=urllib.request.Request(url,data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),method='POST',headers={'Accept':'application/json','Content-Type':'application/json',**dict(headers or {})})\n"
        "    with urllib.request.urlopen(req,timeout=10) as response:\n"
        "        return response.status,response.read().decode('utf-8','replace')\n"
        "def get_json(url,headers=None):\n"
        "    req=urllib.request.Request(url,headers={'Accept':'application/json',**dict(headers or {})})\n"
        "    try:\n"
        "        with urllib.request.urlopen(req,timeout=10) as response:\n"
        "            return response.status,response.read().decode('utf-8','replace')\n"
        "    except urllib.error.HTTPError as exc:\n"
        "        return exc.code,exc.read().decode('utf-8','replace')\n"
        "values=read_env(env_file)\n"
        "admin=values.get('CODEKB_AUTH_ADMIN_TOKEN','').strip()\n"
        "store_path=Path(values.get('CODEKB_USER_TOKEN_STORE','/data/codekb/state/user-tokens.json'))\n"
        "outbox_path=Path(values.get('CODEKB_USER_CONFIRMATION_OUTBOX','/data/codekb/outbox/user-confirmation.jsonl'))\n"
        "responses_path=Path(values.get('CODEKB_USER_CONFIRMATION_RESPONSES','/data/codekb/state/user-confirmation-responses.jsonl'))\n"
        "if not admin:\n"
        "    print(json.dumps({'status':'pending_external_inputs','reason':'CODEKB_AUTH_ADMIN_TOKEN missing from env file','secret_values_written':False},sort_keys=True))\n"
        "    sys.exit(0)\n"
        "backups=[]\n"
        "for path in (store_path,outbox_path,responses_path):\n"
        "    backups.append((path,path.exists(),path.read_bytes() if path.exists() else b''))\n"
        "route='p5-response-summary-smoke-user'\n"
        "try:\n"
        "    denied_status,denied_body=get_json(summary_url)\n"
        "    issue_status,issue_body=post_json(token_url,{'display_name':'P5 response summary smoke','scopes':['diagnose'],'ttl_days':1,'metadata':{'im_userid':route,'source':marker}},{'X-CodeKB-Admin-Token':admin})\n"
        "    issued=json.loads(issue_body)\n"
        "    token=str(issued.get('token',''))\n"
        "    request_status,request_body=post_json(confirmations_base+'/request',{'auth_token':token,'reason':'problem_solved','message':'please confirm','payload':{'source':marker}})\n"
        "    confirmation_id=str((json.loads(request_body).get('confirmation') or {}).get('confirmation_id',''))\n"
        "    response_status,response_body=post_json(confirmations_base+'/'+confirmation_id+'/response',{'auth_token':token,'decision':'confirmed','comment':'done password=abc123','metadata':{'source':marker,'url':'https://kb.example/path?token=secret-token'}})\n"
        "    summary_status,summary_body=get_json(summary_url,headers={'X-CodeKB-Admin-Token':admin})\n"
        "    summary=json.loads(summary_body) if summary_status==200 else {}\n"
        "    responses=summary.get('responses') or []\n"
        "    target=[item for item in responses if item.get('confirmation_id')==confirmation_id]\n"
        "    target_item=target[0] if target else {}\n"
        "    raw_summary=json.dumps(summary,ensure_ascii=False)\n"
        "    files_text='\\n'.join(path.read_text(encoding='utf-8') for path in (store_path,outbox_path,responses_path) if path.exists())\n"
        "    summary_masks=bool(token not in raw_summary and admin not in raw_summary and route not in raw_summary and 'abc123' not in raw_summary and 'secret-token' not in raw_summary and '\\\"responder_user_token_hash\\\"' not in raw_summary and '\\\"responder_user_token_hash_prefix\\\"' in raw_summary)\n"
        "    files_mask=bool(token not in files_text)\n"
        "    ok=bool(denied_status==401 and issue_status==200 and request_status==200 and confirmation_id and response_status==200 and summary_status==200 and target and target_item.get('decision')=='confirmed' and '[REDACTED]' in raw_summary and summary_masks and files_mask)\n"
        "    print(json.dumps({'status':'validated' if ok else 'failed','denied_without_admin_http_401':denied_status==401,'confirmation_id_present':bool(confirmation_id),'response_recorded':response_status==200,'summary_http_200':summary_status==200,'summary_contains_response':bool(target),'summary_decision':target_item.get('decision',''),'summary_masks_secrets':summary_masks,'files_mask_token_and_route':files_mask,'secret_values_written':False},ensure_ascii=False,sort_keys=True))\n"
        "    sys.exit(0 if ok else 1)\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'status':'failed','error':exc.__class__.__name__,'secret_values_written':False},sort_keys=True))\n"
        "    sys.exit(1)\n"
        "finally:\n"
        "    for path,existed,data in backups:\n"
        "        if existed:\n"
        "            path.parent.mkdir(parents=True,exist_ok=True)\n"
        "            path.write_bytes(data)\n"
        "        else:\n"
        "            try:\n"
        "                path.unlink()\n"
        "            except FileNotFoundError:\n"
        "                pass\n"
    )
    return (python, "-c", script, confirmations_base_url, token_binding_url, env_file)


def _http_diagnose_confirmation_policy_smoke_args(
    python: str,
    diagnose_url: str,
    token_binding_url: str,
    env_file: str,
) -> tuple[str, ...]:
    script = (
        "import hashlib,json,sys,urllib.error,urllib.request;"
        "from pathlib import Path;"
        "diagnose_url=sys.argv[1];token_url=sys.argv[2];env_file=sys.argv[3];"
        "\n"
        "def parse_value(value):\n"
        "    value=value.strip()\n"
        "    if len(value)>=2 and value[0]==value[-1] and value[0] in ('\\\"', \"'\"):\n"
        "        return value[1:-1]\n"
        "    return value\n"
        "def read_env(path):\n"
        "    values={}\n"
        "    try:\n"
        "        lines=open(path,encoding='utf-8').read().splitlines()\n"
        "    except FileNotFoundError:\n"
        "        return values\n"
        "    for raw in lines:\n"
        "        line=raw.strip()\n"
        "        if not line or line.startswith('#'):\n"
        "            continue\n"
        "        if line.startswith('export '):\n"
        "            line=line[len('export '):].strip()\n"
        "        if '=' not in line:\n"
        "            continue\n"
        "        key,value=line.split('=',1)\n"
        "        values[key.strip()]=parse_value(value)\n"
        "    return values\n"
        "values=read_env(env_file)\n"
        "admin=values.get('CODEKB_AUTH_ADMIN_TOKEN','').strip()\n"
        "store_path=Path(values.get('CODEKB_USER_TOKEN_STORE','/data/codekb/state/user-tokens.json'))\n"
        "outbox_path=Path(values.get('CODEKB_USER_CONFIRMATION_OUTBOX','/data/codekb/outbox/user-confirmation.jsonl'))\n"
        "if not admin:\n"
        "    print(json.dumps({'status':'pending_external_inputs','reason':'CODEKB_AUTH_ADMIN_TOKEN missing from env file','secret_values_written':False},sort_keys=True))\n"
        "    sys.exit(0)\n"
        "had_store=store_path.exists()\n"
        "store_backup=store_path.read_bytes() if had_store else b''\n"
        "had_outbox=outbox_path.exists()\n"
        "outbox_backup=outbox_path.read_bytes() if had_outbox else b''\n"
        "route='p5-diagnose-confirmation-smoke-user'\n"
        "try:\n"
        "    issue_payload=json.dumps({\n"
        "        'display_name':'P5 diagnose confirmation smoke',\n"
        "        'scopes':['diagnose'],\n"
        "        'ttl_days':1,\n"
        "        'metadata':{'im_userid':route,'source':'p5_final_verify'},\n"
        "    }).encode('utf-8')\n"
        "    issue_req=urllib.request.Request(token_url,data=issue_payload,method='POST',headers={'Accept':'application/json','Content-Type':'application/json','X-CodeKB-Admin-Token':admin})\n"
        "    issue_body=urllib.request.urlopen(issue_req,timeout=10).read().decode('utf-8','replace')\n"
        "    issued=json.loads(issue_body)\n"
        "    token=str(issued.get('token',''))\n"
        "    bad_denied=False\n"
        "    bad_leak=False\n"
        "    bad_payload=json.dumps({'query':'DEVICE_SEQ 是什么？','sub_kbs':['testing'],'top_k':1,'confirmation_policy':'always','auth_token':'bad-current-user-token'}).encode('utf-8')\n"
        "    try:\n"
        "        bad_req=urllib.request.Request(diagnose_url,data=bad_payload,method='POST',headers={'Accept':'application/json','Content-Type':'application/json'})\n"
        "        urllib.request.urlopen(bad_req,timeout=20).read().decode('utf-8','replace')\n"
        "    except urllib.error.HTTPError as exc:\n"
        "        bad_body=exc.read().decode('utf-8','replace')\n"
        "        bad_denied=exc.code==401\n"
        "        bad_leak=token in bad_body or admin in bad_body or route in bad_body\n"
        "    confirm_payload=json.dumps({\n"
        "        'query':'DEVICE_SEQ 是什么？',\n"
        "        'sub_kbs':['testing'],\n"
        "        'top_k':1,\n"
        "        'auth_token':token,\n"
        "        'confirmation_policy':'always',\n"
        "        'confirmation_reason':'problem_solved',\n"
        "        'confirmation_message':'请确认本次问题是否已解决',\n"
        "        'confirmation_payload':{'source':'p5_final_verify_http_diagnose_confirmation'},\n"
        "    }).encode('utf-8')\n"
        "    confirm_req=urllib.request.Request(diagnose_url,data=confirm_payload,method='POST',headers={'Accept':'application/json','Content-Type':'application/json'})\n"
        "    confirm_body=urllib.request.urlopen(confirm_req,timeout=20).read().decode('utf-8','replace')\n"
        "    data=json.loads(confirm_body)\n"
        "    confirmation=data.get('confirmation') or {}\n"
        "    token_hash=hashlib.sha256(token.encode('utf-8')).hexdigest()\n"
        "    outbox_text=outbox_path.read_text(encoding='utf-8') if outbox_path.exists() else ''\n"
        "    response_masks=(token not in confirm_body and admin not in confirm_body and route not in confirm_body)\n"
        "    outbox_hashes=(token_hash in outbox_text and token not in outbox_text)\n"
        "    outbox_masks=(admin not in outbox_text and route not in outbox_text)\n"
        "    confirmation_ok=bool(confirmation.get('confirmation_id') and confirmation.get('reason')=='problem_solved' and confirmation.get('target_user_token_hash_prefix')==token_hash[:12])\n"
        "    diagnosis_ok=bool(data.get('diagnosis_id') and data.get('answer_id'))\n"
        "    ok=bool(token.startswith('lkb_') and bad_denied and not bad_leak and diagnosis_ok and confirmation_ok and response_masks and outbox_hashes and outbox_masks)\n"
        "    print(json.dumps({\n"
        "        'status':'validated' if ok else 'failed',\n"
        "        'invalid_token_http_401':bad_denied,\n"
        "        'diagnosis_returned':diagnosis_ok,\n"
        "        'confirmation_queued':confirmation_ok,\n"
        "        'target_is_current_user_token':confirmation.get('target_user_token_hash_prefix')==token_hash[:12],\n"
        "        'response_masks_token':response_masks,\n"
        "        'outbox_hashes_token':outbox_hashes,\n"
        "        'outbox_masks_route':outbox_masks,\n"
        "        'secret_values_written':False,\n"
        "    },ensure_ascii=False,sort_keys=True))\n"
        "    sys.exit(0 if ok else 1)\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'status':'failed','error':exc.__class__.__name__,'secret_values_written':False},sort_keys=True))\n"
        "    sys.exit(1)\n"
        "finally:\n"
        "    if had_store:\n"
        "        store_path.write_bytes(store_backup)\n"
        "    else:\n"
        "        try:\n"
        "            store_path.unlink()\n"
        "        except FileNotFoundError:\n"
        "            pass\n"
        "    if had_outbox:\n"
        "        outbox_path.write_bytes(outbox_backup)\n"
        "    else:\n"
        "        try:\n"
        "            outbox_path.unlink()\n"
        "        except FileNotFoundError:\n"
        "            pass\n"
    )
    return (python, "-c", script, diagnose_url, token_binding_url, env_file)


def _http_webhook_confirmation_policy_smoke_args(
    python: str,
    webhook_url: str,
    token_binding_url: str,
    env_file: str,
) -> tuple[str, ...]:
    script = (
        "import hashlib,json,sys,urllib.error,urllib.request;"
        "from pathlib import Path;"
        "webhook_url=sys.argv[1];token_url=sys.argv[2];env_file=sys.argv[3];"
        "\n"
        "def parse_value(value):\n"
        "    value=value.strip()\n"
        "    if len(value)>=2 and value[0]==value[-1] and value[0] in ('\\\"', \"'\"):\n"
        "        return value[1:-1]\n"
        "    return value\n"
        "def read_env(path):\n"
        "    values={}\n"
        "    try:\n"
        "        lines=open(path,encoding='utf-8').read().splitlines()\n"
        "    except FileNotFoundError:\n"
        "        return values\n"
        "    for raw in lines:\n"
        "        line=raw.strip()\n"
        "        if not line or line.startswith('#'):\n"
        "            continue\n"
        "        if line.startswith('export '):\n"
        "            line=line[len('export '):].strip()\n"
        "        if '=' not in line:\n"
        "            continue\n"
        "        key,value=line.split('=',1)\n"
        "        values[key.strip()]=parse_value(value)\n"
        "    return values\n"
        "values=read_env(env_file)\n"
        "admin=values.get('CODEKB_AUTH_ADMIN_TOKEN','').strip()\n"
        "webhook_token=values.get('CODEKB_DIAGNOSE_WEBHOOK_TOKEN','').strip()\n"
        "store_path=Path(values.get('CODEKB_USER_TOKEN_STORE','/data/codekb/state/user-tokens.json'))\n"
        "outbox_path=Path(values.get('CODEKB_USER_CONFIRMATION_OUTBOX','/data/codekb/outbox/user-confirmation.jsonl'))\n"
        "log_path=Path(values.get('CODEKB_DIAGNOSE_WEBHOOK_LOG','/data/codekb/logs/diagnose-webhook.jsonl'))\n"
        "missing=[]\n"
        "if not admin:\n"
        "    missing.append('CODEKB_AUTH_ADMIN_TOKEN')\n"
        "if not webhook_token:\n"
        "    missing.append('CODEKB_DIAGNOSE_WEBHOOK_TOKEN')\n"
        "if missing:\n"
        "    print(json.dumps({'status':'pending_external_inputs','reason':','.join(missing)+' missing from env file','secret_values_written':False},sort_keys=True))\n"
        "    sys.exit(0)\n"
        "had_store=store_path.exists(); store_backup=store_path.read_bytes() if had_store else b''\n"
        "had_outbox=outbox_path.exists(); outbox_backup=outbox_path.read_bytes() if had_outbox else b''\n"
        "had_log=log_path.exists(); log_backup=log_path.read_bytes() if had_log else b''\n"
        "route='p5-webhook-confirmation-smoke-user'\n"
        "try:\n"
        "    issue_payload=json.dumps({\n"
        "        'display_name':'P5 webhook confirmation smoke',\n"
        "        'scopes':['diagnose'],\n"
        "        'ttl_days':1,\n"
        "        'metadata':{'im_userid':route,'source':'p5_final_verify'},\n"
        "    }).encode('utf-8')\n"
        "    issue_req=urllib.request.Request(token_url,data=issue_payload,method='POST',headers={'Accept':'application/json','Content-Type':'application/json','X-CodeKB-Admin-Token':admin})\n"
        "    issue_body=urllib.request.urlopen(issue_req,timeout=10).read().decode('utf-8','replace')\n"
        "    issued=json.loads(issue_body)\n"
        "    token=str(issued.get('token',''))\n"
        "    base_payload={\n"
        "        'repository':{'path':'ym/app'},\n"
        "        'error':{'code':'DEVICE_SEQ','message':'DEVICE_SEQ 构建失败'},\n"
        "        'sub_kbs':['testing'],\n"
        "        'confirmation_policy':'always',\n"
        "        'confirmation_reason':'gap_candidate_review',\n"
        "        'confirmation_message':'请确认是否提交本次 KB 缺口候选',\n"
        "        'confirmation_payload':{'source':'p5_final_verify_http_webhook_confirmation'},\n"
        "    }\n"
        "    bad_denied=False; bad_leak=False\n"
        "    bad_payload=json.dumps({**base_payload,'auth_token':'bad-current-user-token'}).encode('utf-8')\n"
        "    try:\n"
        "        bad_req=urllib.request.Request(webhook_url,data=bad_payload,method='POST',headers={'Accept':'application/json','Content-Type':'application/json','X-CodeKB-Token':webhook_token})\n"
        "        urllib.request.urlopen(bad_req,timeout=20).read().decode('utf-8','replace')\n"
        "    except urllib.error.HTTPError as exc:\n"
        "        bad_body=exc.read().decode('utf-8','replace')\n"
        "        bad_denied=exc.code==401\n"
        "        bad_leak=token in bad_body or admin in bad_body or webhook_token in bad_body or route in bad_body\n"
        "    ok_payload=json.dumps({**base_payload,'auth_token':token}).encode('utf-8')\n"
        "    ok_req=urllib.request.Request(webhook_url,data=ok_payload,method='POST',headers={'Accept':'application/json','Content-Type':'application/json','X-CodeKB-Token':webhook_token})\n"
        "    ok_body=urllib.request.urlopen(ok_req,timeout=20).read().decode('utf-8','replace')\n"
        "    data=json.loads(ok_body)\n"
        "    confirmation=data.get('confirmation') or {}\n"
        "    token_hash=hashlib.sha256(token.encode('utf-8')).hexdigest()\n"
        "    outbox_text=outbox_path.read_text(encoding='utf-8') if outbox_path.exists() else ''\n"
        "    log_text=log_path.read_text(encoding='utf-8') if log_path.exists() else ''\n"
        "    combined=ok_body+'\\n'+outbox_text+'\\n'+log_text\n"
        "    masks=(token not in combined and admin not in combined and webhook_token not in combined and route not in combined and 'bad-current-user-token' not in combined)\n"
        "    log_masks=('auth_token' not in log_text and 'confirmation_policy' not in log_text)\n"
        "    outbox_hashes=(token_hash in outbox_text and token not in outbox_text)\n"
        "    confirmation_ok=bool(confirmation.get('confirmation_id') and confirmation.get('reason')=='gap_candidate_review' and confirmation.get('target_user_token_hash_prefix')==token_hash[:12])\n"
        "    diagnosis_ok=bool(data.get('status')=='diagnosed' and data.get('diagnosis',{}).get('diagnosis_id'))\n"
        "    ok=bool(token.startswith('lkb_') and bad_denied and not bad_leak and diagnosis_ok and confirmation_ok and masks and log_masks and outbox_hashes)\n"
        "    print(json.dumps({\n"
        "        'status':'validated' if ok else 'failed',\n"
        "        'invalid_user_token_http_401':bad_denied,\n"
        "        'diagnosis_returned':diagnosis_ok,\n"
        "        'confirmation_queued':confirmation_ok,\n"
        "        'target_is_current_user_token':confirmation.get('target_user_token_hash_prefix')==token_hash[:12],\n"
        "        'response_outbox_log_masks_secrets':masks,\n"
        "        'webhook_log_excludes_confirmation_args':log_masks,\n"
        "        'outbox_hashes_token':outbox_hashes,\n"
        "        'secret_values_written':False,\n"
        "    },ensure_ascii=False,sort_keys=True))\n"
        "    sys.exit(0 if ok else 1)\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'status':'failed','error':exc.__class__.__name__,'secret_values_written':False},sort_keys=True))\n"
        "    sys.exit(1)\n"
        "finally:\n"
        "    if had_store:\n"
        "        store_path.write_bytes(store_backup)\n"
        "    else:\n"
        "        try: store_path.unlink()\n"
        "        except FileNotFoundError: pass\n"
        "    if had_outbox:\n"
        "        outbox_path.write_bytes(outbox_backup)\n"
        "    else:\n"
        "        try: outbox_path.unlink()\n"
        "        except FileNotFoundError: pass\n"
        "    if had_log:\n"
        "        log_path.write_bytes(log_backup)\n"
        "    else:\n"
        "        try: log_path.unlink()\n"
        "        except FileNotFoundError: pass\n"
    )
    return (python, "-c", script, webhook_url, token_binding_url, env_file)


def _http_webhook_token_guard_args(python: str, url: str, env_file: str) -> tuple[str, ...]:
    script = (
        "import json,sys,urllib.error,urllib.request;"
        "url=sys.argv[1];env_file=sys.argv[2];"
        "\n"
        "def parse_value(value):\n"
        "    value=value.strip()\n"
        "    if len(value)>=2 and value[0]==value[-1] and value[0] in ('\\\"', \"'\"):\n"
        "        return value[1:-1]\n"
        "    return value\n"
        "def read_token(path):\n"
        "    try:\n"
        "        lines=open(path,encoding='utf-8').read().splitlines()\n"
        "    except FileNotFoundError:\n"
        "        return ''\n"
        "    for raw in lines:\n"
        "        line=raw.strip()\n"
        "        if not line or line.startswith('#'):\n"
        "            continue\n"
        "        if line.startswith('export '):\n"
        "            line=line[len('export '):].strip()\n"
        "        if '=' not in line:\n"
        "            continue\n"
        "        key,value=line.split('=',1)\n"
        "        if key.strip()=='CODEKB_DIAGNOSE_WEBHOOK_TOKEN':\n"
        "            return parse_value(value)\n"
        "    return ''\n"
        "token=read_token(env_file).strip()\n"
        "if not token:\n"
        "    print(json.dumps({'status':'pending_external_inputs','reason':'CODEKB_DIAGNOSE_WEBHOOK_TOKEN missing from env file','secret_values_written':False},sort_keys=True))\n"
        "    sys.exit(0)\n"
        "payload=json.dumps({\n"
        "    'repository':{'path':'ym/app'},\n"
        "    'error':{'code':'DEVICE_SEQ','message':'DEVICE_SEQ build failed'},\n"
        "    'sub_kbs':['testing'],\n"
        "}).encode('utf-8')\n"
        "missing_denied=False\n"
        "missing_leak=False\n"
        "try:\n"
        "    req=urllib.request.Request(url,data=payload,method='POST',headers={'Accept':'application/json','Content-Type':'application/json'})\n"
        "    urllib.request.urlopen(req,timeout=10).read().decode('utf-8','replace')\n"
        "except urllib.error.HTTPError as exc:\n"
        "    body=exc.read().decode('utf-8','replace')\n"
        "    missing_denied=exc.code==401\n"
        "    missing_leak=token in body\n"
        "except Exception:\n"
        "    missing_denied=False\n"
        "try:\n"
        "    req=urllib.request.Request(url,data=payload,method='POST',headers={'Accept':'application/json','Content-Type':'application/json','X-CodeKB-Token':token})\n"
        "    body=urllib.request.urlopen(req,timeout=10).read().decode('utf-8','replace')\n"
        "    data=json.loads(body)\n"
        "    valid_ok=data.get('status')=='validated' and bool(data.get('valid')) and bool(data.get('query_ready'))\n"
        "    leak=(token in body)\n"
        "except Exception as exc:\n"
        "    data={'error':exc.__class__.__name__}\n"
        "    valid_ok=False\n"
        "    leak=False\n"
        "ok=bool(missing_denied and not missing_leak and valid_ok and not leak)\n"
        "print(json.dumps({\n"
        "    'status':'validated' if ok else 'failed',\n"
        "    'missing_token_http_401':missing_denied,\n"
        "    'valid_token_validated':valid_ok,\n"
        "    'response_masks_webhook_token':not leak and not missing_leak,\n"
        "    'query_ready':bool(data.get('query_ready')) if isinstance(data,dict) else False,\n"
        "    'secret_values_written':False,\n"
        "},ensure_ascii=False,sort_keys=True))\n"
        "sys.exit(0 if ok else 1)\n"
    )
    return (python, "-c", script, url, env_file)


def _http_webhook_sample_import_smoke_args(python: str, url: str, env_file: str) -> tuple[str, ...]:
    script = (
        "import json,sys,urllib.error,urllib.request;"
        "from pathlib import Path;"
        "url=sys.argv[1];env_file=sys.argv[2];"
        "\n"
        "def parse_value(value):\n"
        "    value=value.strip()\n"
        "    if len(value)>=2 and value[0]==value[-1] and value[0] in ('\\\"', \"'\"):\n"
        "        return value[1:-1]\n"
        "    return value\n"
        "def read_env(path):\n"
        "    values={}\n"
        "    try:\n"
        "        lines=open(path,encoding='utf-8').read().splitlines()\n"
        "    except FileNotFoundError:\n"
        "        return values\n"
        "    for raw in lines:\n"
        "        line=raw.strip()\n"
        "        if not line or line.startswith('#'):\n"
        "            continue\n"
        "        if line.startswith('export '):\n"
        "            line=line[len('export '):].strip()\n"
        "        if '=' not in line:\n"
        "            continue\n"
        "        key,value=line.split('=',1)\n"
        "        values[key.strip()]=parse_value(value)\n"
        "    return values\n"
        "values=read_env(env_file)\n"
        "admin=values.get('CODEKB_AUTH_ADMIN_TOKEN','').strip()\n"
        "if not admin:\n"
        "    print(json.dumps({'status':'pending_external_inputs','reason':'CODEKB_AUTH_ADMIN_TOKEN missing from env file','secret_values_written':False},sort_keys=True))\n"
        "    sys.exit(0)\n"
        "active=values.get('CODEKB_DIAGNOSE_WEBHOOK_SAMPLES','').strip()\n"
        "target=values.get('CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES','').strip()\n"
        "if not target:\n"
        "    normalized=active.replace('\\\\','/')\n"
        "    if normalized and normalized!='docs/diagnose-webhook-samples.draft.yaml' and not normalized.endswith('/diagnose-webhook-samples.draft.yaml'):\n"
        "        target=active\n"
        "    else:\n"
        "        target='/data/codekb/state/diagnose-webhook-samples.real.yaml'\n"
        "target_path=Path(target)\n"
        "had_target=target_path.exists()\n"
        "backup=target_path.read_bytes() if had_target else b''\n"
        "secret_values=['p5-import-token-secret','p5-import-password-secret']\n"
        "request_payload={\n"
        "    'name':'p5_final_verify_import_smoke',\n"
        "    'append':True,\n"
        "    'payload':{\n"
        "        'repository':{'path':'ym/app'},\n"
        "        'pipeline':{'url':'https://example.invalid/build?token='+secret_values[0]},\n"
        "        'error':{'code':'DEVICE_SEQ','message':'DEVICE_SEQ password='+secret_values[1]},\n"
        "        'sub_kbs':['testing'],\n"
        "    },\n"
        "}\n"
        "payload=json.dumps(request_payload).encode('utf-8')\n"
        "denied_401=False\n"
        "denied_leak=False\n"
        "try:\n"
        "    req=urllib.request.Request(url,data=payload,method='POST',headers={'Accept':'application/json','Content-Type':'application/json'})\n"
        "    urllib.request.urlopen(req,timeout=10).read().decode('utf-8','replace')\n"
        "except urllib.error.HTTPError as exc:\n"
        "    body=exc.read().decode('utf-8','replace')\n"
        "    denied_401=exc.code==401\n"
        "    denied_leak=any(value in body for value in secret_values) or admin in body\n"
        "except Exception:\n"
        "    denied_401=False\n"
        "try:\n"
        "    req=urllib.request.Request(url,data=payload,method='POST',headers={'Accept':'application/json','Content-Type':'application/json','X-CodeKB-Admin-Token':admin})\n"
        "    body=urllib.request.urlopen(req,timeout=10).read().decode('utf-8','replace')\n"
        "    data=json.loads(body)\n"
        "    output_text=target_path.read_text(encoding='utf-8') if target_path.exists() else ''\n"
        "    response_leak=any(value in body for value in secret_values) or admin in body\n"
        "    file_leak=any(value in output_text for value in secret_values) or admin in output_text\n"
        "    ok=bool(denied_401 and not denied_leak and data.get('status')=='imported' and (data.get('validation') or {}).get('status')=='passed' and data.get('raw_sensitive_values_detected',0)>=2 and not data.get('raw_sensitive_values_leaked') and not response_leak and not file_leak)\n"
        "    print(json.dumps({\n"
        "        'status':'validated' if ok else 'failed',\n"
        "        'denied_without_admin':denied_401,\n"
        "        'import_status':data.get('status',''),\n"
        "        'validation_status':(data.get('validation') or {}).get('status',''),\n"
        "        'raw_sensitive_values_detected':data.get('raw_sensitive_values_detected',0),\n"
        "        'response_masks_secrets':not response_leak and not denied_leak,\n"
        "        'output_masks_secrets':not file_leak,\n"
        "        'output_is_active':bool(data.get('output_is_active')),\n"
        "        'secret_values_written':False,\n"
        "    },ensure_ascii=False,sort_keys=True))\n"
        "    sys.exit(0 if ok else 1)\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'status':'failed','error':exc.__class__.__name__,'secret_values_written':False},sort_keys=True))\n"
        "    sys.exit(1)\n"
        "finally:\n"
        "    if had_target:\n"
        "        target_path.parent.mkdir(parents=True,exist_ok=True)\n"
        "        target_path.write_bytes(backup)\n"
        "    else:\n"
        "        try:\n"
        "            target_path.unlink()\n"
        "        except FileNotFoundError:\n"
        "            pass\n"
    )
    return (python, "-c", script, url, env_file)


def _http_im_oauth_callback_guard_args(python: str, url: str, env_file: str) -> tuple[str, ...]:
    script = (
        "import json,sys,urllib.error,urllib.parse,urllib.request\n"
        "from pathlib import Path\n"
        "url=sys.argv[1];env_file=sys.argv[2]\n"
        "def parse_value(value):\n"
        "    value=value.strip()\n"
        "    if len(value)>=2 and value[0]==value[-1] and value[0] in ('\\\"', \"'\"):\n"
        "        return value[1:-1]\n"
        "    return value\n"
        "def read_env(path):\n"
        "    values={}\n"
        "    try:\n"
        "        lines=open(path,encoding='utf-8').read().splitlines()\n"
        "    except FileNotFoundError:\n"
        "        return values\n"
        "    for raw in lines:\n"
        "        line=raw.strip()\n"
        "        if not line or line.startswith('#'):\n"
        "            continue\n"
        "        if line.startswith('export '):\n"
        "            line=line[len('export '):].strip()\n"
        "        if '=' not in line:\n"
        "            continue\n"
        "        key,value=line.split('=',1)\n"
        "        values[key.strip()]=parse_value(value)\n"
        "    return values\n"
        "values=read_env(env_file)\n"
        "state_secret=values.get('CODEKB_IM_OAUTH_STATE_SECRET','').strip()\n"
        "store_path=Path(values.get('CODEKB_USER_TOKEN_STORE','/data/codekb/state/user-tokens.json'))\n"
        "if not state_secret:\n"
        "    print(json.dumps({'status':'pending_external_inputs','reason':'CODEKB_IM_OAUTH_STATE_SECRET missing from env file','secret_values_written':False},sort_keys=True))\n"
        "    sys.exit(0)\n"
        "had_store=store_path.exists()\n"
        "before=store_path.read_bytes() if had_store else b''\n"
        "bad_state='p5-invalid-oauth-state-token'\n"
        "bad_code='p5-invalid-oauth-code'\n"
        "target=url+'?'+urllib.parse.urlencode({'code':bad_code,'state':bad_state})\n"
        "try:\n"
        "    try:\n"
        "        req=urllib.request.Request(target,headers={'Accept':'text/html,application/json'})\n"
        "        body=urllib.request.urlopen(req,timeout=10).read().decode('utf-8','replace')\n"
        "        status=200\n"
        "    except urllib.error.HTTPError as exc:\n"
        "        status=exc.code\n"
        "        body=exc.read().decode('utf-8','replace')\n"
        "    after=store_path.read_bytes() if store_path.exists() else b''\n"
        "    store_unchanged=(had_store==store_path.exists() and before==after)\n"
        "    body_masks=state_secret not in body and bad_state not in body and bad_code not in body and 'lkb_' not in body\n"
        "    ok=bool(status==400 and store_unchanged and body_masks)\n"
        "    print(json.dumps({'status':'validated' if ok else 'failed','invalid_state_http_400':status==400,'token_store_unchanged':store_unchanged,'response_masks_state_secret':state_secret not in body,'response_masks_bad_state':bad_state not in body,'response_masks_bad_code':bad_code not in body,'no_token_issued':'lkb_' not in body,'secret_values_written':False},ensure_ascii=False,sort_keys=True))\n"
        "    sys.exit(0 if ok else 1)\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'status':'failed','error':exc.__class__.__name__,'secret_values_written':False},sort_keys=True))\n"
        "    sys.exit(1)\n"
        "finally:\n"
        "    if had_store:\n"
        "        store_path.parent.mkdir(parents=True,exist_ok=True)\n"
        "        store_path.write_bytes(before)\n"
        "    else:\n"
        "        try:\n"
        "            store_path.unlink()\n"
        "        except FileNotFoundError:\n"
        "            pass\n"
    )
    return (python, "-c", script, url, env_file)


def _http_im_configure_plan_args(python: str, url: str, env_file: str) -> tuple[str, ...]:
    script = (
        "import json,sys,urllib.error,urllib.request;"
        "url=sys.argv[1];env_file=sys.argv[2];"
        "\n"
        "def parse_value(value):\n"
        "    value=value.strip()\n"
        "    if len(value)>=2 and value[0]==value[-1] and value[0] in ('\\\"', \"'\"):\n"
        "        return value[1:-1]\n"
        "    return value\n"
        "def read_admin_token(path):\n"
        "    try:\n"
        "        lines=open(path,encoding='utf-8').read().splitlines()\n"
        "    except FileNotFoundError:\n"
        "        return ''\n"
        "    for raw in lines:\n"
        "        line=raw.strip()\n"
        "        if not line or line.startswith('#'):\n"
        "            continue\n"
        "        if line.startswith('export '):\n"
        "            line=line[len('export '):].strip()\n"
        "        if '=' not in line:\n"
        "            continue\n"
        "        key,value=line.split('=',1)\n"
        "        if key.strip()=='CODEKB_AUTH_ADMIN_TOKEN':\n"
        "            return parse_value(value)\n"
        "    return ''\n"
        "admin=read_admin_token(env_file).strip()\n"
        "if not admin:\n"
        "    print(json.dumps({'status':'pending_external_inputs','reason':'CODEKB_AUTH_ADMIN_TOKEN missing from env file','secret_values_written':False}))\n"
        "    sys.exit(0)\n"
        "markers=['p5_plan_corp','p5_plan_app','p5_plan_state'];"
        "payload=json.dumps({"
        "'corp_id':markers[0],"
        "'agent_id':'100001',"
        "'app_secret':markers[1],"
        "'oauth_state_secret':markers[2],"
        "'redirect_uri':'http://127.0.0.1:8080/auth/im/oauth/callback',"
        "'apply':False"
        "}).encode('utf-8');"
        "req=urllib.request.Request(url,data=payload,method='POST',headers={'Accept':'application/json','Content-Type':'application/json','X-CodeKB-Admin-Token':admin});"
        "\ntry:\n"
        "    body=urllib.request.urlopen(req,timeout=10).read().decode('utf-8','replace')\n"
        "    leaked=any(marker in body for marker in markers) or admin in body\n"
        "    data=json.loads(body)\n"
        "    ok=bool(data.get('ok')) and not data.get('applied') and not leaked\n"
        "    print(json.dumps({'status':'validated' if ok else 'failed','api_status':data.get('status',''),'applied':bool(data.get('applied')),'secret_leak':leaked,'planned_update_keys':data.get('planned_update_keys',[]),'secret_values_written':False}))\n"
        "    sys.exit(0 if ok else 1)\n"
        "except urllib.error.HTTPError as exc:\n"
        "    body=exc.read().decode('utf-8','replace')\n"
        "    leaked=any(marker in body for marker in markers) or admin in body\n"
        "    print(json.dumps({'status':'failed','http_status':exc.code,'secret_leak':leaked,'secret_values_written':False}))\n"
        "    sys.exit(1)\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'status':'failed','error':exc.__class__.__name__,'secret_values_written':False}))\n"
        "    sys.exit(1)\n"
    )
    return (python, "-c", script, url, env_file)


def _parse_json(stdout: str) -> Any | None:
    text = str(stdout or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _command_status(command_id: str, exit_code: int, parsed: Any | None) -> str:
    if isinstance(parsed, dict):
        json_status = _json_status(command_id, parsed)
        if json_status:
            if exit_code != 0 and json_status not in {"accepted", "pending"}:
                return "failed"
            return json_status
    if exit_code == 0:
        return "passed"
    return "failed"


def _json_status(command_id: str, parsed: dict[str, Any]) -> str:
    status = str(parsed.get("status", "") or "").strip()
    if parsed.get("accepted") is True:
        return "accepted"
    if command_id in {"acceptance", "http_acceptance"} and status == "pending_external_inputs":
        return "pending"
    if command_id in {"readiness", "http_readiness"}:
        if status == "ready":
            return "passed"
        if status == "ready_with_warnings":
            return "pending"
        if status == "blocked":
            return "failed"
    if command_id == "external_inputs":
        if status == "complete":
            return "passed"
        if status == "pending_external_inputs":
            return "pending"
    if command_id in {"external_state", "http_external_state"}:
        if status == "ready":
            return "passed"
        if status == "pending_external_inputs":
            return "pending"
    if command_id == "http_final_verification_guide":
        if status in {"accepted", "pending_external_inputs"}:
            return "passed"
    if command_id == "http_mcp_setup_status":
        if status in {"ready", "pending_oauth_config"}:
            return "passed"
    if command_id == "sample_suite":
        if status == "passed":
            return "passed"
        if status:
            return "failed"
    if command_id == "p3_usecase_smoke":
        if status == "passed":
            return "passed"
        if status:
            return "failed"
    if command_id == "im_oauth_smoke":
        if status == "verified":
            return "passed"
        if status.startswith("blocked_") or status in {"missing_secret", "pending_external_inputs"}:
            return "pending"
        if status:
            return "failed"
    if command_id in {"im_smoke", "current_user_smoke", "http_current_user_smoke"}:
        if status in {"validated", "responded", "executed", "credentials_verified"}:
            return "passed"
        if status.startswith("blocked_") or status in {"pending_external_inputs"}:
            return "pending"
        if status:
            return "failed"
    if command_id == "http_confirmation_request":
        if status == "queued" and parsed.get("confirmation"):
            return "passed"
        if status.startswith("blocked_") or status in {"pending_external_inputs"}:
            return "pending"
        if status:
            return "failed"
    if status in {"ok", "validated", "passed"}:
        return "passed"
    if status in {"pending", "pending_external_inputs", "ready_with_warnings"}:
        return "pending"
    if status in {"blocked", "failed", "error"}:
        return "failed"
    return ""


def _accepted(results: list[dict[str, Any]]) -> bool:
    for result in results:
        if result["id"] == "acceptance" and isinstance(result.get("json"), dict):
            return bool(result["json"].get("accepted"))
    return False


def _overall_status(
    results: list[dict[str, Any]],
    *,
    accepted: bool,
    failed_required: list[dict[str, Any]],
    pending_required: list[dict[str, Any]],
) -> str:
    if accepted and not failed_required and not pending_required:
        return "accepted"
    if failed_required:
        return "failed"
    acceptance = next((result for result in results if result["id"] == "acceptance"), None)
    if acceptance and isinstance(acceptance.get("json"), dict):
        status = str(acceptance["json"].get("status", "") or "")
        if status:
            return status
    if pending_required:
        return "pending_external_inputs"
    return "pending_external_inputs"


def _next_steps(results: list[dict[str, Any]], *, status: str) -> list[str]:
    if status == "accepted":
        return []
    external_inputs = next((result for result in results if result["id"] == "external_inputs"), None)
    if external_inputs and isinstance(external_inputs.get("json"), dict):
        steps = _external_input_next_steps(external_inputs["json"])
        if steps:
            return steps
    acceptance = next((result for result in results if result["id"] == "acceptance"), None)
    if acceptance and isinstance(acceptance.get("json"), dict):
        external_inputs = acceptance["json"].get("external_inputs") or []
        if external_inputs:
            return [
                f"resolve {item.get('check_id', 'unknown')}: {item.get('evidence_needed', '')}"
                for item in external_inputs
                if isinstance(item, dict)
            ]
    failed = [result["id"] for result in results if result["required"] and result["status"] == "failed"]
    if failed:
        return [f"inspect failed required checks: {', '.join(failed)}"]
    return ["rerun diagnose-p5-final-verify after external inputs are configured"]


def _external_input_next_steps(plan: dict[str, Any]) -> list[str]:
    if plan.get("status") == "complete":
        return []
    tasks = [item for item in plan.get("tasks") or [] if isinstance(item, dict)]
    if not tasks:
        return []
    by_id = {str(item.get("check_id") or ""): item for item in tasks}
    ordered_ids = [
        str(item)
        for item in ((plan.get("operator_handoff") or {}).get("ordered_task_ids") or [])
        if str(item) in by_id
    ]
    if not ordered_ids:
        ordered_ids = [str(item.get("check_id") or "") for item in tasks if item.get("check_id")]

    steps: list[str] = []
    for check_id in ordered_ids:
        task = by_id.get(check_id)
        if not task:
            continue
        evidence = str(task.get("evidence_needed") or task.get("message") or "").strip()
        if evidence:
            steps.append(f"resolve {check_id}: {evidence}")
        else:
            steps.append(f"resolve {check_id}")
    return steps


def _external_input_handoff(results: list[dict[str, Any]]) -> dict[str, Any]:
    external_inputs = next((result for result in results if result["id"] == "external_inputs"), None)
    if not external_inputs or not isinstance(external_inputs.get("json"), dict):
        return {
            "status": "unavailable",
            "ordered_task_ids": [],
            "next_action": {},
            "completion_criteria": [],
            "secret_values_written": False,
        }
    plan = external_inputs["json"]
    handoff = dict(plan.get("operator_handoff") or {})
    tasks = [task for task in plan.get("tasks") or [] if isinstance(task, dict)]
    task_by_id = {str(task.get("check_id") or ""): task for task in tasks}
    ordered_task_ids = [
        str(item)
        for item in (
            handoff.get("ordered_task_ids")
            or [task.get("check_id") for task in tasks]
        )
        if item
    ]
    next_action = dict(handoff.get("next_action") or {})
    if not next_action and ordered_task_ids:
        first_task = dict(task_by_id.get(ordered_task_ids[0]) or {})
        next_action = {
            "check_id": first_task.get("check_id", ""),
            "title": first_task.get("title", ""),
            "owner": first_task.get("owner", ""),
            "evidence_needed": first_task.get("evidence_needed") or first_task.get("message", ""),
        }
    return {
        "status": plan.get("status", "unknown"),
        "pending_count": plan.get("pending_count", 0),
        "ordered_task_ids": ordered_task_ids,
        "next_action": _public_next_action(next_action),
        "completion_criteria": [str(item) for item in handoff.get("completion_criteria") or []],
        "secret_values_written": bool(plan.get("secret_values_written") or handoff.get("secret_values_written")),
    }


def _public_next_action(next_action: dict[str, Any]) -> dict[str, Any]:
    if not next_action:
        return {}
    return {
        "check_id": str(next_action.get("check_id") or ""),
        "title": str(next_action.get("title") or ""),
        "owner": str(next_action.get("owner") or ""),
        "evidence_needed": str(next_action.get("evidence_needed") or ""),
        "safe_commands": [str(item) for item in next_action.get("safe_commands") or []],
        "verification_commands": [str(item) for item in next_action.get("verification_commands") or []],
    }


def _tail(text: str, limit: int = 4000) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[-limit:]


def _shell_join(args: Sequence[str]) -> str:
    return " ".join(_quote_arg(arg) for arg in args)


def _current_user_token_command(
    *,
    command_id: str,
    description: str,
    args: tuple[str, ...],
    env: dict[str, str],
) -> VerificationCommand:
    if os.getenv("CODEKB_USER_AUTH_TOKEN", "").strip():
        return VerificationCommand(id=command_id, description=description, args=args, required=False, env=env)
    return VerificationCommand(
        id=command_id,
        description=description,
        args=args,
        required=False,
        timeout_seconds=0,
        env=env,
        skip_reason="CODEKB_USER_AUTH_TOKEN is not set",
    )


def _quote_arg(arg: str) -> str:
    value = str(arg)
    if not value:
        return "''"
    if all(ch.isalnum() or ch in "/._:-=+" for ch in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"
