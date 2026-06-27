from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .aliases import load_aliases
from .candidate import JsonCandidateStore
from .current_user_smoke import (
    DEFAULT_CURRENT_USER_SMOKE_COMMENT,
    DEFAULT_CURRENT_USER_SMOKE_MESSAGE,
    DEFAULT_CURRENT_USER_SMOKE_QUERY,
    DEFAULT_CURRENT_USER_SMOKE_REASON,
    DEFAULT_CURRENT_USER_SMOKE_SUB_KBS,
    run_current_user_smoke,
)
from .diagnosis import DiagnosticResult, submit_diagnostic_gap
from .diagnosis_acceptance import (
    build_p5_acceptance_report,
    build_p5_external_input_plan,
    render_p5_external_input_plan_markdown,
)
from .diagnosis_context import build_diagnostic_query, parse_diagnostic_context
from .diagnosis_gaps import summarize_diagnostic_gaps
from .diagnosis_integrations import DEFAULT_API_BASE_URL, export_diagnose_integration_pack
from .diagnosis_readiness import build_p5_readiness_report
from .diagnosis_webhook import (
    DEFAULT_WEBHOOK_MAPPING_PATH,
    DEFAULT_WEBHOOK_SAMPLE_SUITE_PATH,
    JsonlDiagnosticWebhookStore,
    SUPPORTED_WEBHOOK_SOURCE_CHOICES,
    effective_diagnostic_webhook_mapping,
    import_diagnostic_webhook_sample,
    load_diagnostic_webhook_payload_file,
    preview_diagnostic_webhook,
    validate_diagnostic_webhook,
    validate_diagnostic_webhook_sample_suite,
)
from .evaluator import evaluate_golden_questions
from .feedback import JsonlFeedbackStore, summarize_feedback
from .governance import (
    build_curator_weekly_report,
    build_governance_report,
    build_governance_ticket_plans,
    governance_state_ticketed_item_ids,
    process_governance_ticket_outbox,
    summarize_governance_state,
    sync_governance_state,
    sync_governance_state_from_ticket_results,
    write_curator_weekly_report,
    write_governance_report,
    write_governance_ticket_outbox,
)
from .governance_artifacts import export_governance_artifacts
from .index_artifacts import export_index_artifacts
from .local_index import build_local_index, local_index_stats
from .mcp_server import DiagnoseMcpRuntime, run_stdio
from .p5_external_state import (
    DEFAULT_P5_ENV_FILE,
    DEFAULT_REAL_SAMPLES,
    DEFAULT_USER_TOKEN_STORE,
    DEFAULT_IM_TEMPLATE,
    build_p5_external_state,
    render_p5_external_state_text,
)
from .p5_final_verify import (
    render_p5_final_verification_text,
    run_p5_final_verification,
    write_p5_final_verification_report,
)
from .p5_security import build_p5_security_bootstrap, render_p5_security_env, write_p5_security_env_file
from .p3_usecase_smoke import run_p3_usecase_smoke
from .wiki_publish_client import HttpWikiPublishClient
from .publish import build_publish_plans, process_publish_outbox, write_publish_outbox
from .reconcile import reconcile_candidates
from .ticket_client import HttpGovernanceTicketClient
from .public_base_config import configure_public_base_env
from .quality import evaluate_quality
from .quality_gate import run_quality_gate
from .bench_latency import run_latency_benchmark
from .embedding_config import load_embedding_config
from .registry import load_registry
from .service import OfflineKbService
from .storage_integrations import build_storage_readiness
from .storage_sync import sync_postgres_upserts_from_env, sync_qdrant_points_from_env
from .sync import sync_source_path
from .user_auth import JsonUserTokenStore, public_token_metadata
from .user_confirmation import (
    JsonlUserConfirmationResponseStore,
    get_user_confirmation_detail,
    list_user_confirmations,
    public_confirmation_response,
)
from .user_confirmation_delivery import process_user_confirmation_outbox
from .im_config import configure_im_env, write_im_config_template
from .im_smoke import (
    DEFAULT_IM_SMOKE_MESSAGE,
    DEFAULT_IM_SMOKE_REASON,
    run_im_delivery_smoke,
)
from .im_oauth_smoke import DEFAULT_IM_OAUTH_NEXT_URL, run_im_oauth_smoke
from .webhook_sample_activation import activate_diagnostic_webhook_samples


def _enforce_quality_gate(args) -> int | None:
    """动作执行前的可选质量闸门。返回 None 表示放行,闸门被要求且未通过时返回非零退出码。
    没有 ``--require-quality-gate`` 时什么都不做(返回 None),保持默认行为。"""
    if not getattr(args, "require_quality_gate", False):
        return None
    result = run_quality_gate(
        fixture_path=getattr(args, "fixtures", "data/fixtures/sample_corpus.jsonl"),
        questions_path="data/fixtures/golden_questions.md",
        aliases_path="data/entity_aliases.yaml",
        include_prefixes={"REL", "TST", "INC"},
        skip_missing_expected=True,
    )
    report = result.report
    print(
        f"quality_gate={'PASS' if result.passed else 'FAIL'} "
        f"hit@4={report.hit_rate:.3f} citation_rate={report.citation_rate:.3f} "
        f"faithfulness={report.faithfulness:.3f} refusal_rate={report.refusal_rate:.3f} "
        f"answer_correctness={report.answer_correctness:.3f}"
    )
    for reason in result.reasons:
        print(f"quality_gate_reason: {reason}")
    if not result.passed:
        print("quality_gate_blocked: refusing action because the quality gate failed")
        return 1
    return None


def main(
    argv: list[str] | None = None,
    *,
    wiki_publish_client_factory=None,
    governance_ticket_client_factory=None,
) -> int:
    parser = argparse.ArgumentParser(prog="codekb")
    subparsers = parser.add_subparsers(dest="command", required=True)

    eval_parser = subparsers.add_parser("eval", help="Run offline golden hit@k evaluation")
    eval_parser.add_argument("--fixtures", default="data/fixtures/sample_corpus.jsonl")
    eval_parser.add_argument("--questions", default="data/fixtures/golden_questions.md")
    eval_parser.add_argument("--top-k", type=int, default=4)
    eval_parser.add_argument("--prefix", action="append", help="Question prefix to include, e.g. REL")
    eval_parser.add_argument("--aliases", default="data/entity_aliases.yaml")
    eval_parser.add_argument(
        "--skip-missing-expected",
        action="store_true",
        help="Skip questions whose expected docids are not loaded by the fixture source",
    )

    ask_parser = subparsers.add_parser("ask", help="Ask against offline fixture KB")
    ask_parser.add_argument("query")
    ask_parser.add_argument("--fixtures", default="data/fixtures/sample_corpus.jsonl")
    ask_parser.add_argument("--aliases", default="data/entity_aliases.yaml")
    ask_parser.add_argument("--sub-kb", action="append", help="Limit to sub KB, e.g. testing")
    ask_parser.add_argument("--top-k", type=int, default=4)
    ask_parser.add_argument("--trace-log", help="Optional JSONL trace log path")
    ask_parser.add_argument(
        "--retriever",
        default="bm25-lite",
        choices=["bm25-lite", "hybrid-lite", "qdrant-lite", "qdrant-hybrid-lite"],
    )
    ask_parser.add_argument("--index-db", help="Optional SQLite local index path")
    ask_parser.add_argument(
        "--answer-mode",
        default=None,
        choices=["extractive", "generative"],
        help="Answer generation mode; defaults to CODEKB_ANSWER_MODE (else extractive). generative degrades to extractive when no LLM client is available",
    )

    diagnose_parser = subparsers.add_parser("diagnose", help="Run P5 diagnosis v0 against the KB")
    diagnose_parser.add_argument("query", nargs="?", default="")
    diagnose_parser.add_argument("--fixtures", default="data/fixtures/sample_corpus.jsonl")
    diagnose_parser.add_argument("--aliases", default="data/entity_aliases.yaml")
    diagnose_parser.add_argument("--sub-kb", action="append", help="Limit to sub KB, e.g. release")
    diagnose_parser.add_argument("--top-k", type=int, default=4)
    diagnose_parser.add_argument("--trace-log", help="Optional JSONL trace log path")
    diagnose_parser.add_argument(
        "--retriever",
        default="bm25-lite",
        choices=["bm25-lite", "hybrid-lite", "qdrant-lite", "qdrant-hybrid-lite"],
    )
    diagnose_parser.add_argument("--index-db", help="Optional SQLite local index path")
    diagnose_parser.add_argument(
        "--answer-mode",
        default=None,
        choices=["extractive", "generative"],
        help="Answer generation mode; defaults to CODEKB_ANSWER_MODE (else extractive). generative degrades to extractive when no LLM client is available",
    )
    diagnose_parser.add_argument("--registry", default="docs/kb-registry.draft.yaml")
    diagnose_parser.add_argument("--policy", default=_default_governance_policy())
    diagnose_parser.add_argument("--feedback-log", default=_default_feedback_log())
    diagnose_parser.add_argument("--candidate-store", default=_default_candidate_store())
    diagnose_parser.add_argument("--pending-docs-dir", default=_default_pending_docs_dir())
    diagnose_parser.add_argument("--stale-after-days", type=int, default=180)
    diagnose_parser.add_argument("--candidate-sla-days", type=int, default=3)
    diagnose_parser.add_argument("--min-confidence", type=float, default=0.35)
    diagnose_parser.add_argument("--no-governance", action="store_true")
    diagnose_parser.add_argument("--submit-gap", action="store_true")
    diagnose_parser.add_argument("--submitted-by-hash", default="")
    diagnose_parser.add_argument("--allow-duplicate", action="store_true")
    diagnose_parser.add_argument("--context-json", default="", help="Diagnostic context as a JSON object")
    diagnose_parser.add_argument("--surface", default="", help="Calling surface, e.g. code_review or ci")
    diagnose_parser.add_argument("--repo", default="", help="Repository path or name")
    diagnose_parser.add_argument("--branch", default="", help="Branch name")
    diagnose_parser.add_argument("--commit", default="", help="Commit SHA")
    diagnose_parser.add_argument("--mr-id", default="", help="Merge request id")
    diagnose_parser.add_argument("--build-id", default="", help="Build or pipeline id")
    diagnose_parser.add_argument("--job-name", default="", help="Build job name")
    diagnose_parser.add_argument("--error-code", default="", help="Machine readable error code")
    diagnose_parser.add_argument("--error-text", default="", help="Error text to diagnose")
    diagnose_parser.add_argument("--log-file", default="", help="File containing a diagnostic log excerpt")
    diagnose_parser.add_argument("--tag", action="append", default=[], help="Diagnostic context tag")
    diagnose_parser.add_argument("--json", action="store_true")

    diagnose_gap_summary_parser = subparsers.add_parser(
        "diagnose-gap-summary",
        help="Summarize P5 diagnostic gap candidates",
    )
    diagnose_gap_summary_parser.add_argument("--store", default=_default_candidate_store())
    diagnose_gap_summary_parser.add_argument("--pending-docs-dir", default=_default_pending_docs_dir())
    diagnose_gap_summary_parser.add_argument("--status", default="")
    diagnose_gap_summary_parser.add_argument("--limit", type=int, default=20)
    diagnose_gap_summary_parser.add_argument("--json", action="store_true")

    diagnose_webhook_events_parser = subparsers.add_parser(
        "diagnose-webhook-events",
        help="Summarize P5 diagnostic webhook events",
    )
    diagnose_webhook_events_parser.add_argument("--log", default=_default_diagnose_webhook_log())
    diagnose_webhook_events_parser.add_argument("--source", default="")
    diagnose_webhook_events_parser.add_argument("--status", default="")
    diagnose_webhook_events_parser.add_argument("--action", default="")
    diagnose_webhook_events_parser.add_argument("--limit", type=int, default=20)
    diagnose_webhook_events_parser.add_argument("--json", action="store_true")

    diagnose_webhook_normalize_parser = subparsers.add_parser(
        "diagnose-webhook-normalize",
        help="Normalize a P5 diagnostic webhook payload without running diagnosis",
    )
    diagnose_webhook_normalize_parser.add_argument("source", choices=list(SUPPORTED_WEBHOOK_SOURCE_CHOICES))
    diagnose_webhook_normalize_parser.add_argument("--payload-json", default="")
    diagnose_webhook_normalize_parser.add_argument("--payload-file", default="")
    diagnose_webhook_normalize_parser.add_argument("--mapping", default=_default_diagnose_webhook_mapping())
    diagnose_webhook_normalize_parser.add_argument("--json", action="store_true")

    diagnose_webhook_validate_parser = subparsers.add_parser(
        "diagnose-webhook-validate",
        help="Validate a P5 diagnostic webhook payload without running diagnosis",
    )
    diagnose_webhook_validate_parser.add_argument("source", choices=list(SUPPORTED_WEBHOOK_SOURCE_CHOICES))
    diagnose_webhook_validate_parser.add_argument("--payload-json", default="")
    diagnose_webhook_validate_parser.add_argument("--payload-file", default="")
    diagnose_webhook_validate_parser.add_argument("--mapping", default=_default_diagnose_webhook_mapping())
    diagnose_webhook_validate_parser.add_argument("--json", action="store_true")

    diagnose_webhook_mapping_parser = subparsers.add_parser(
        "diagnose-webhook-mapping",
        help="Show effective P5 diagnostic webhook field mapping",
    )
    diagnose_webhook_mapping_parser.add_argument("source", choices=list(SUPPORTED_WEBHOOK_SOURCE_CHOICES))
    diagnose_webhook_mapping_parser.add_argument("--mapping", default=_default_diagnose_webhook_mapping())
    diagnose_webhook_mapping_parser.add_argument("--json", action="store_true")

    diagnose_webhook_sample_suite_parser = subparsers.add_parser(
        "diagnose-webhook-sample-suite",
        help="Validate P5 diagnostic webhook sample payloads against the active mapping",
    )
    diagnose_webhook_sample_suite_parser.add_argument("--samples", default=_default_diagnose_webhook_samples())
    diagnose_webhook_sample_suite_parser.add_argument("--mapping", default=_default_diagnose_webhook_mapping())
    diagnose_webhook_sample_suite_parser.add_argument("--json", action="store_true")

    diagnose_webhook_sample_import_parser = subparsers.add_parser(
        "diagnose-webhook-sample-import",
        help="Import and sanitize a real P5 diagnostic webhook payload into a sample suite",
    )
    diagnose_webhook_sample_import_parser.add_argument("source", choices=list(SUPPORTED_WEBHOOK_SOURCE_CHOICES))
    diagnose_webhook_sample_import_parser.add_argument("--name", required=True)
    diagnose_webhook_sample_import_parser.add_argument("--payload-file", required=True)
    diagnose_webhook_sample_import_parser.add_argument("--output", required=True)
    diagnose_webhook_sample_import_parser.add_argument("--mapping", default=_default_diagnose_webhook_mapping())
    diagnose_webhook_sample_import_parser.add_argument("--append", action="store_true")
    diagnose_webhook_sample_import_parser.add_argument(
        "--expected-context",
        action="append",
        default=[],
        help="Expected context field as key=value, repeatable",
    )
    diagnose_webhook_sample_import_parser.add_argument("--expected-sub-kb", action="append", default=[])
    diagnose_webhook_sample_import_parser.add_argument(
        "--forbidden-value",
        action="append",
        default=[],
        help="Optional non-secret sentinel value that must not appear after validation",
    )
    diagnose_webhook_sample_import_parser.add_argument("--json", action="store_true")

    diagnose_webhook_sample_activate_parser = subparsers.add_parser(
        "diagnose-webhook-sample-activate",
        help="Validate and activate a real P5 diagnostic webhook sample suite in the server env file",
    )
    diagnose_webhook_sample_activate_parser.add_argument("--env-file", default=os.getenv("CODEKB_ENV_FILE", ""))
    diagnose_webhook_sample_activate_parser.add_argument("--samples", default="")
    diagnose_webhook_sample_activate_parser.add_argument("--mapping", default="")
    diagnose_webhook_sample_activate_parser.add_argument("--apply", action="store_true")
    diagnose_webhook_sample_activate_parser.add_argument("--confirm-real-samples", action="store_true")
    diagnose_webhook_sample_activate_parser.add_argument("--json", action="store_true")

    diagnose_integration_export_parser = subparsers.add_parser(
        "diagnose-integration-export",
        help="Export P5 diagnose integration artifacts for MCP, code review, MR, and IM",
    )
    diagnose_integration_export_parser.add_argument("--output-dir", required=True)
    diagnose_integration_export_parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    diagnose_integration_export_parser.add_argument("--json", action="store_true")

    diagnose_readiness_parser = subparsers.add_parser(
        "diagnose-readiness",
        help="Check P5 diagnose integration readiness",
    )
    diagnose_readiness_parser.add_argument("--env-file", default=os.getenv("CODEKB_ENV_FILE", ""))
    diagnose_readiness_parser.add_argument("--fixtures")
    diagnose_readiness_parser.add_argument("--aliases")
    diagnose_readiness_parser.add_argument("--registry")
    diagnose_readiness_parser.add_argument("--policy")
    diagnose_readiness_parser.add_argument("--index-db")
    diagnose_readiness_parser.add_argument("--webhook-log")
    diagnose_readiness_parser.add_argument("--mapping")
    diagnose_readiness_parser.add_argument("--samples")
    diagnose_readiness_parser.add_argument("--token-store")
    diagnose_readiness_parser.add_argument("--confirmation-outbox")
    diagnose_readiness_parser.add_argument("--confirmation-responses")
    diagnose_readiness_parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    diagnose_readiness_parser.add_argument("--json", action="store_true")

    diagnose_acceptance_parser = subparsers.add_parser(
        "diagnose-acceptance",
        help="Run the final P5 diagnose acceptance gate",
    )
    diagnose_acceptance_parser.add_argument("--env-file", default=os.getenv("CODEKB_ENV_FILE", ""))
    diagnose_acceptance_parser.add_argument("--fixtures")
    diagnose_acceptance_parser.add_argument("--aliases")
    diagnose_acceptance_parser.add_argument("--registry")
    diagnose_acceptance_parser.add_argument("--policy")
    diagnose_acceptance_parser.add_argument("--index-db")
    diagnose_acceptance_parser.add_argument("--webhook-log")
    diagnose_acceptance_parser.add_argument("--mapping")
    diagnose_acceptance_parser.add_argument("--samples")
    diagnose_acceptance_parser.add_argument("--token-store")
    diagnose_acceptance_parser.add_argument("--confirmation-outbox")
    diagnose_acceptance_parser.add_argument("--confirmation-responses")
    diagnose_acceptance_parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    diagnose_acceptance_parser.add_argument("--json", action="store_true")

    diagnose_external_inputs_parser = subparsers.add_parser(
        "diagnose-external-inputs",
        help="Render the remaining P5 external-input tasks without exposing secrets",
    )
    diagnose_external_inputs_parser.add_argument("--env-file", default=os.getenv("CODEKB_ENV_FILE", ""))
    diagnose_external_inputs_parser.add_argument("--fixtures")
    diagnose_external_inputs_parser.add_argument("--aliases")
    diagnose_external_inputs_parser.add_argument("--registry")
    diagnose_external_inputs_parser.add_argument("--policy")
    diagnose_external_inputs_parser.add_argument("--index-db")
    diagnose_external_inputs_parser.add_argument("--webhook-log")
    diagnose_external_inputs_parser.add_argument("--mapping")
    diagnose_external_inputs_parser.add_argument("--samples")
    diagnose_external_inputs_parser.add_argument("--token-store")
    diagnose_external_inputs_parser.add_argument("--confirmation-outbox")
    diagnose_external_inputs_parser.add_argument("--confirmation-responses")
    diagnose_external_inputs_parser.add_argument(
        "--im-template",
        default=os.getenv("CODEKB_P5_IM_TEMPLATE", DEFAULT_IM_TEMPLATE),
    )
    diagnose_external_inputs_parser.add_argument(
        "--real-samples",
        default=os.getenv("CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES", DEFAULT_REAL_SAMPLES),
    )
    diagnose_external_inputs_parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    diagnose_external_inputs_parser.add_argument("--json", action="store_true")

    diagnose_p5_handoff_parser = subparsers.add_parser(
        "diagnose-p5-handoff-bundle",
        help="Write a safe P5 external handoff bundle with plans, templates, and integration artifacts",
    )
    diagnose_p5_handoff_parser.add_argument("--output-dir", required=True)
    diagnose_p5_handoff_parser.add_argument("--env-file", default=os.getenv("CODEKB_ENV_FILE", ""))
    diagnose_p5_handoff_parser.add_argument("--fixtures")
    diagnose_p5_handoff_parser.add_argument("--aliases")
    diagnose_p5_handoff_parser.add_argument("--registry")
    diagnose_p5_handoff_parser.add_argument("--policy")
    diagnose_p5_handoff_parser.add_argument("--index-db")
    diagnose_p5_handoff_parser.add_argument("--webhook-log")
    diagnose_p5_handoff_parser.add_argument("--mapping")
    diagnose_p5_handoff_parser.add_argument("--samples")
    diagnose_p5_handoff_parser.add_argument("--token-store")
    diagnose_p5_handoff_parser.add_argument("--confirmation-outbox")
    diagnose_p5_handoff_parser.add_argument("--confirmation-responses")
    diagnose_p5_handoff_parser.add_argument(
        "--im-template",
        default=os.getenv("CODEKB_P5_IM_TEMPLATE", DEFAULT_IM_TEMPLATE),
    )
    diagnose_p5_handoff_parser.add_argument(
        "--real-samples",
        default=os.getenv("CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES", DEFAULT_REAL_SAMPLES),
    )
    diagnose_p5_handoff_parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    diagnose_p5_handoff_parser.add_argument("--force", action="store_true")
    diagnose_p5_handoff_parser.add_argument("--json", action="store_true")

    diagnose_p5_final_verify_parser = subparsers.add_parser(
        "diagnose-p5-final-verify",
        help="Run P5 final verification commands and summarize the acceptance evidence",
    )
    diagnose_p5_final_verify_parser.add_argument("--env-file", default=os.getenv("CODEKB_ENV_FILE", ""))
    diagnose_p5_final_verify_parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    diagnose_p5_final_verify_parser.add_argument("--python", default="python3")
    diagnose_p5_final_verify_parser.add_argument(
        "--confirmation-worker",
        default="/data/codekb/current/deploy/codekb-confirmation-worker",
    )
    diagnose_p5_final_verify_parser.add_argument("--skip-slow", action="store_true")
    diagnose_p5_final_verify_parser.add_argument("--skip-http", action="store_true")
    diagnose_p5_final_verify_parser.add_argument("--skip-worker", action="store_true")
    diagnose_p5_final_verify_parser.add_argument("--output", default="")
    diagnose_p5_final_verify_parser.add_argument("--json", action="store_true")

    diagnose_p5_external_state_parser = subparsers.add_parser(
        "diagnose-p5-external-state",
        help="Inspect P5 external input evidence without exposing secret values",
    )
    diagnose_p5_external_state_parser.add_argument("--env-file", default=os.getenv("CODEKB_ENV_FILE", ""))
    diagnose_p5_external_state_parser.add_argument("--im-template", default=DEFAULT_IM_TEMPLATE)
    diagnose_p5_external_state_parser.add_argument("--token-store", default=DEFAULT_USER_TOKEN_STORE)
    diagnose_p5_external_state_parser.add_argument("--real-samples", default=DEFAULT_REAL_SAMPLES)
    diagnose_p5_external_state_parser.add_argument("--json", action="store_true")

    diagnose_security_bootstrap_parser = subparsers.add_parser(
        "diagnose-security-bootstrap",
        help="Generate P5 webhook/OAuth/admin secret environment values",
    )
    diagnose_security_bootstrap_parser.add_argument("--no-admin-token", action="store_true")
    diagnose_security_bootstrap_parser.add_argument("--include-static-mcp-token", action="store_true")
    diagnose_security_bootstrap_parser.add_argument("--token-bytes", type=int, default=32)
    diagnose_security_bootstrap_parser.add_argument("--output", default="")
    diagnose_security_bootstrap_parser.add_argument("--force", action="store_true")
    diagnose_security_bootstrap_parser.add_argument("--json", action="store_true")

    diagnose_mcp_server_parser = subparsers.add_parser(
        "diagnose-mcp-server",
        help="Run the P5 diagnose MCP stdio server",
    )
    diagnose_mcp_server_parser.add_argument("--fixtures", default="data/fixtures/sample_corpus.jsonl")
    diagnose_mcp_server_parser.add_argument("--aliases", default="data/entity_aliases.yaml")
    diagnose_mcp_server_parser.add_argument("--registry", default="docs/kb-registry.draft.yaml")
    diagnose_mcp_server_parser.add_argument("--policy", default=_default_governance_policy())
    diagnose_mcp_server_parser.add_argument("--feedback-log", default=_default_feedback_log())
    diagnose_mcp_server_parser.add_argument("--candidate-store", default=_default_candidate_store())
    diagnose_mcp_server_parser.add_argument("--pending-docs-dir", default=_default_pending_docs_dir())
    diagnose_mcp_server_parser.add_argument("--mapping", default=_default_diagnose_webhook_mapping())
    diagnose_mcp_server_parser.add_argument("--mcp-token", default=_default_mcp_token())
    diagnose_mcp_server_parser.add_argument("--token-store", default=_default_user_token_store())
    diagnose_mcp_server_parser.add_argument("--allow-static-mcp-token", action="store_true")
    diagnose_mcp_server_parser.add_argument("--confirmation-outbox", default=_default_user_confirmation_outbox())
    diagnose_mcp_server_parser.add_argument("--trace-log", default="")
    diagnose_mcp_server_parser.add_argument(
        "--retriever",
        default="bm25-lite",
        choices=["bm25-lite", "hybrid-lite", "qdrant-lite", "qdrant-hybrid-lite"],
    )
    diagnose_mcp_server_parser.add_argument("--index-db", default="")
    diagnose_mcp_server_parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)

    current_user_smoke_parser = subparsers.add_parser(
        "diagnose-current-user-smoke",
        help="Run current-user MCP auth, diagnosis, confirmation, and response smoke checks",
    )
    current_user_smoke_parser.add_argument("--auth-token", default=os.getenv("CODEKB_USER_AUTH_TOKEN", ""))
    current_user_smoke_parser.add_argument("--token-store", default=_default_user_token_store())
    current_user_smoke_parser.add_argument("--confirmation-outbox", default=_default_user_confirmation_outbox())
    current_user_smoke_parser.add_argument("--confirmation-responses", default=_default_user_confirmation_responses())
    current_user_smoke_parser.add_argument("--delivery-report", default=_default_user_confirmation_report())
    current_user_smoke_parser.add_argument("--delivery-log", default=_default_user_confirmation_delivery_log())
    current_user_smoke_parser.add_argument("--fixtures", default="data/fixtures/sample_corpus.jsonl")
    current_user_smoke_parser.add_argument("--aliases", default="data/entity_aliases.yaml")
    current_user_smoke_parser.add_argument("--registry", default="docs/kb-registry.draft.yaml")
    current_user_smoke_parser.add_argument("--policy", default=_default_governance_policy())
    current_user_smoke_parser.add_argument("--feedback-log", default=_default_feedback_log())
    current_user_smoke_parser.add_argument("--candidate-store", default=_default_candidate_store())
    current_user_smoke_parser.add_argument("--pending-docs-dir", default=_default_pending_docs_dir())
    current_user_smoke_parser.add_argument("--query", default=DEFAULT_CURRENT_USER_SMOKE_QUERY)
    current_user_smoke_parser.add_argument("--sub-kb", action="append", default=[])
    current_user_smoke_parser.add_argument(
        "--reason",
        default=DEFAULT_CURRENT_USER_SMOKE_REASON,
        choices=["interaction_complete", "problem_solved", "human_review_required", "gap_candidate_review"],
    )
    current_user_smoke_parser.add_argument("--message", default=DEFAULT_CURRENT_USER_SMOKE_MESSAGE)
    current_user_smoke_parser.add_argument("--respond", action="store_true")
    current_user_smoke_parser.add_argument(
        "--decision",
        default="confirmed",
        choices=["confirmed", "rejected", "needs_followup"],
    )
    current_user_smoke_parser.add_argument("--comment", default=DEFAULT_CURRENT_USER_SMOKE_COMMENT)
    current_user_smoke_parser.add_argument("--trace-log", default="")
    current_user_smoke_parser.add_argument(
        "--retriever",
        default="bm25-lite",
        choices=["bm25-lite", "hybrid-lite", "qdrant-lite", "qdrant-hybrid-lite"],
    )
    current_user_smoke_parser.add_argument("--index-db", default="")
    current_user_smoke_parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    current_user_smoke_parser.add_argument("--top-k", type=int, default=4)
    current_user_smoke_parser.add_argument("--include-governance", action="store_true")
    current_user_smoke_parser.add_argument("--json", action="store_true")

    im_smoke_parser = subparsers.add_parser(
        "diagnose-im-smoke",
        help="Verify IM app credentials and current-user confirmation delivery",
    )
    im_smoke_parser.add_argument("--env-file", default=os.getenv("CODEKB_ENV_FILE", ""))
    im_smoke_parser.add_argument("--auth-token", default=os.getenv("CODEKB_USER_AUTH_TOKEN", ""))
    im_smoke_parser.add_argument("--token-store", default="")
    im_smoke_parser.add_argument("--confirmation-outbox", default="")
    im_smoke_parser.add_argument("--delivery-report", default="")
    im_smoke_parser.add_argument("--delivery-log", default="")
    im_smoke_parser.add_argument(
        "--reason",
        default=DEFAULT_IM_SMOKE_REASON,
        choices=["interaction_complete", "problem_solved", "human_review_required", "gap_candidate_review"],
    )
    im_smoke_parser.add_argument("--message", default=DEFAULT_IM_SMOKE_MESSAGE)
    im_smoke_parser.add_argument("--skip-credential-check", action="store_true")
    im_smoke_parser.add_argument("--execute", action="store_true")
    im_smoke_parser.add_argument("--json", action="store_true")

    im_oauth_smoke_parser = subparsers.add_parser(
        "diagnose-im-oauth-smoke",
        help="Verify IM OAuth setup URL, state signing, and current-user token store status",
    )
    im_oauth_smoke_parser.add_argument("--env-file", default=os.getenv("CODEKB_ENV_FILE", ""))
    im_oauth_smoke_parser.add_argument("--token-store", default="")
    im_oauth_smoke_parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    im_oauth_smoke_parser.add_argument("--redirect-uri", default="")
    im_oauth_smoke_parser.add_argument("--next-url", default=DEFAULT_IM_OAUTH_NEXT_URL)
    im_oauth_smoke_parser.add_argument("--check-credentials", action="store_true")
    im_oauth_smoke_parser.add_argument("--json", action="store_true")

    im_config_parser = subparsers.add_parser(
        "diagnose-im-configure",
        help="Safely plan or write IM OAuth/delivery env values without printing secrets",
    )
    im_config_parser.add_argument("--env-file", default=os.getenv("CODEKB_ENV_FILE", ""))
    im_config_parser.add_argument("--corp-id", default="")
    im_config_parser.add_argument("--agent-id", default="")
    im_config_parser.add_argument("--app-secret", default="")
    im_config_parser.add_argument("--oauth-state-secret", default="")
    im_config_parser.add_argument("--redirect-uri", default="")
    im_config_parser.add_argument("--confirm-url-base", default="")
    im_config_parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    im_config_parser.add_argument("--template-output", default="")
    im_config_parser.add_argument("--from-template", default="")
    im_config_parser.add_argument("--force", action="store_true")
    im_config_parser.add_argument("--enable-send", action="store_true")
    im_config_parser.add_argument("--confirm-real-send", action="store_true")
    im_config_parser.add_argument("--apply", action="store_true")
    im_config_parser.add_argument("--json", action="store_true")

    public_base_config_parser = subparsers.add_parser(
        "diagnose-public-base-configure",
        help="Safely plan or write public API base URL env values without touching secrets",
    )
    public_base_config_parser.add_argument("--env-file", default=os.getenv("CODEKB_ENV_FILE", ""))
    public_base_config_parser.add_argument("--api-base-url", required=True)
    public_base_config_parser.add_argument("--apply", action="store_true")
    public_base_config_parser.add_argument("--json", action="store_true")

    auth_token_bind_parser = subparsers.add_parser(
        "auth-token-bind",
        help="Issue a current-user token after IM/web authorization",
    )
    auth_token_bind_parser.add_argument("--store", default=_default_user_token_store())
    auth_token_bind_parser.add_argument("--user-id-hash", required=True)
    auth_token_bind_parser.add_argument("--display-name", default="")
    auth_token_bind_parser.add_argument("--scope", action="append", default=[])
    auth_token_bind_parser.add_argument("--ttl-days", type=int, default=30)
    auth_token_bind_parser.add_argument("--metadata-json", default="{}")
    auth_token_bind_parser.add_argument("--json", action="store_true")

    auth_token_list_parser = subparsers.add_parser(
        "auth-token-list",
        help="List current-user token bindings without exposing raw tokens",
    )
    auth_token_list_parser.add_argument("--store", default=_default_user_token_store())
    auth_token_list_parser.add_argument("--json", action="store_true")

    auth_token_revoke_parser = subparsers.add_parser(
        "auth-token-revoke",
        help="Revoke a current-user token binding",
    )
    auth_token_revoke_parser.add_argument("--store", default=_default_user_token_store())
    auth_token_revoke_parser.add_argument("--token-id", required=True)
    auth_token_revoke_parser.add_argument("--json", action="store_true")

    user_confirmation_outbox_parser = subparsers.add_parser(
        "user-confirmation-outbox",
        help="Validate or execute current-user IM confirmation outbox",
    )
    user_confirmation_outbox_parser.add_argument("--outbox", default=_default_user_confirmation_outbox())
    user_confirmation_outbox_parser.add_argument("--token-store", default=_default_user_token_store())
    user_confirmation_outbox_parser.add_argument("--report", default=_default_user_confirmation_report())
    user_confirmation_outbox_parser.add_argument("--delivery-log", default=_default_user_confirmation_delivery_log())
    user_confirmation_outbox_parser.add_argument("--max-retries", type=int, default=0)
    user_confirmation_outbox_parser.add_argument("--backoff-base-seconds", type=float, default=0.0)
    user_confirmation_outbox_parser.add_argument("--dead-letter-path", default="")
    user_confirmation_outbox_parser.add_argument("--limit", type=int, default=50)
    user_confirmation_outbox_parser.add_argument("--confirmation-id", default="")
    user_confirmation_outbox_parser.add_argument("--execute", action="store_true")
    user_confirmation_outbox_parser.add_argument("--json", action="store_true")

    user_confirmation_pending_parser = subparsers.add_parser(
        "user-confirmation-pending",
        help="List pending confirmations for the current authenticated user",
    )
    user_confirmation_pending_parser.add_argument("--outbox", default=_default_user_confirmation_outbox())
    user_confirmation_pending_parser.add_argument("--responses", default=_default_user_confirmation_responses())
    user_confirmation_pending_parser.add_argument("--token-store", default=_default_user_token_store())
    user_confirmation_pending_parser.add_argument("--auth-token", default=os.getenv("CODEKB_USER_AUTH_TOKEN", ""))
    user_confirmation_pending_parser.add_argument("--limit", type=int, default=50)
    user_confirmation_pending_parser.add_argument("--include-responded", action="store_true")
    user_confirmation_pending_parser.add_argument("--json", action="store_true")

    user_confirmation_detail_parser = subparsers.add_parser(
        "user-confirmation-detail",
        help="Show one confirmation for the current authenticated user",
    )
    user_confirmation_detail_parser.add_argument("--outbox", default=_default_user_confirmation_outbox())
    user_confirmation_detail_parser.add_argument("--responses", default=_default_user_confirmation_responses())
    user_confirmation_detail_parser.add_argument("--token-store", default=_default_user_token_store())
    user_confirmation_detail_parser.add_argument("--auth-token", default=os.getenv("CODEKB_USER_AUTH_TOKEN", ""))
    user_confirmation_detail_parser.add_argument("--confirmation-id", required=True)
    user_confirmation_detail_parser.add_argument("--json", action="store_true")

    user_confirmation_respond_parser = subparsers.add_parser(
        "user-confirmation-respond",
        help="Record the current user's response for a confirmation request",
    )
    user_confirmation_respond_parser.add_argument("--outbox", default=_default_user_confirmation_outbox())
    user_confirmation_respond_parser.add_argument("--responses", default=_default_user_confirmation_responses())
    user_confirmation_respond_parser.add_argument("--token-store", default=_default_user_token_store())
    user_confirmation_respond_parser.add_argument("--confirmation-id", required=True)
    user_confirmation_respond_parser.add_argument("--auth-token", default=os.getenv("CODEKB_USER_AUTH_TOKEN", ""))
    user_confirmation_respond_parser.add_argument(
        "--decision",
        required=True,
        choices=["confirmed", "rejected", "needs_followup"],
    )
    user_confirmation_respond_parser.add_argument("--comment", default="")
    user_confirmation_respond_parser.add_argument("--metadata-json", default="{}")
    user_confirmation_respond_parser.add_argument("--json", action="store_true")

    user_confirmation_responses_parser = subparsers.add_parser(
        "user-confirmation-responses",
        help="Summarize current-user confirmation responses",
    )
    user_confirmation_responses_parser.add_argument("--responses", default=_default_user_confirmation_responses())
    user_confirmation_responses_parser.add_argument("--limit", type=int, default=50)
    user_confirmation_responses_parser.add_argument("--json", action="store_true")

    sync_parser = subparsers.add_parser("sync", help="Sync source documents into local atom state")
    sync_parser.add_argument("--fixtures", default="data/fixtures/sample_corpus.jsonl")
    sync_parser.add_argument("--state-file", help="Optional incremental sync state JSON path")
    sync_parser.add_argument("--report-file", help="Optional sync report JSON path")
    sync_parser.add_argument("--force", action="store_true", help="Re-index every loaded document")
    sync_parser.add_argument("--fail-on-error", action="store_true", help="Return non-zero when any document fails")

    check_parser = subparsers.add_parser("p1-check", help="Run P1 sync and golden evaluation gate")
    check_parser.add_argument("--fixtures", default="data/fixtures/sample_corpus.jsonl")
    check_parser.add_argument("--questions", default="data/fixtures/golden_questions.md")
    check_parser.add_argument("--aliases", default="data/entity_aliases.yaml")
    check_parser.add_argument("--state-file", help="Optional incremental sync state JSON path")
    check_parser.add_argument("--report-file", help="Optional sync report JSON path")
    check_parser.add_argument("--top-k", type=int, default=4)
    check_parser.add_argument("--min-hit-rate", type=float, default=0.75)
    check_parser.add_argument("--prefix", action="append", help="Question prefix to include, e.g. REL")
    check_parser.add_argument("--skip-missing-expected", action="store_true")

    export_parser = subparsers.add_parser("export-index", help="Export P1 local index artifacts")
    export_parser.add_argument("--fixtures", default="data/fixtures/sample_corpus.jsonl")
    export_parser.add_argument(
        "--include-source",
        action="append",
        default=[],
        help="Additional source bundle path, e.g. /data/codekb/pending-docs",
    )
    export_parser.add_argument("--output-dir", required=True)
    export_parser.add_argument(
        "--require-quality-gate",
        action="store_true",
        help="Refuse to export unless the P2 answer-quality gate passes",
    )

    build_index_parser = subparsers.add_parser("build-index", help="Build a persistent SQLite local index")
    build_index_parser.add_argument("--fixtures", default="data/fixtures/sample_corpus.jsonl")
    build_index_parser.add_argument(
        "--include-source",
        action="append",
        default=[],
        help="Additional source bundle path, e.g. /data/codekb/pending-docs",
    )
    build_index_parser.add_argument("--db-path", required=True)
    build_index_parser.add_argument(
        "--require-quality-gate",
        action="store_true",
        help="Refuse to rebuild unless the P2 answer-quality gate passes",
    )

    p3_usecase_smoke_parser = subparsers.add_parser(
        "p3-usecase-smoke",
        help="Run an isolated P3 ingest -> audit -> ask -> publish outbox usecase smoke",
    )
    p3_usecase_smoke_parser.add_argument("--work-dir", default="")
    p3_usecase_smoke_parser.add_argument("--fixtures", default="data/fixtures/sample_corpus.jsonl")
    p3_usecase_smoke_parser.add_argument("--aliases", default="data/entity_aliases.yaml")
    p3_usecase_smoke_parser.add_argument(
        "--publish-mode",
        default="manual",
        choices=["manual", "index_page", "template_copy"],
    )
    p3_usecase_smoke_parser.add_argument("--index-docid", default="")
    p3_usecase_smoke_parser.add_argument("--template-docid", default="")
    p3_usecase_smoke_parser.add_argument("--target-parentid", default="")
    p3_usecase_smoke_parser.add_argument("--json", action="store_true")

    index_stats_parser = subparsers.add_parser("index-stats", help="Show SQLite local index stats")
    index_stats_parser.add_argument("--db-path", required=True)

    storage_readiness_parser = subparsers.add_parser(
        "storage-readiness",
        help="Check external Postgres and Qdrant storage readiness",
    )
    storage_readiness_parser.add_argument("--env-file", default="", help="Optional env file with storage credentials")
    storage_readiness_parser.add_argument("--timeout-seconds", type=int, default=3)
    storage_readiness_parser.add_argument("--json", action="store_true")

    storage_sync_qdrant_parser = subparsers.add_parser(
        "storage-sync-qdrant",
        help="Sync exported qdrant_points.jsonl into Qdrant",
    )
    storage_sync_qdrant_parser.add_argument("--points", required=True, help="Path to qdrant_points.jsonl")
    storage_sync_qdrant_parser.add_argument("--env-file", default="", help="Optional env file with QDRANT_URL/API key")
    storage_sync_qdrant_parser.add_argument("--collection", default="codekb_atoms")
    storage_sync_qdrant_parser.add_argument(
        "--vector-size",
        type=int,
        default=None,
        help="Vector size; defaults to the configured embedding dimension (CODEKB_EMBEDDING_DIM, else 64)",
    )
    storage_sync_qdrant_parser.add_argument("--batch-size", type=int, default=64)
    storage_sync_qdrant_parser.add_argument("--timeout-seconds", type=int, default=20)
    storage_sync_qdrant_parser.add_argument("--execute", action="store_true")
    storage_sync_qdrant_parser.add_argument("--json", action="store_true")

    storage_sync_postgres_parser = subparsers.add_parser(
        "storage-sync-postgres",
        help="Sync exported postgres_upserts.jsonl into Postgres",
    )
    storage_sync_postgres_parser.add_argument("--upserts", required=True, help="Path to postgres_upserts.jsonl")
    storage_sync_postgres_parser.add_argument("--env-file", default="", help="Optional env file with POSTGRES_DSN")
    storage_sync_postgres_parser.add_argument("--execute", action="store_true")
    storage_sync_postgres_parser.add_argument("--json", action="store_true")

    feedback_parser = subparsers.add_parser("feedback", help="Append answer feedback to JSONL")
    feedback_parser.add_argument("--log", required=True)
    feedback_parser.add_argument("--answer-id", required=True)
    feedback_parser.add_argument("--trace-id", required=True)
    feedback_parser.add_argument("--rating", type=int, choices=[-1, 0, 1], required=True)
    feedback_parser.add_argument("--reason", default="")
    feedback_parser.add_argument("--user-id-hash", default="")
    feedback_parser.add_argument("--corrected-answer", default="")

    feedback_summary_parser = subparsers.add_parser("feedback-summary", help="Summarize feedback JSONL")
    feedback_summary_parser.add_argument("--log", required=True)
    feedback_summary_parser.add_argument("--badcase-limit", type=int, default=20)
    feedback_summary_parser.add_argument("--output")

    ingest_parser = subparsers.add_parser("ingest", help="Submit a KB candidate for audit")
    ingest_parser.add_argument("--store", default=_default_candidate_store())
    ingest_parser.add_argument("--pending-docs-dir", default=_default_pending_docs_dir())
    ingest_parser.add_argument("--sub-kb", required=True)
    ingest_parser.add_argument("--title", required=True)
    ingest_parser.add_argument("--content")
    ingest_parser.add_argument("--content-file")
    ingest_parser.add_argument("--source-type", default="manual")
    ingest_parser.add_argument("--source-ref", default="")
    ingest_parser.add_argument("--submitted-by-hash", default="")
    ingest_parser.add_argument("--metadata-json", default="{}")
    ingest_parser.add_argument("--allow-duplicate", action="store_true")

    candidate_list_parser = subparsers.add_parser("candidate-list", help="List KB candidates")
    candidate_list_parser.add_argument("--store", default=_default_candidate_store())
    candidate_list_parser.add_argument("--pending-docs-dir", default=_default_pending_docs_dir())
    candidate_list_parser.add_argument("--status")
    candidate_list_parser.add_argument("--limit", type=int, default=50)
    candidate_list_parser.add_argument("--json", action="store_true")

    candidate_purge_parser = subparsers.add_parser(
        "candidate-purge", help="Purge KB candidates by status (dry-run first; --apply to persist)"
    )
    candidate_purge_parser.add_argument("--store", default=_default_candidate_store())
    candidate_purge_parser.add_argument("--pending-docs-dir", default=_default_pending_docs_dir())
    candidate_purge_parser.add_argument("--status", required=True)
    candidate_purge_parser.add_argument("--apply", action="store_true")
    candidate_purge_parser.add_argument("--json", action="store_true")

    reconcile_parser = subparsers.add_parser(
        "reconcile", help="Reconcile approved candidates with pending docs (read-only)"
    )
    reconcile_parser.add_argument("--store", default=_default_candidate_store())
    reconcile_parser.add_argument("--pending-docs-dir", default=_default_pending_docs_dir())
    reconcile_parser.add_argument("--json", action="store_true")

    candidate_revise_parser = subparsers.add_parser("candidate-revise", help="Revise a KB candidate after request_revision")
    candidate_revise_parser.add_argument("--store", default=_default_candidate_store())
    candidate_revise_parser.add_argument("--pending-docs-dir", default=_default_pending_docs_dir())
    candidate_revise_parser.add_argument("--candidate-id", required=True)
    candidate_revise_parser.add_argument("--title", default="")
    candidate_revise_parser.add_argument("--content")
    candidate_revise_parser.add_argument("--content-file")
    candidate_revise_parser.add_argument("--metadata-json", default="{}")
    candidate_revise_parser.add_argument("--submitted-by-hash", default="")
    candidate_revise_parser.add_argument("--comment", default="")
    candidate_revise_parser.add_argument("--json", action="store_true")

    audit_parser = subparsers.add_parser("audit", help="Review a KB candidate")
    audit_parser.add_argument("--store", default=_default_candidate_store())
    audit_parser.add_argument("--pending-docs-dir", default=_default_pending_docs_dir())
    audit_parser.add_argument("--candidate-id", required=True)
    audit_parser.add_argument("--action", choices=["approve", "reject", "request_revision"], required=True)
    audit_parser.add_argument("--reviewer-hash", default="")
    audit_parser.add_argument("--comment", default="")

    publish_plan_parser = subparsers.add_parser("publish-plan", help="Plan Wiki publication for pending docs")
    publish_plan_parser.add_argument("--pending-docs-dir", default=_default_pending_docs_dir())
    publish_plan_parser.add_argument("--mode", default="manual", choices=["manual", "index_page", "template_copy"])
    publish_plan_parser.add_argument("--target-parentid", default="")
    publish_plan_parser.add_argument("--template-docid", default="")
    publish_plan_parser.add_argument("--index-docid", default="")
    publish_plan_parser.add_argument("--limit", type=int, default=50)
    publish_plan_parser.add_argument("--outbox", default=_default_publish_outbox())
    publish_plan_parser.add_argument("--no-outbox", action="store_true")
    publish_plan_parser.add_argument("--json", action="store_true")

    publish_outbox_parser = subparsers.add_parser("publish-outbox", help="Validate or execute Wiki publish outbox")
    publish_outbox_parser.add_argument("--outbox", default=_default_publish_outbox())
    publish_outbox_parser.add_argument("--report", default=_default_publish_report())
    publish_outbox_parser.add_argument("--ledger", default=_default_publish_ledger())
    publish_outbox_parser.add_argument("--limit", type=int, default=50)
    publish_outbox_parser.add_argument("--execute", action="store_true")
    publish_outbox_parser.add_argument("--confirm-real-publish", action="store_true")
    publish_outbox_parser.add_argument("--json", action="store_true")
    publish_outbox_parser.add_argument(
        "--require-quality-gate",
        action="store_true",
        help="Refuse to execute the publish outbox unless the P2 answer-quality gate passes",
    )

    governance_parser = subparsers.add_parser("governance-report", help="Build P4 KB governance report")
    governance_parser.add_argument("--fixtures", default="data/fixtures/sample_corpus.jsonl")
    governance_parser.add_argument(
        "--include-source",
        action="append",
        default=[],
        help="Additional source bundle path, e.g. /data/codekb/pending-docs",
    )
    governance_parser.add_argument("--registry", default="docs/kb-registry.draft.yaml")
    governance_parser.add_argument("--policy", default=_default_governance_policy())
    governance_parser.add_argument("--feedback-log", default=_default_feedback_log())
    governance_parser.add_argument("--candidate-store", default=_default_candidate_store())
    governance_parser.add_argument("--pending-docs-dir", default=_default_pending_docs_dir())
    governance_parser.add_argument("--stale-after-days", type=int, default=180)
    governance_parser.add_argument("--candidate-sla-days", type=int, default=3)
    governance_parser.add_argument("--output", default=_default_governance_report())
    governance_parser.add_argument("--item-limit", type=int, default=20)
    governance_parser.add_argument("--json", action="store_true")

    governance_state_parser = subparsers.add_parser("governance-state-sync", help="Sync P4 governance items into state ledger")
    governance_state_parser.add_argument("--fixtures", default="data/fixtures/sample_corpus.jsonl")
    governance_state_parser.add_argument("--include-source", action="append", default=[])
    governance_state_parser.add_argument("--registry", default="docs/kb-registry.draft.yaml")
    governance_state_parser.add_argument("--policy", default=_default_governance_policy())
    governance_state_parser.add_argument("--feedback-log", default=_default_feedback_log())
    governance_state_parser.add_argument("--candidate-store", default=_default_candidate_store())
    governance_state_parser.add_argument("--pending-docs-dir", default=_default_pending_docs_dir())
    governance_state_parser.add_argument("--stale-after-days", type=int, default=180)
    governance_state_parser.add_argument("--candidate-sla-days", type=int, default=3)
    governance_state_parser.add_argument("--state", default=_default_governance_state())
    governance_state_parser.add_argument("--json", action="store_true")

    governance_weekly_parser = subparsers.add_parser("governance-weekly-report", help="Build P4 curator weekly report")
    governance_weekly_parser.add_argument("--fixtures", default="data/fixtures/sample_corpus.jsonl")
    governance_weekly_parser.add_argument("--include-source", action="append", default=[])
    governance_weekly_parser.add_argument("--registry", default="docs/kb-registry.draft.yaml")
    governance_weekly_parser.add_argument("--policy", default=_default_governance_policy())
    governance_weekly_parser.add_argument("--feedback-log", default=_default_feedback_log())
    governance_weekly_parser.add_argument("--candidate-store", default=_default_candidate_store())
    governance_weekly_parser.add_argument("--pending-docs-dir", default=_default_pending_docs_dir())
    governance_weekly_parser.add_argument("--stale-after-days", type=int, default=180)
    governance_weekly_parser.add_argument("--candidate-sla-days", type=int, default=3)
    governance_weekly_parser.add_argument("--state", default=_default_governance_state())
    governance_weekly_parser.add_argument("--item-limit", type=int, default=20)
    governance_weekly_parser.add_argument("--output", default=_default_governance_weekly_report())
    governance_weekly_parser.add_argument("--json-output", default="")
    governance_weekly_parser.add_argument("--json", action="store_true")

    governance_export_parser = subparsers.add_parser("governance-export", help="Export P4 governance artifacts")
    governance_export_parser.add_argument("--fixtures", default="data/fixtures/sample_corpus.jsonl")
    governance_export_parser.add_argument("--include-source", action="append", default=[])
    governance_export_parser.add_argument("--registry", default="docs/kb-registry.draft.yaml")
    governance_export_parser.add_argument("--policy", default=_default_governance_policy())
    governance_export_parser.add_argument("--feedback-log", default=_default_feedback_log())
    governance_export_parser.add_argument("--candidate-store", default=_default_candidate_store())
    governance_export_parser.add_argument("--pending-docs-dir", default=_default_pending_docs_dir())
    governance_export_parser.add_argument("--stale-after-days", type=int, default=180)
    governance_export_parser.add_argument("--candidate-sla-days", type=int, default=3)
    governance_export_parser.add_argument("--target", default="manual", choices=["manual", "issue_tracker", "git"])
    governance_export_parser.add_argument("--min-severity", default="P1")
    governance_export_parser.add_argument("--ticket-limit", type=int, default=50)
    governance_export_parser.add_argument("--output-dir", required=True)

    governance_ticket_plan_parser = subparsers.add_parser(
        "governance-ticket-plan",
        help="Plan external ticket dispatch for P4 governance items",
    )
    governance_ticket_plan_parser.add_argument("--fixtures", default="data/fixtures/sample_corpus.jsonl")
    governance_ticket_plan_parser.add_argument("--include-source", action="append", default=[])
    governance_ticket_plan_parser.add_argument("--registry", default="docs/kb-registry.draft.yaml")
    governance_ticket_plan_parser.add_argument("--policy", default=_default_governance_policy())
    governance_ticket_plan_parser.add_argument("--feedback-log", default=_default_feedback_log())
    governance_ticket_plan_parser.add_argument("--candidate-store", default=_default_candidate_store())
    governance_ticket_plan_parser.add_argument("--pending-docs-dir", default=_default_pending_docs_dir())
    governance_ticket_plan_parser.add_argument("--stale-after-days", type=int, default=180)
    governance_ticket_plan_parser.add_argument("--candidate-sla-days", type=int, default=3)
    governance_ticket_plan_parser.add_argument("--target", default="manual", choices=["manual", "issue_tracker", "git"])
    governance_ticket_plan_parser.add_argument("--min-severity", default="P1")
    governance_ticket_plan_parser.add_argument("--include-type", action="append", default=[])
    governance_ticket_plan_parser.add_argument("--limit", type=int, default=50)
    governance_ticket_plan_parser.add_argument("--outbox", default=_default_governance_ticket_outbox())
    governance_ticket_plan_parser.add_argument("--state", default="")
    governance_ticket_plan_parser.add_argument("--skip-ticketed", action="store_true")
    governance_ticket_plan_parser.add_argument("--no-outbox", action="store_true")
    governance_ticket_plan_parser.add_argument("--json", action="store_true")

    governance_ticket_outbox_parser = subparsers.add_parser(
        "governance-ticket-outbox",
        help="Validate or execute P4 governance ticket outbox",
    )
    governance_ticket_outbox_parser.add_argument("--outbox", default=_default_governance_ticket_outbox())
    governance_ticket_outbox_parser.add_argument("--report", default=_default_governance_ticket_report())
    governance_ticket_outbox_parser.add_argument("--limit", type=int, default=50)
    governance_ticket_outbox_parser.add_argument("--execute", action="store_true")
    governance_ticket_outbox_parser.add_argument(
        "--state",
        default="",
        help="Optional governance state path; when set, real executed ticket ids are written back into it.",
    )
    governance_ticket_outbox_parser.add_argument("--json", action="store_true")

    quality_parser = subparsers.add_parser("quality-check", help="Run P2 answer quality gate")
    quality_parser.add_argument("--fixtures", default="data/fixtures/sample_corpus.jsonl")
    quality_parser.add_argument("--questions", default="data/fixtures/golden_questions.md")
    quality_parser.add_argument("--aliases", default="data/entity_aliases.yaml")
    quality_parser.add_argument("--top-k", type=int, default=4)
    quality_parser.add_argument("--prefix", action="append", help="Question prefix to include, e.g. REL")
    quality_parser.add_argument("--skip-missing-expected", action="store_true")
    quality_parser.add_argument("--min-hit-rate", type=float, default=0.75)
    quality_parser.add_argument("--min-citation-rate", type=float, default=1.0)
    quality_parser.add_argument("--min-faithfulness", type=float, default=0.7)
    quality_parser.add_argument("--max-refusal-rate", type=float, default=0.2)
    quality_parser.add_argument("--min-answer-correctness", type=float, default=None)
    quality_parser.add_argument(
        "--granularity", choices=["docid", "passage"], default="docid"
    )
    quality_parser.add_argument("--output")

    bench_parser = subparsers.add_parser("bench-latency", help="Benchmark ask() latency over golden set")
    bench_parser.add_argument("--fixtures", default="data/fixtures/sample_corpus.jsonl")
    bench_parser.add_argument("--questions", default="data/fixtures/golden_questions.md")
    bench_parser.add_argument("--aliases", default="data/entity_aliases.yaml")
    bench_parser.add_argument("--top-k", type=int, default=4)
    bench_parser.add_argument("--prefix", action="append", help="Question prefix to include, e.g. REL")
    bench_parser.add_argument("--warmup", type=int, default=1)
    bench_parser.add_argument("--repeats", type=int, default=3)
    bench_parser.add_argument("--limit", type=int, default=None)
    bench_parser.add_argument("--output")
    bench_parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "eval":
        prefixes = set(args.prefix) if args.prefix else None
        aliases = load_aliases(args.aliases) if args.aliases else None
        report = evaluate_golden_questions(
            fixture_path=args.fixtures,
            questions_path=args.questions,
            top_k=args.top_k,
            include_prefixes=prefixes,
            aliases=aliases,
            skip_missing_expected=args.skip_missing_expected,
        )
        print(f"total={report.total} evaluated={report.evaluated} skipped={report.skipped}")
        print(f"hit@{args.top_k}={report.hit_rate:.3f} ({report.hits}/{report.evaluated})")
        for result in report.results:
            if result.skipped:
                continue
            status = "HIT" if result.hit else "MISS"
            print(
                f"{status} {result.question_id} expected={','.join(result.expected_sources)} "
                f"retrieved={','.join(result.retrieved_sources) or '-'}"
            )
        return 0

    if args.command == "ask":
        service = OfflineKbService(
            fixture_path=args.fixtures,
            aliases_path=args.aliases,
            trace_log_path=args.trace_log,
            retriever_mode=args.retriever,
            index_db_path=args.index_db,
            answer_mode=args.answer_mode,
        )
        answer = service.ask(args.query, sub_kbs=set(args.sub_kb) if args.sub_kb else None, top_k=args.top_k)
        print(f"answer_id={answer.answer_id}")
        print(f"trace_id={answer.trace_id}")
        print(answer.answer)
        if answer.refused:
            print(f"refusal_reason={answer.refusal_reason}")
        else:
            print("citations=" + ",".join(f"{item.docid}#{item.anchor}" for item in answer.citations))
        return 0

    if args.command == "diagnose":
        if args.min_confidence < 0 or args.min_confidence > 1:
            raise SystemExit("--min-confidence must be between 0 and 1")
        context = _diagnostic_context_from_args(args)
        try:
            query = build_diagnostic_query(args.query, context)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        service = OfflineKbService(
            fixture_path=args.fixtures,
            aliases_path=args.aliases,
            trace_log_path=args.trace_log,
            retriever_mode=args.retriever,
            index_db_path=args.index_db,
            answer_mode=args.answer_mode,
        )
        owner_groups = _registry_owner_groups(args.registry)
        governance_items = ()
        if not args.no_governance:
            report = build_governance_report(
                [args.fixtures],
                registry_path=args.registry,
                policy_path=args.policy,
                feedback_log_path=args.feedback_log,
                candidate_store_path=args.candidate_store,
                pending_docs_dir=args.pending_docs_dir,
                stale_after_days=args.stale_after_days,
                candidate_sla_days=args.candidate_sla_days,
            )
            governance_items = report.items
        diagnosis = service.diagnose(
            query,
            sub_kbs=set(args.sub_kb) if args.sub_kb else None,
            top_k=args.top_k,
            governance_items=governance_items,
            owner_groups=owner_groups,
            min_confidence=args.min_confidence,
            context=context,
        )
        submission = None
        if args.submit_gap:
            try:
                submission = submit_diagnostic_gap(
                    diagnosis,
                    JsonCandidateStore(args.candidate_store, pending_docs_dir=args.pending_docs_dir),
                    submitted_by_hash=args.submitted_by_hash,
                    allow_duplicate=args.allow_duplicate,
                )
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc
        if args.json:
            payload = diagnosis.to_dict()
            if submission is not None:
                payload["gap_submission"] = submission.to_dict()
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        _print_diagnosis(diagnosis)
        if submission is not None:
            print(
                f"gap_submission candidate_id={submission.candidate.candidate_id} "
                f"duplicate={str(submission.duplicate).lower()} status={submission.candidate.status}"
            )
        return 0

    if args.command == "diagnose-gap-summary":
        summary = summarize_diagnostic_gaps(
            JsonCandidateStore(args.store, pending_docs_dir=args.pending_docs_dir),
            status=args.status or None,
            limit=args.limit,
        )
        if args.json:
            print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print(
            f"diagnose_gap_summary gaps={summary.total_diagnostic_gaps} "
            f"clusters={summary.clusters_total} store={summary.store_path}"
        )
        for cluster in summary.clusters:
            print(
                f"CLUSTER {cluster.cluster_id} sub_kb={cluster.sub_kb_id or '-'} "
                f"total={cluster.total_candidates} open={cluster.open_candidates} "
                f"owners={','.join(cluster.suggested_owners) or '-'} title={cluster.representative_title}"
            )
        return 0

    if args.command == "diagnose-webhook-events":
        if args.limit < 0 or args.limit > 200:
            raise SystemExit("--limit must be between 0 and 200")
        summary = JsonlDiagnosticWebhookStore(args.log).summary(
            source=args.source or None,
            status=args.status or None,
            action=args.action or None,
            limit=args.limit,
        )
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print(
            f"diagnose_webhook_events total={summary['total']} "
            f"unfiltered_total={summary['unfiltered_total']} invalid_lines={summary['invalid_lines']} "
            f"log={summary['path']}"
        )
        filters = summary["filters"]
        if any(filters.values()):
            print(
                "filters "
                f"source={filters['source'] or '-'} status={filters['status'] or '-'} "
                f"action={filters['action'] or '-'}"
            )
        print(
            "counts "
            f"sources={_format_counts(summary['by_source'])} "
            f"statuses={_format_counts(summary['by_status'])} "
            f"actions={_format_counts(summary['by_action'])}"
        )
        for event in summary["events"]:
            print(
                f"EVENT {event['created_at']} source={event['source']} action={event['action']} "
                f"status={event['status']} diagnosis={event['diagnosis_id'] or '-'} "
                f"trace={event['trace_id'] or '-'} query={event['query'][:120]}"
            )
        return 0

    if args.command == "diagnose-webhook-normalize":
        payload = _read_json_payload_arg(args.payload_json, args.payload_file, "payload")
        try:
            preview = preview_diagnostic_webhook(args.source, payload, mapping_path=args.mapping)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if args.json:
            print(json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print(
            f"diagnose_webhook_normalize source={preview['source']} "
            f"sub_kbs={','.join(preview['sub_kbs']) or '-'}"
        )
        print(f"query={preview['query']}")
        context = preview["context"]
        context_fields = []
        for field in ("surface", "repo", "branch", "error_code", "build_id", "mr_id"):
            if context.get(field):
                context_fields.append(f"{field}={context[field]}")
        if context_fields:
            print("context " + " ".join(context_fields))
        return 0

    if args.command == "diagnose-webhook-validate":
        payload = _read_json_payload_arg(args.payload_json, args.payload_file, "payload")
        try:
            report = validate_diagnostic_webhook(args.source, payload, mapping_path=args.mapping)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if report["valid"] else 1
        print(
            f"diagnose_webhook_validate source={report['source']} "
            f"valid={str(report['valid']).lower()} query_ready={str(report['query_ready']).lower()} "
            f"errors={len(report['errors'])} warnings={len(report['warnings'])}"
        )
        if report["query"]:
            print(f"query={report['query']}")
        for warning in report["warnings"]:
            print(f"WARNING {warning}")
        for error in report["errors"]:
            print(f"ERROR {error}")
        return 0 if report["valid"] else 1

    if args.command == "diagnose-webhook-mapping":
        try:
            mapping = effective_diagnostic_webhook_mapping(args.source, args.mapping)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        payload = mapping.to_dict()
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        source_mapping = payload["sources"][args.source]
        print(f"diagnose_webhook_mapping source={args.source} path={payload['path']} exists={str(payload['exists']).lower()}")
        print("query_paths=" + ",".join(source_mapping["query_paths"]))
        print("sub_kbs_paths=" + ",".join(source_mapping["sub_kbs_paths"]))
        print("context_fields=" + ",".join(sorted(source_mapping["context_paths"])))
        print("link_fields=" + ",".join(sorted(source_mapping["link_paths"])))
        return 0

    if args.command == "diagnose-webhook-sample-suite":
        try:
            summary = validate_diagnostic_webhook_sample_suite(args.samples, mapping_path=args.mapping)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if summary["status"] == "passed" else 1
        print(
            f"diagnose_webhook_sample_suite status={summary['status']} total={summary['total']} "
            f"passed={summary['passed']} failed={summary['failed']} samples={summary['path']}"
        )
        for sample in summary["samples"]:
            print(
                f"SAMPLE {sample['name'] or '-'} source={sample['source'] or '-'} status={sample['status']} "
                f"valid={str(sample['valid']).lower()} query_ready={str(sample['query_ready']).lower()} "
                f"errors={len(sample['errors'])}"
            )
            for error in sample["errors"]:
                print(f"ERROR {sample['name'] or '-'} {error}")
        return 0 if summary["status"] == "passed" else 1

    if args.command == "diagnose-webhook-sample-import":
        try:
            payload = load_diagnostic_webhook_payload_file(args.payload_file)
            result = import_diagnostic_webhook_sample(
                source=args.source,
                name=args.name,
                payload=payload,
                output_path=args.output,
                mapping_path=args.mapping,
                append=args.append,
                expected_context=_parse_key_value_args(args.expected_context),
                expected_sub_kbs=args.expected_sub_kb or None,
                forbidden_values=args.forbidden_value,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if result["validation"]["status"] == "passed" else 1
        print(
            f"diagnose_webhook_sample_import status={result['status']} source={result['source']} "
            f"name={result['sample_name']} output={result['output']} "
            f"raw_sensitive_values_detected={result['raw_sensitive_values_detected']}"
        )
        print(
            f"validation status={result['validation']['status']} total={result['validation']['total']} "
            f"failed={result['validation']['failed']}"
        )
        return 0 if result["validation"]["status"] == "passed" else 1

    if args.command == "diagnose-webhook-sample-activate":
        try:
            activation_env = _readiness_env(args.env_file)
            result = activate_diagnostic_webhook_samples(
                env_file=args.env_file,
                samples_path=args.samples,
                mapping_path=_readiness_value(
                    args.mapping,
                    activation_env,
                    "CODEKB_DIAGNOSE_WEBHOOK_MAPPING",
                    DEFAULT_WEBHOOK_MAPPING_PATH,
                ),
                env=activation_env,
                apply=args.apply,
                confirm_real_samples=args.confirm_real_samples,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if result["ok"] else 1
        suite = result["sample_suite"]
        print(
            f"diagnose_webhook_sample_activate status={result['status']} ok={str(result['ok']).lower()} "
            f"applied={str(result['applied']).lower()} samples={suite['path']} "
            f"total={suite['total']} sources={','.join(suite['sources'])}"
        )
        if result["restart_required"]:
            print("restart_required=true")
        print(result["message"])
        return 0 if result["ok"] else 1

    if args.command == "diagnose-integration-export":
        summary = export_diagnose_integration_pack(args.output_dir, api_base_url=args.api_base_url)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print(
            f"diagnose_integration_export output_dir={args.output_dir} "
            f"files={len(summary['files'])} mcp_tools={summary['mcp_tools']} mr_card_actions={summary['mr_card_actions']}"
        )
        return 0

    if args.command == "diagnose-readiness":
        try:
            report = _readiness_report_from_args(args)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if report["status"] != "blocked" else 1
        print(
            f"diagnose_readiness status={report['status']} checks={report['summary']['checks']} "
            f"ok={report['summary']['ok']} warn={report['summary']['warn']} "
            f"deferred={report['summary']['deferred']} blocked={report['summary']['blocked']}"
        )
        for check in report["checks"]:
            print(f"CHECK {check['id']} status={check['status']} message={check['message']}")
            if check["remediation"]:
                print(f"REMEDIATION {check['id']} {check['remediation']}")
        return 0 if report["status"] != "blocked" else 1

    if args.command == "diagnose-acceptance":
        try:
            readiness_report = _readiness_report_from_args(args)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        api_base_url = _readiness_report_api_base_url(readiness_report, args)
        report = build_p5_acceptance_report(readiness_report, api_base_url=api_base_url)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if report["accepted"] else 1
        print(
            f"diagnose_acceptance status={report['status']} accepted={str(report['accepted']).lower()} "
            f"readiness={report['readiness_status']} pending={len(report['pending_checks'])}"
        )
        for item in report["external_inputs"]:
            print(f"PENDING {item['check_id']} status={item['status']} owner={item['owner']}")
            if item["remediation"]:
                print(f"REMEDIATION {item['check_id']} {item['remediation']}")
        for item in report["final_verification"]:
            print(f"VERIFY {item['id']} {item['command']}")
        return 0 if report["accepted"] else 1

    if args.command == "diagnose-external-inputs":
        try:
            readiness_report = _readiness_report_from_args(args)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        env_file = str(args.env_file or os.getenv("CODEKB_ENV_FILE", "") or "/data/codekb/state/p5-secrets.env")
        api_base_url = _readiness_report_api_base_url(readiness_report, args)
        report = build_p5_external_input_plan(
            readiness_report,
            api_base_url=api_base_url,
            env_file=env_file,
            external_state_report=_external_state_report_from_args(args, env_file),
        )
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if readiness_report["status"] != "blocked" else 1
        print(render_p5_external_input_plan_markdown(report), end="")
        return 0 if readiness_report["status"] != "blocked" else 1

    if args.command == "diagnose-p5-handoff-bundle":
        try:
            readiness_report = _readiness_report_from_args(args)
            summary = _write_p5_handoff_bundle(args, readiness_report)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if readiness_report["status"] != "blocked" else 1
        print(
            f"diagnose_p5_handoff_bundle output_dir={summary['output_dir']} "
            f"status={summary['status']} pending={summary['pending_count']} files={len(summary['files'])} "
            f"im_template={summary['im_template']['status']}"
        )
        return 0 if readiness_report["status"] != "blocked" else 1

    if args.command == "diagnose-p5-final-verify":
        env_file = str(args.env_file or os.getenv("CODEKB_ENV_FILE", "") or "/data/codekb/state/p5-secrets.env")
        report = run_p5_final_verification(
            env_file=env_file,
            api_base_url=args.api_base_url,
            python=args.python,
            include_slow=not args.skip_slow,
            include_http=not args.skip_http,
            include_worker=not args.skip_worker,
            confirmation_worker=args.confirmation_worker,
        )
        if args.output:
            report["output"] = args.output
            write_p5_final_verification_report(args.output, report)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(render_p5_final_verification_text(report), end="")
        return 0 if report["ok"] else 1

    if args.command == "diagnose-p5-external-state":
        report = build_p5_external_state(
            env_file=str(args.env_file or os.getenv("CODEKB_ENV_FILE", "") or DEFAULT_P5_ENV_FILE),
            im_template=args.im_template,
            token_store=args.token_store,
            real_samples=args.real_samples,
        )
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(render_p5_external_state_text(report), end="")
        return 0

    if args.command == "diagnose-security-bootstrap":
        try:
            payload = build_p5_security_bootstrap(
                include_admin_token=not args.no_admin_token,
                include_static_mcp_token=args.include_static_mcp_token,
                token_bytes=args.token_bytes,
            )
            env_content = render_p5_security_env(payload)
            if args.output:
                write_p5_security_env_file(args.output, env_content, force=args.force)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if args.json:
            result = dict(payload)
            if args.output:
                result["output"] = args.output
                result["file_mode"] = "0600"
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.output:
            print(f"diagnose_security_bootstrap output={args.output} file_mode=0600 env_keys={len(payload['env'])}")
            for warning in payload["warnings"]:
                print(f"WARNING {warning}")
            return 0
        print(env_content, end="")
        for warning in payload["warnings"]:
            print(f"# WARNING {warning}")
        return 0

    if args.command == "diagnose-mcp-server":
        return run_stdio(
            DiagnoseMcpRuntime(
                fixture_path=args.fixtures,
                aliases_path=args.aliases,
                registry_path=args.registry,
                governance_policy_path=args.policy,
                feedback_log_path=args.feedback_log,
                candidate_store_path=args.candidate_store,
                pending_docs_dir=args.pending_docs_dir,
                mapping_path=args.mapping,
                trace_log_path=args.trace_log,
                retriever=args.retriever,
                index_db_path=args.index_db,
                api_base_url=args.api_base_url,
                mcp_token=args.mcp_token,
                token_store_path=args.token_store,
                allow_static_mcp_token=args.allow_static_mcp_token,
                confirmation_outbox_path=args.confirmation_outbox,
            )
        )

    if args.command == "diagnose-current-user-smoke":
        try:
            report = run_current_user_smoke(
                auth_token=args.auth_token,
                token_store_path=args.token_store,
                confirmation_outbox_path=args.confirmation_outbox,
                confirmation_responses_path=args.confirmation_responses,
                query=args.query,
                sub_kbs=args.sub_kb or DEFAULT_CURRENT_USER_SMOKE_SUB_KBS,
                reason=args.reason,
                message=args.message,
                respond=args.respond,
                decision=args.decision,
                comment=args.comment,
                delivery_report_path=args.delivery_report,
                delivery_log_path=args.delivery_log,
                fixture_path=args.fixtures,
                aliases_path=args.aliases,
                registry_path=args.registry,
                governance_policy_path=args.policy,
                feedback_log_path=args.feedback_log,
                candidate_store_path=args.candidate_store,
                pending_docs_dir=args.pending_docs_dir,
                trace_log_path=args.trace_log,
                retriever=args.retriever,
                index_db_path=args.index_db,
                api_base_url=args.api_base_url,
                top_k=args.top_k,
                include_governance=args.include_governance,
            )
        except (PermissionError, ValueError, RuntimeError) as exc:
            raise SystemExit(str(exc)) from exc
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if report["ok"] else 1
        confirmation = report["confirmation"]
        delivery = report["delivery"]["result"] or {}
        print(
            f"diagnose_current_user_smoke status={report['status']} ok={str(report['ok']).lower()} "
            f"confirmation_id={confirmation.get('confirmation_id', '')} "
            f"delivery_status={delivery.get('status', '')}"
        )
        return 0 if report["ok"] else 1

    if args.command == "diagnose-im-smoke":
        try:
            smoke_env = _readiness_env(args.env_file)
            report = run_im_delivery_smoke(
                env=smoke_env,
                auth_token=args.auth_token,
                token_store_path=_readiness_value(
                    args.token_store,
                    smoke_env,
                    "CODEKB_USER_TOKEN_STORE",
                    _default_user_token_store(),
                ),
                confirmation_outbox_path=_readiness_value(
                    args.confirmation_outbox,
                    smoke_env,
                    "CODEKB_USER_CONFIRMATION_OUTBOX",
                    _default_user_confirmation_outbox(),
                ),
                delivery_report_path=_readiness_value(
                    args.delivery_report,
                    smoke_env,
                    "CODEKB_USER_CONFIRMATION_REPORT",
                    _default_user_confirmation_report(),
                ),
                delivery_log_path=_readiness_value(
                    args.delivery_log,
                    smoke_env,
                    "CODEKB_USER_CONFIRMATION_DELIVERY_LOG",
                    _default_user_confirmation_delivery_log(),
                ),
                reason=args.reason,
                message=args.message,
                check_credentials=not args.skip_credential_check,
                execute=args.execute,
                write_enabled=str(smoke_env.get("CODEKB_ENABLE_IM_SEND", "") or "").strip() == "1",
            )
        except (PermissionError, ValueError, RuntimeError) as exc:
            raise SystemExit(str(exc)) from exc
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if report["ok"] else 1
        delivery = (report.get("delivery") or {}).get("result") or {}
        print(
            f"diagnose_im_smoke status={report['status']} ok={str(report['ok']).lower()} "
            f"credentials={report['credentials']['status']} delivery={delivery.get('status', '-')}"
        )
        return 0 if report["ok"] else 1

    if args.command == "diagnose-im-oauth-smoke":
        try:
            smoke_env = _readiness_env(args.env_file)
            report = run_im_oauth_smoke(
                env=smoke_env,
                token_store_path=_readiness_value(
                    args.token_store,
                    smoke_env,
                    "CODEKB_USER_TOKEN_STORE",
                    _default_user_token_store(),
                ),
                api_base_url=args.api_base_url,
                redirect_uri=args.redirect_uri,
                next_url=args.next_url,
                check_credentials=args.check_credentials,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if report["ok"] else 1
        print(
            f"diagnose_im_oauth_smoke status={report['status']} ok={str(report['ok']).lower()} "
            f"configured={str(report['configured']).lower()} active_tokens={report['token_store']['active']}"
        )
        return 0 if report["ok"] else 1

    if args.command == "diagnose-im-configure":
        try:
            env_file = args.env_file or os.getenv("CODEKB_ENV_FILE", "") or "/data/codekb/state/p5-secrets.env"
            if args.template_output and args.from_template:
                raise ValueError("--template-output and --from-template cannot be used together")
            if args.template_output:
                report = write_im_config_template(
                    output_path=args.template_output,
                    env_file=env_file,
                    api_base_url=args.api_base_url,
                    force=args.force,
                )
            else:
                report = configure_im_env(
                    env_file=env_file,
                    apply=args.apply,
                    confirm_real_send=args.confirm_real_send,
                    enable_send=args.enable_send,
                    template_path=args.from_template,
                    values={
                        "corp_id": args.corp_id,
                        "agent_id": args.agent_id,
                        "app_secret": args.app_secret,
                        "oauth_state_secret": args.oauth_state_secret,
                        "redirect_uri": args.redirect_uri,
                        "confirm_url_base": args.confirm_url_base,
                    },
                )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if report["ok"] else 1
        if args.template_output:
            print(
                f"diagnose_im_configure_template status={report['status']} ok={str(report['ok']).lower()} "
                f"output={report['output']} file_mode={report['file_mode']}"
            )
            print(report["message"])
            return 0 if report["ok"] else 1
        print(
            f"diagnose_im_configure status={report['status']} ok={str(report['ok']).lower()} "
            f"applied={str(report['applied']).lower()} configured={str(report['configured']).lower()} "
            f"updates={','.join(report['planned_update_keys']) or '-'}"
        )
        if report["missing_env"]:
            print("missing_env=" + ",".join(report["missing_env"]))
        if report["restart_required"]:
            print("restart_required=true")
        print(report["message"])
        return 0 if report["ok"] else 1

    if args.command == "diagnose-public-base-configure":
        try:
            env_file = args.env_file or os.getenv("CODEKB_ENV_FILE", "") or "/data/codekb/state/p5-secrets.env"
            report = configure_public_base_env(
                env_file=env_file,
                api_base_url=args.api_base_url,
                apply=args.apply,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if report["ok"] else 1
        print(
            f"diagnose_public_base_configure status={report['status']} ok={str(report['ok']).lower()} "
            f"applied={str(report['applied']).lower()} api_base_url={report['urls']['api_base_url']}"
        )
        if report["restart_required"]:
            print("restart_required=true")
        print(report["message"])
        return 0 if report["ok"] else 1

    if args.command == "auth-token-bind":
        try:
            metadata = json.loads(args.metadata_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"metadata-json must be valid JSON: {exc}") from exc
        if not isinstance(metadata, dict):
            raise SystemExit("metadata-json must be a JSON object")
        issued = JsonUserTokenStore(args.store).issue(
            user_id_hash=args.user_id_hash,
            display_name=args.display_name,
            scopes=args.scope,
            ttl_days=args.ttl_days,
            metadata=metadata,
        )
        issued = {"token": issued["token"], "binding": _public_cli_token_binding(issued["binding"])}
        if args.json:
            print(json.dumps(issued, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        binding = issued["binding"]
        print(f"auth_token_issued token_id={binding['token_id']} user_id_hash={binding['user_id_hash']}")
        print(f"token={issued['token']}")
        return 0

    if args.command == "auth-token-list":
        summary = JsonUserTokenStore(args.store).summary()
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print(
            f"auth_tokens total={summary['total']} active={summary['active']} "
            f"revoked={summary['revoked']} expired={summary['expired']} store={summary['path']}"
        )
        for binding in summary["bindings"]:
            print(
                f"TOKEN {binding['token_id']} user={binding['user_id_hash']} "
                f"active={str(not binding['revoked_at']).lower()} expires={binding['expires_at']}"
            )
        return 0

    if args.command == "auth-token-revoke":
        revoked = JsonUserTokenStore(args.store).revoke(args.token_id)
        payload = _public_cli_token_binding(revoked.to_dict())
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print(f"auth_token_revoked token_id={payload['token_id']} user_id_hash={payload['user_id_hash']}")
        return 0

    if args.command == "user-confirmation-outbox":
        report = process_user_confirmation_outbox(
            args.outbox,
            token_store_path=args.token_store,
            execute=args.execute,
            write_enabled=os.getenv("CODEKB_ENABLE_IM_SEND") == "1",
            limit=args.limit,
            confirmation_id=args.confirmation_id,
            report_path=args.report,
            delivery_log_path=args.delivery_log,
            max_retries=args.max_retries,
            backoff_base_seconds=args.backoff_base_seconds,
            dead_letter_path=args.dead_letter_path or None,
        )
        if args.json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if report.status not in {"failed", "invalid"} else 1
        print(
            f"user_confirmation_outbox status={report.status} total={report.total} "
            f"processed={report.processed} invalid_lines={report.invalid_lines} "
            f"executed_operations={report.executed_operations} blocked_operations={report.blocked_operations} "
            f"report={args.report}"
        )
        return 0 if report.status not in {"failed", "invalid"} else 1

    if args.command == "user-confirmation-pending":
        if not JsonUserTokenStore(args.token_store).validate(args.auth_token):
            raise SystemExit("invalid auth token")
        confirmations = list_user_confirmations(
            args.outbox,
            responses_path=args.responses,
            user_token=args.auth_token,
            limit=args.limit,
            include_responded=args.include_responded,
        )
        payload = {"total": len(confirmations), "confirmations": list(confirmations)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print(f"user_confirmation_pending total={payload['total']}")
        for confirmation in confirmations:
            print(
                f"CONFIRMATION {confirmation['confirmation_id']} "
                f"reason={confirmation['reason']} status={confirmation['status']}"
            )
        return 0

    if args.command == "user-confirmation-detail":
        if not JsonUserTokenStore(args.token_store).validate(args.auth_token):
            raise SystemExit("invalid auth token")
        try:
            confirmation = get_user_confirmation_detail(
                args.outbox,
                responses_path=args.responses,
                user_token=args.auth_token,
                confirmation_id=args.confirmation_id,
            )
        except PermissionError as exc:
            raise SystemExit(str(exc)) from exc
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if args.json:
            print(json.dumps(confirmation, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print(
            f"user_confirmation_detail confirmation_id={confirmation['confirmation_id']} "
            f"reason={confirmation['reason']} status={confirmation['status']}"
        )
        return 0

    if args.command == "user-confirmation-respond":
        try:
            metadata = json.loads(args.metadata_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"metadata-json must be valid JSON: {exc}") from exc
        if not isinstance(metadata, dict):
            raise SystemExit("metadata-json must be a JSON object")
        if not JsonUserTokenStore(args.token_store).validate(args.auth_token):
            raise SystemExit("invalid auth token")
        try:
            response = JsonlUserConfirmationResponseStore(args.responses).record(
                outbox_path=args.outbox,
                user_token=args.auth_token,
                confirmation_id=args.confirmation_id,
                decision=args.decision,
                comment=args.comment,
                metadata=metadata,
            )
        except PermissionError as exc:
            raise SystemExit(str(exc)) from exc
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        payload = public_confirmation_response(response)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print(
            f"user_confirmation_response confirmation_id={payload['confirmation_id']} "
            f"decision={payload['decision']} response_id={payload['response_id']}"
        )
        return 0

    if args.command == "user-confirmation-responses":
        summary = JsonlUserConfirmationResponseStore(args.responses).summary(limit=args.limit)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print(
            f"user_confirmation_responses total={summary['total']} "
            f"latest_total={summary['latest_total']} store={summary['path']}"
        )
        return 0

    if args.command == "sync":
        report = sync_source_path(
            args.fixtures,
            state_path=args.state_file,
            report_path=args.report_file,
            force=args.force,
        )
        print(f"source={report.source_path}")
        print(
            f"total={report.total} indexed={report.indexed} skipped={report.skipped} "
            f"failed={report.failed} atom_count={report.atom_count}"
        )
        if report.missing_docids:
            print("missing_docids=" + ",".join(report.missing_docids))
        for result in report.results:
            print(
                f"{result.status.upper()} {result.docid} sub_kb={result.sub_kb_id or '-'} "
                f"atoms={result.atom_count} reason={result.reason}"
            )
        return 1 if args.fail_on_error and report.failed else 0

    if args.command == "p1-check":
        prefixes = set(args.prefix) if args.prefix else {"REL", "TST", "INC"}
        sync_report = sync_source_path(
            args.fixtures,
            state_path=args.state_file,
            report_path=args.report_file,
        )
        aliases = load_aliases(args.aliases) if args.aliases else None
        eval_report = evaluate_golden_questions(
            fixture_path=args.fixtures,
            questions_path=args.questions,
            top_k=args.top_k,
            include_prefixes=prefixes,
            aliases=aliases,
            skip_missing_expected=args.skip_missing_expected,
        )
        passed = sync_report.failed == 0 and eval_report.hit_rate >= args.min_hit_rate
        print(
            f"sync total={sync_report.total} indexed={sync_report.indexed} "
            f"skipped={sync_report.skipped} failed={sync_report.failed} atom_count={sync_report.atom_count}"
        )
        print(
            f"eval total={eval_report.total} evaluated={eval_report.evaluated} skipped={eval_report.skipped} "
            f"hit@{args.top_k}={eval_report.hit_rate:.3f} ({eval_report.hits}/{eval_report.evaluated})"
        )
        print(f"p1_gate={'PASS' if passed else 'FAIL'}")
        return 0 if passed else 1

    if args.command == "export-index":
        gate_exit = _enforce_quality_gate(args)
        if gate_exit is not None:
            return gate_exit
        summary = export_index_artifacts(args.fixtures, args.output_dir, include_paths=tuple(args.include_source))
        print(
            " ".join(
                [
                    f"source_documents={summary['source_documents']}",
                    f"knowledge_atoms={summary['knowledge_atoms']}",
                    f"postgres_upserts={summary['postgres_upserts']}",
                    f"opensearch_documents={summary['opensearch_documents']}",
                    f"qdrant_points={summary['qdrant_points']}",
                ]
            )
        )
        return 0

    if args.command == "build-index":
        gate_exit = _enforce_quality_gate(args)
        if gate_exit is not None:
            return gate_exit
        summary = build_local_index(args.fixtures, args.db_path, include_paths=tuple(args.include_source))
        print(
            f"db_path={summary.db_path} source_documents={summary.source_documents} "
            f"knowledge_atoms={summary.knowledge_atoms}"
        )
        return 0

    if args.command == "index-stats":
        stats = local_index_stats(args.db_path)
        print(
            f"db_path={stats['db_path']} source_documents={stats['source_documents']} "
            f"knowledge_atoms={stats['knowledge_atoms']} schema_version={stats['schema_version']}"
        )
        return 0

    if args.command == "storage-readiness":
        report = build_storage_readiness(
            env_file=args.env_file or None,
            timeout_seconds=args.timeout_seconds,
        )
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            summary = report["summary"]
            print(
                " ".join(
                    [
                        f"storage_status={report['status']}",
                        f"ok={summary['ok']}",
                        f"deferred={summary['deferred']}",
                        f"pending={summary['pending']}",
                        f"error={summary['error']}",
                    ]
                )
            )
        return 0 if report["status"] in {"ready", "deferred", "pending_external_inputs"} else 1

    if args.command == "storage-sync-qdrant":
        try:
            vector_size = (
                args.vector_size
                if args.vector_size is not None
                else load_embedding_config().dimensions
            )
            report = sync_qdrant_points_from_env(
                points_path=args.points,
                env_file=args.env_file or None,
                collection=args.collection,
                vector_size=vector_size,
                execute=args.execute,
                batch_size=args.batch_size,
                timeout_seconds=args.timeout_seconds,
            )
        except ValueError as exc:
            print(f"storage-sync-qdrant failed: {exc}")
            return 2
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(
                " ".join(
                    [
                        f"qdrant_sync_status={report['status']}",
                        f"execute={str(report['execute']).lower()}",
                        f"collection={report['collection']}",
                        f"points={report['points']}",
                        f"batches={report['batches']}",
                    ]
                )
            )
        return 0 if report["status"] in {"planned", "synced"} else 1

    if args.command == "storage-sync-postgres":
        try:
            report = sync_postgres_upserts_from_env(
                upserts_path=args.upserts,
                env_file=args.env_file or None,
                execute=args.execute,
            )
        except ValueError as exc:
            print(f"storage-sync-postgres failed: {exc}")
            return 2
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(
                " ".join(
                    [
                        f"postgres_sync_status={report['status']}",
                        f"execute={str(report['execute']).lower()}",
                        f"upserts={report['upserts']}",
                    ]
                )
            )
        return 0 if report["status"] in {"planned", "synced"} else 1

    if args.command == "p3-usecase-smoke":
        report = run_p3_usecase_smoke(
            work_dir=args.work_dir or None,
            fixture_path=args.fixtures,
            aliases_path=args.aliases,
            publish_mode=args.publish_mode,
            index_docid=args.index_docid,
            template_docid=args.template_docid,
            target_parentid=args.target_parentid,
        )
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(
                f"p3_usecase_smoke status={report['status']} candidate_id={report['ingest']['candidate_id']} "
                f"ask_refused={str(report['ask']['refused']).lower()} "
                f"publish_status={report['publish']['process_status']} work_dir={report['paths']['root']}"
            )
        return 0 if report["status"] == "passed" else 1

    if args.command == "feedback":
        record = JsonlFeedbackStore(args.log).append(
            answer_id=args.answer_id,
            trace_id=args.trace_id,
            rating=args.rating,
            reason=args.reason,
            user_id_hash=args.user_id_hash,
            corrected_answer=args.corrected_answer,
        )
        print(f"status=accepted feedback_id={record.feedback_id}")
        return 0

    if args.command == "feedback-summary":
        summary = summarize_feedback(args.log, badcase_limit=args.badcase_limit).to_dict()
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(
                json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        print(
            f"feedback total={summary['total']} positive={summary['positive']} neutral={summary['neutral']} "
            f"negative={summary['negative']} corrected={summary['corrected']} "
            f"negative_rate={summary['negative_rate']:.3f} corrected_rate={summary['corrected_rate']:.3f} "
            f"invalid_lines={summary['invalid_lines']}"
        )
        print(f"badcases={len(summary['badcases'])}")
        return 0

    if args.command == "ingest":
        try:
            metadata = json.loads(args.metadata_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"metadata-json must be valid JSON: {exc}") from exc
        if not isinstance(metadata, dict):
            raise SystemExit("metadata-json must be a JSON object")
        submission = JsonCandidateStore(args.store, pending_docs_dir=args.pending_docs_dir).submit(
            sub_kb_id=args.sub_kb,
            title=args.title,
            content=_read_content_arg(args.content, args.content_file),
            source_type=args.source_type,
            source_ref=args.source_ref,
            submitted_by_hash=args.submitted_by_hash,
            metadata=metadata,
            allow_duplicate=args.allow_duplicate,
        )
        print(
            f"status={'duplicate' if submission.duplicate else 'accepted'} "
            f"candidate_id={submission.candidate.candidate_id} "
            f"duplicate={str(submission.duplicate).lower()} "
            f"dedupe_key={submission.candidate.dedupe_key}"
        )
        if submission.candidate.conflict_candidate_id:
            print(f"conflict_candidate_id={submission.candidate.conflict_candidate_id}")
        return 0

    if args.command == "candidate-list":
        store = JsonCandidateStore(args.store, pending_docs_dir=args.pending_docs_dir)
        candidates = store.list(status=args.status, limit=args.limit)
        if args.json:
            print(json.dumps([candidate.to_dict() for candidate in candidates], ensure_ascii=False, indent=2))
            return 0
        summary = store.summary()
        print(
            f"candidates total={summary['total']} pending_review={summary['pending_review']} "
            f"approved={summary['approved']} rejected={summary['rejected']} "
            f"needs_revision={summary['needs_revision']} conflicts={summary['conflicts']}"
        )
        for candidate in candidates:
            print(
                f"{candidate.status.upper()} {candidate.candidate_id} sub_kb={candidate.sub_kb_id} "
                f"title={candidate.title} conflict={candidate.conflict_candidate_id or '-'}"
            )
        return 0

    if args.command == "candidate-purge":
        store = JsonCandidateStore(args.store, pending_docs_dir=args.pending_docs_dir)
        plan = store.purge(status=args.status, dry_run=not args.apply)
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        mode = "applied" if args.apply else "dry-run"
        print(
            f"candidate-purge status={plan['status']} mode={mode} "
            f"matched_candidates={plan['matched_candidates']} matched_audits={plan['matched_audits']} "
            f"pending_docs={plan['pending_docs']} remaining_candidates={plan['remaining_candidates']}"
        )
        return 0

    if args.command == "reconcile":
        store = JsonCandidateStore(args.store, pending_docs_dir=args.pending_docs_dir)
        report = reconcile_candidates(store, args.pending_docs_dir)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        counts = report["counts"]
        print(
            f"reconcile approved_candidates={report['approved_candidates']} "
            f"ok={counts['ok']} orphan_docs={counts['orphan_docs']} missing_docs={counts['missing_docs']}"
        )
        return 0

    if args.command == "candidate-revise":
        try:
            metadata = json.loads(args.metadata_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"metadata-json must be valid JSON: {exc}") from exc
        if not isinstance(metadata, dict):
            raise SystemExit("metadata-json must be a JSON object")
        result = JsonCandidateStore(args.store, pending_docs_dir=args.pending_docs_dir).revise(
            args.candidate_id,
            title=args.title,
            content=_read_content_arg(args.content, args.content_file),
            metadata=metadata,
            submitted_by_hash=args.submitted_by_hash,
            comment=args.comment,
        )
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print(
            f"revision candidate_id={result.candidate.candidate_id} "
            f"status={result.candidate.status} audit_id={result.audit.audit_id}"
        )
        return 0

    if args.command == "audit":
        result = JsonCandidateStore(args.store, pending_docs_dir=args.pending_docs_dir).audit(
            args.candidate_id,
            action=args.action,
            reviewer_hash=args.reviewer_hash,
            comment=args.comment,
        )
        print(
            f"audit action={result.audit.action} candidate_id={result.candidate.candidate_id} "
            f"status={result.candidate.status}"
        )
        if result.audit.output_path:
            print(f"output_path={result.audit.output_path}")
        return 0

    if args.command == "publish-plan":
        plans = build_publish_plans(
            args.pending_docs_dir,
            mode=args.mode,
            target_parentid=args.target_parentid,
            template_docid=args.template_docid,
            index_docid=args.index_docid,
            limit=args.limit,
        )
        outbox_count = 0
        if not args.no_outbox:
            outbox_count = write_publish_outbox(plans, args.outbox)
        if args.json:
            print(json.dumps([plan.to_dict() for plan in plans], ensure_ascii=False, indent=2))
            return 0
        print(
            f"publish_plan total={len(plans)} mode={args.mode} "
            f"outbox={args.outbox if not args.no_outbox else '-'} outbox_written={outbox_count}"
        )
        for plan in plans:
            print(
                f"PLAN {plan.candidate_id} sub_kb={plan.sub_kb_id} title={plan.title} "
                f"operations={','.join(operation.tool for operation in plan.operations)}"
            )
        return 0

    if args.command == "publish-outbox":
        if args.execute:
            gate_exit = _enforce_quality_gate(args)
            if gate_exit is not None:
                return gate_exit
        write_enabled = os.getenv("CODEKB_ENABLE_WIKI_WRITE") == "1"
        # 只有真正执行且打开了写开关时才创建真实的 Wiki 写客户端,
        # 否则保持 None,走 blocked_missing_client。
        client = None
        if args.execute and write_enabled:
            factory = wiki_publish_client_factory or HttpWikiPublishClient.from_env
            client = factory()
        report = process_publish_outbox(
            args.outbox,
            execute=args.execute,
            write_enabled=write_enabled,
            client=client,
            limit=args.limit,
            report_path=args.report,
            ledger_path=args.ledger,
            confirm_real_publish=args.confirm_real_publish,
        )
        if args.json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if not report.status.startswith("blocked") and report.status != "partial" else 1
        print(
            f"publish_outbox status={report.status} total={report.total} processed={report.processed} "
            f"invalid_lines={report.invalid_lines} executed_operations={report.executed_operations} "
            f"blocked_operations={report.blocked_operations} skipped_operations={report.skipped_operations} "
            f"report={args.report} ledger={args.ledger} confirm_real_publish={args.confirm_real_publish}"
        )
        for result in report.results:
            print(
                f"{result.status.upper()} {result.candidate_id} mode={result.mode} "
                f"operations={','.join(operation.status for operation in result.operations)}"
            )
        return 0 if not report.status.startswith("blocked") and report.status != "partial" else 1

    if args.command == "governance-report":
        report = build_governance_report(
            [args.fixtures, *args.include_source],
            registry_path=args.registry,
            policy_path=args.policy,
            feedback_log_path=args.feedback_log,
            candidate_store_path=args.candidate_store,
            pending_docs_dir=args.pending_docs_dir,
            stale_after_days=args.stale_after_days,
            candidate_sla_days=args.candidate_sla_days,
        )
        if args.output:
            write_governance_report(report, args.output)
        if args.json:
            print(json.dumps(report.to_dict(item_limit=args.item_limit), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print(
            f"governance source_documents={report.source_documents} open_items={len(report.items)} "
            f"p1_items={report.counts_by_severity.get('P1', 0)} output={args.output or '-'}"
        )
        type_counts = ",".join(f"{key}={value}" for key, value in sorted(report.counts_by_type.items()))
        print(f"counts_by_type={type_counts or '-'}")
        for item in report.items[: max(0, args.item_limit)]:
            print(
                f"{item.severity} {item.item_type} sub_kb={item.sub_kb_id or '-'} "
                f"owner={item.suggested_owner or '-'} title={item.title}"
            )
        return 0

    if args.command == "governance-state-sync":
        report = build_governance_report(
            [args.fixtures, *args.include_source],
            registry_path=args.registry,
            policy_path=args.policy,
            feedback_log_path=args.feedback_log,
            candidate_store_path=args.candidate_store,
            pending_docs_dir=args.pending_docs_dir,
            stale_after_days=args.stale_after_days,
            candidate_sla_days=args.candidate_sla_days,
        )
        result = sync_governance_state(report, args.state)
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print(
            f"governance_state state={result.state_path} items_seen={result.items_seen} "
            f"new_items={result.new_items} updated_items={result.updated_items} "
            f"not_seen_items={result.not_seen_items} ticketed_items={result.ticketed_items}"
        )
        return 0

    if args.command == "governance-weekly-report":
        report = build_governance_report(
            [args.fixtures, *args.include_source],
            registry_path=args.registry,
            policy_path=args.policy,
            feedback_log_path=args.feedback_log,
            candidate_store_path=args.candidate_store,
            pending_docs_dir=args.pending_docs_dir,
            stale_after_days=args.stale_after_days,
            candidate_sla_days=args.candidate_sla_days,
        )
        state_summary = summarize_governance_state(args.state) if args.state else {}
        weekly = build_curator_weekly_report(report, state_summary=state_summary, item_limit=args.item_limit)
        if args.output:
            write_curator_weekly_report(weekly, args.output)
        if args.json_output:
            Path(args.json_output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json_output).write_text(
                json.dumps(weekly.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        if args.json:
            print(json.dumps(weekly.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print(
            f"governance_weekly_report open_items={weekly.summary['open_items']} "
            f"p1_items={weekly.summary['p1_items']} inactive_owners={weekly.summary['inactive_owners']} "
            f"state_tickets={weekly.summary['state_tickets']} output={args.output or '-'}"
        )
        return 0

    if args.command == "governance-export":
        report = build_governance_report(
            [args.fixtures, *args.include_source],
            registry_path=args.registry,
            policy_path=args.policy,
            feedback_log_path=args.feedback_log,
            candidate_store_path=args.candidate_store,
            pending_docs_dir=args.pending_docs_dir,
            stale_after_days=args.stale_after_days,
            candidate_sla_days=args.candidate_sla_days,
        )
        plans = build_governance_ticket_plans(
            report,
            target=args.target,
            min_severity=args.min_severity,
            limit=args.ticket_limit,
        )
        summary = export_governance_artifacts(report, plans, args.output_dir)
        print(
            f"governance_export items={summary['governance_items']} "
            f"ticket_plans={summary['governance_ticket_plans']} "
            f"postgres_upserts={summary['postgres_upserts']} output_dir={args.output_dir}"
        )
        return 0

    if args.command == "governance-ticket-plan":
        report = build_governance_report(
            [args.fixtures, *args.include_source],
            registry_path=args.registry,
            policy_path=args.policy,
            feedback_log_path=args.feedback_log,
            candidate_store_path=args.candidate_store,
            pending_docs_dir=args.pending_docs_dir,
            stale_after_days=args.stale_after_days,
            candidate_sla_days=args.candidate_sla_days,
        )
        skip_item_ids = governance_state_ticketed_item_ids(args.state, targets=[args.target]) if args.skip_ticketed and args.state else set()
        plans = build_governance_ticket_plans(
            report,
            target=args.target,
            min_severity=args.min_severity,
            include_types=args.include_type,
            skip_item_ids=skip_item_ids,
            limit=args.limit,
        )
        outbox_count = 0
        if not args.no_outbox:
            outbox_count = write_governance_ticket_outbox(plans, args.outbox)
        state_result = sync_governance_state(report, args.state, ticket_plans=plans) if args.state else None
        if args.json:
            print(json.dumps([plan.to_dict() for plan in plans], ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print(
            f"governance_ticket_plan total={len(plans)} target={args.target} "
            f"min_severity={args.min_severity} outbox={args.outbox if not args.no_outbox else '-'} "
            f"outbox_written={outbox_count}"
        )
        if state_result is not None:
            print(
                f"state={state_result.state_path} items_seen={state_result.items_seen} "
                f"new_items={state_result.new_items} ticketed_items={state_result.ticketed_items}"
            )
        for plan in plans:
            print(
                f"PLAN {plan.item_id} severity={plan.severity} type={plan.item_type} "
                f"target={plan.target} assignee={plan.assignee or '-'} operations={','.join(op.tool for op in plan.operations)}"
            )
        return 0

    if args.command == "governance-ticket-outbox":
        write_enabled = os.getenv("CODEKB_ENABLE_GOVERNANCE_WRITE") == "1"
        # 只有真正执行且打开了写开关时才创建真实的 ISSUE_TRACKER/Git 客户端,
        # 否则保持 None,走 blocked_missing_client。
        client = None
        if args.execute and write_enabled:
            factory = governance_ticket_client_factory or HttpGovernanceTicketClient.from_env
            client = factory()
        report = process_governance_ticket_outbox(
            args.outbox,
            execute=args.execute,
            write_enabled=write_enabled,
            client=client,
            limit=args.limit,
            report_path=args.report,
        )
        state_writeback = None
        if str(getattr(args, "state", "") or "").strip():
            state_writeback = sync_governance_state_from_ticket_results(report, args.state)
        if args.json:
            payload = report.to_dict()
            if state_writeback is not None:
                payload["state_writeback"] = state_writeback
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if report.status not in {"blocked", "partial"} else 1
        print(
            f"governance_ticket_outbox status={report.status} total={report.total} processed={report.processed} "
            f"invalid_lines={report.invalid_lines} executed_operations={report.executed_operations} "
            f"blocked_operations={report.blocked_operations} report={args.report}"
        )
        for result in report.results:
            print(
                f"{result.status.upper()} {result.item_id} target={result.target} "
                f"operations={','.join(operation.status for operation in result.operations)}"
            )
        if state_writeback is not None:
            print(
                f"state_writeback state={state_writeback['state_path']} "
                f"recorded_tickets={state_writeback['recorded_tickets']} "
                f"updated_items={state_writeback['updated_items']}"
            )
        return 0 if report.status not in {"blocked", "partial"} else 1

    if args.command == "quality-check":
        report = evaluate_quality(
            fixture_path=args.fixtures,
            questions_path=args.questions,
            aliases_path=args.aliases,
            include_prefixes=set(args.prefix) if args.prefix else {"REL", "TST", "INC"},
            top_k=args.top_k,
            min_hit_rate=args.min_hit_rate,
            min_citation_rate=args.min_citation_rate,
            min_faithfulness=args.min_faithfulness,
            max_refusal_rate=args.max_refusal_rate,
            min_answer_correctness=args.min_answer_correctness,
            granularity=args.granularity,
            skip_missing_expected=args.skip_missing_expected,
            output_path=args.output,
        )
        refusal_flag = "OVER" if report.refusal_rate > args.max_refusal_rate else "ok"
        print(
            f"quality total={report.total} evaluated={report.evaluated} "
            f"hit@{args.top_k}={report.hit_rate:.3f} citation_rate={report.citation_rate:.3f} "
            f"refusal_rate={report.refusal_rate:.3f}({refusal_flag}/max={args.max_refusal_rate:.3f}) "
            f"faithfulness={report.faithfulness:.3f} answer_correctness={report.answer_correctness:.3f} "
            f"granularity={args.granularity}"
        )
        print(f"quality_gate={'PASS' if report.passed else 'FAIL'}")
        return 0 if report.passed else 1

    if args.command == "bench-latency":
        benchmark = run_latency_benchmark(
            fixture_path=args.fixtures,
            questions_path=args.questions,
            aliases_path=args.aliases,
            include_prefixes=set(args.prefix) if args.prefix else {"REL", "TST", "INC"},
            top_k=args.top_k,
            warmup=args.warmup,
            repeats=args.repeats,
            limit=args.limit,
            output_path=args.output,
        )
        if args.json:
            print(json.dumps(benchmark.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(
                f"bench-latency samples={benchmark.samples} queries={benchmark.queries} "
                f"warmup={benchmark.warmup} repeats={benchmark.repeats} "
                f"p50={benchmark.p50_ms:.3f}ms p95={benchmark.p95_ms:.3f}ms "
                f"p99={benchmark.p99_ms:.3f}ms max={benchmark.max_ms:.3f}ms mean={benchmark.mean_ms:.3f}ms"
            )
        return 0

    return 2


def _default_candidate_store() -> str:
    return os.getenv("CODEKB_CANDIDATE_STORE", "/data/codekb/state/candidates.json")


def _public_cli_token_binding(binding: dict) -> dict:
    token_hash = str(binding.get("token_hash", "") or "")
    return {
        "token_id": binding.get("token_id", ""),
        "created_at": binding.get("created_at", ""),
        "expires_at": binding.get("expires_at", ""),
        "revoked_at": binding.get("revoked_at", ""),
        "user_id_hash": binding.get("user_id_hash", ""),
        "token_hash_prefix": token_hash[:12],
        "display_name": binding.get("display_name", ""),
        "scopes": list(binding.get("scopes", []) or []),
        "metadata": public_token_metadata(dict(binding.get("metadata", {}) or {})),
    }


def _readiness_env(env_file: str) -> dict[str, str]:
    env = dict(os.environ)
    normalized_file = str(env_file or "").strip()
    if normalized_file:
        env.update(_parse_env_file(normalized_file))
    return env


def _readiness_report_from_args(args) -> dict:
    readiness_env = _readiness_env(args.env_file)
    api_base_url = _readiness_api_base_url(args, readiness_env)
    return build_p5_readiness_report(
        fixture_path=_readiness_value(
            args.fixtures,
            readiness_env,
            "CODEKB_FIXTURES",
            "data/fixtures/sample_corpus.jsonl",
        ),
        aliases_path=_readiness_value(args.aliases, readiness_env, "CODEKB_ALIASES", "data/entity_aliases.yaml"),
        registry_path=_readiness_value(
            args.registry,
            readiness_env,
            "CODEKB_REGISTRY",
            "docs/kb-registry.draft.yaml",
        ),
        governance_policy_path=_readiness_value(
            args.policy,
            readiness_env,
            "CODEKB_GOVERNANCE_POLICY",
            "docs/governance-policy.draft.yaml",
        ),
        index_db_path=_readiness_value(args.index_db, readiness_env, "CODEKB_INDEX_DB", ""),
        diagnose_webhook_mapping_path=_readiness_value(
            args.mapping,
            readiness_env,
            "CODEKB_DIAGNOSE_WEBHOOK_MAPPING",
            DEFAULT_WEBHOOK_MAPPING_PATH,
        ),
        diagnose_webhook_samples_path=_readiness_value(
            args.samples,
            readiness_env,
            "CODEKB_DIAGNOSE_WEBHOOK_SAMPLES",
            DEFAULT_WEBHOOK_SAMPLE_SUITE_PATH,
        ),
        diagnose_webhook_log_path=_readiness_value(
            args.webhook_log,
            readiness_env,
            "CODEKB_DIAGNOSE_WEBHOOK_LOG",
            "/data/codekb/logs/diagnose-webhook.jsonl",
        ),
        user_token_store_path=_readiness_value(
            args.token_store,
            readiness_env,
            "CODEKB_USER_TOKEN_STORE",
            "/data/codekb/state/user-tokens.json",
        ),
        user_confirmation_outbox_path=_readiness_value(
            args.confirmation_outbox,
            readiness_env,
            "CODEKB_USER_CONFIRMATION_OUTBOX",
            "/data/codekb/outbox/user-confirmation.jsonl",
        ),
        user_confirmation_responses_path=_readiness_value(
            args.confirmation_responses,
            readiness_env,
            "CODEKB_USER_CONFIRMATION_RESPONSES",
            "/data/codekb/state/user-confirmation-responses.jsonl",
        ),
        api_base_url=api_base_url,
        env=readiness_env,
    )


def _external_state_report_from_args(args, env_file: str) -> dict:
    readiness_env = _readiness_env(env_file)
    return build_p5_external_state(
        env_file=env_file,
        im_template=_readiness_value(
            getattr(args, "im_template", None),
            readiness_env,
            "CODEKB_P5_IM_TEMPLATE",
            DEFAULT_IM_TEMPLATE,
        ),
        token_store=_readiness_value(
            getattr(args, "token_store", None),
            readiness_env,
            "CODEKB_USER_TOKEN_STORE",
            DEFAULT_USER_TOKEN_STORE,
        ),
        real_samples=_readiness_value(
            getattr(args, "real_samples", None),
            readiness_env,
            "CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES",
            DEFAULT_REAL_SAMPLES,
        ),
    )


def _readiness_value(provided: str | None, env: dict[str, str], env_name: str, fallback: str) -> str:
    if provided not in (None, ""):
        return str(provided)
    return str(env.get(env_name, "") or fallback)


def _readiness_api_base_url(args, env: dict[str, str]) -> str:
    provided = str(getattr(args, "api_base_url", "") or "").strip()
    if provided and provided != DEFAULT_API_BASE_URL:
        return provided.rstrip("/")
    return str(env.get("CODEKB_API_BASE_URL", "") or provided or DEFAULT_API_BASE_URL).rstrip("/")


def _readiness_report_api_base_url(readiness_report: dict, args) -> str:
    return str(readiness_report.get("api_base_url") or getattr(args, "api_base_url", "") or DEFAULT_API_BASE_URL).rstrip("/")


def _parse_env_file(path: str | Path) -> dict[str, str]:
    env_path = Path(path)
    if not env_path.exists():
        raise ValueError(f"env file not found: {env_path}")
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise ValueError(f"invalid env file line {line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        if not _valid_env_key(key):
            raise ValueError(f"invalid env file line {line_number}: invalid key")
        values[key] = _parse_env_value(value.strip())
    return values


def _parse_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _valid_env_key(key: str) -> bool:
    if not key:
        return False
    if not (key[0].isalpha() or key[0] == "_"):
        return False
    return all(char.isalnum() or char == "_" for char in key)


def _default_feedback_log() -> str:
    return os.getenv("CODEKB_FEEDBACK_LOG", "/data/codekb/logs/feedback.jsonl")


def _default_diagnose_webhook_log() -> str:
    return os.getenv("CODEKB_DIAGNOSE_WEBHOOK_LOG", "/data/codekb/logs/diagnose-webhook.jsonl")


def _default_diagnose_webhook_mapping() -> str:
    return os.getenv("CODEKB_DIAGNOSE_WEBHOOK_MAPPING", DEFAULT_WEBHOOK_MAPPING_PATH)


def _default_diagnose_webhook_samples() -> str:
    return os.getenv("CODEKB_DIAGNOSE_WEBHOOK_SAMPLES", DEFAULT_WEBHOOK_SAMPLE_SUITE_PATH)


def _default_mcp_token() -> str:
    return os.getenv("CODEKB_MCP_TOKEN", "")


def _default_user_token_store() -> str:
    return os.getenv("CODEKB_USER_TOKEN_STORE", "/data/codekb/state/user-tokens.json")


def _default_user_confirmation_outbox() -> str:
    return os.getenv("CODEKB_USER_CONFIRMATION_OUTBOX", "/data/codekb/outbox/user-confirmation.jsonl")


def _default_user_confirmation_report() -> str:
    return os.getenv("CODEKB_USER_CONFIRMATION_REPORT", "/data/codekb/logs/user-confirmation-report.json")


def _default_user_confirmation_delivery_log() -> str:
    return os.getenv(
        "CODEKB_USER_CONFIRMATION_DELIVERY_LOG",
        "/data/codekb/state/user-confirmation-delivery.jsonl",
    )


def _default_user_confirmation_responses() -> str:
    return os.getenv(
        "CODEKB_USER_CONFIRMATION_RESPONSES",
        "/data/codekb/state/user-confirmation-responses.jsonl",
    )


def _write_p5_handoff_bundle(args: argparse.Namespace, readiness_report: dict) -> dict:
    output_dir = Path(args.output_dir)
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError(f"output-dir exists and is not a directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    env_file = str(args.env_file or os.getenv("CODEKB_ENV_FILE", "") or "/data/codekb/state/p5-secrets.env")
    api_base_url = _readiness_report_api_base_url(readiness_report, args)
    plan = build_p5_external_input_plan(
        readiness_report,
        api_base_url=api_base_url,
        env_file=env_file,
        external_state_report=_external_state_report_from_args(args, env_file),
    )
    files: list[str] = []
    external_json = output_dir / "external-inputs.json"
    external_json.write_text(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    files.append(external_json.name)
    external_md = output_dir / "external-inputs.md"
    external_md.write_text(render_p5_external_input_plan_markdown(plan), encoding="utf-8")
    files.append(external_md.name)

    integration_dir = output_dir / "integrations"
    integration_summary = export_diagnose_integration_pack(integration_dir, api_base_url=api_base_url)
    files.extend(f"integrations/{name}" for name in integration_summary["files"])
    files.append("integrations/summary.json")

    template_path = output_dir / "im-config.todo.env"
    if template_path.exists() and not args.force:
        template_report = {
            "status": "exists",
            "output": str(template_path),
            "mode": "preserved",
            "message": "Existing IM template preserved; pass --force to regenerate it.",
        }
    else:
        if template_path.exists():
            template_path.unlink()
        template_report = write_im_config_template(
            env_file=env_file,
            output_path=str(template_path),
            api_base_url=api_base_url,
        )
    files.append(template_path.name)

    readme = output_dir / "README.md"
    readme.write_text(
        _render_p5_handoff_bundle_readme(plan, env_file=env_file, template_path=template_path),
        encoding="utf-8",
    )
    files.append(readme.name)

    return {
        "status": plan["status"],
        "pending_count": plan["pending_count"],
        "readiness_status": plan["readiness_status"],
        "output_dir": str(output_dir),
        "api_base_url": api_base_url,
        "files": sorted(set(files)),
        "external_inputs_json": str(external_json),
        "external_inputs_markdown": str(external_md),
        "im_template": template_report,
        "integration_summary": integration_summary,
        "secret_values_written": False,
    }


def _render_p5_handoff_bundle_readme(plan: dict, *, env_file: str, template_path: Path) -> str:
    return "\n".join(
        [
            "# Code-KB P5 Handoff Bundle",
            "",
            "This bundle is generated from the current P5 readiness state. It contains task names, commands, template keys, and verification evidence only; it does not contain secret values.",
            "",
            "## Files",
            "",
            "- `external-inputs.md`: human-readable task plan for the remaining external inputs.",
            "- `external-inputs.json`: machine-readable task plan with the same content.",
            "- `im-config.todo.env`: server-side IM fill-in template. Fill values in this file on the server, then apply it with `diagnose-im-configure --from-template`.",
            "- `integrations/`: MCP tools, code review skill, MR card, IM entry guide, current-user auth guide, and external handoff checklist.",
            "",
            "## Current Status",
            "",
            f"- Status: `{plan['status']}`",
            f"- Readiness: `{plan['readiness_status']}`",
            f"- Pending tasks: `{plan['pending_count']}`",
            f"- Setup URL: `{plan['setup_url']}`",
            f"- Server env file: `{env_file}`",
            f"- IM template: `{template_path}`",
            "",
            "## State Snapshot",
            "",
            "Run this to inspect whether the required external evidence is already present without printing secret values:",
            "",
            "```bash",
            f"PYTHONPATH=src python3 -m codekb diagnose-p5-external-state --env-file {env_file} --im-template {template_path} --json",
            "```",
            "",
            "## Safe Apply",
            "",
            "After filling `im-config.todo.env` on the server, run:",
            "",
            "```bash",
            f"PYTHONPATH=src python3 -m codekb diagnose-im-configure --env-file {env_file} --from-template {template_path} --apply --json",
            "```",
            "",
            "Then restart the API and run:",
            "",
            "```bash",
            f"PYTHONPATH=src python3 -m codekb diagnose-p5-final-verify --env-file {env_file} --api-base-url {plan['api_base_url']} --output /data/codekb/logs/p5-final-verify-report.json --json",
            "```",
            "",
            "The final report separates `pending_required` external-input waits from `failed_required` command failures.",
            "",
        ]
    )


def _default_pending_docs_dir() -> str:
    return os.getenv("CODEKB_PENDING_DOCS_DIR", "/data/codekb/pending-docs")


def _default_publish_outbox() -> str:
    return os.getenv("CODEKB_PUBLISH_OUTBOX", "/data/codekb/outbox/wiki-publish-plan.jsonl")


def _default_publish_report() -> str:
    return os.getenv("CODEKB_PUBLISH_REPORT", "/data/codekb/logs/wiki-publish-report.json")


def _default_publish_ledger() -> str:
    return os.getenv("CODEKB_PUBLISH_LEDGER", "/data/codekb/state/wiki-publish-ledger.jsonl")


def _default_governance_report() -> str:
    return os.getenv("CODEKB_GOVERNANCE_REPORT", "/data/codekb/logs/governance-report.json")


def _default_governance_policy() -> str:
    return os.getenv("CODEKB_GOVERNANCE_POLICY", "docs/governance-policy.draft.yaml")


def _default_governance_state() -> str:
    return os.getenv("CODEKB_GOVERNANCE_STATE", "/data/codekb/state/governance-state.json")


def _default_governance_weekly_report() -> str:
    return os.getenv("CODEKB_GOVERNANCE_WEEKLY_REPORT", "/data/codekb/logs/governance-weekly-report.md")


def _default_governance_ticket_outbox() -> str:
    return os.getenv("CODEKB_GOVERNANCE_TICKET_OUTBOX", "/data/codekb/outbox/governance-ticket-plan.jsonl")


def _default_governance_ticket_report() -> str:
    return os.getenv("CODEKB_GOVERNANCE_TICKET_REPORT", "/data/codekb/logs/governance-ticket-report.json")


def _registry_owner_groups(registry_path: str) -> dict[str, str]:
    registry = load_registry(registry_path)
    return {sub_kb.id: sub_kb.owner_group for sub_kb in registry.sub_kbs}


def _diagnostic_context_from_args(args: argparse.Namespace):
    if args.context_json:
        try:
            payload = json.loads(args.context_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"context-json must be valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise SystemExit("context-json must be a JSON object")
    else:
        payload = {}

    for field in (
        "surface",
        "repo",
        "branch",
        "commit",
        "mr_id",
        "build_id",
        "job_name",
        "error_code",
        "error_text",
    ):
        value = getattr(args, field)
        if value:
            payload[field] = value

    if args.log_file:
        try:
            payload["log_excerpt"] = Path(args.log_file).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise SystemExit(f"failed to read --log-file: {exc}") from exc

    if args.tag:
        existing_tags = payload.get("tags", [])
        if isinstance(existing_tags, str):
            existing_tags = [item.strip() for item in existing_tags.split(",") if item.strip()]
        if not isinstance(existing_tags, list):
            raise SystemExit("context-json tags must be a list or comma-separated string")
        payload["tags"] = [*existing_tags, *args.tag]

    try:
        return parse_diagnostic_context(payload)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _print_diagnosis(diagnosis: DiagnosticResult) -> None:
    print(
        f"diagnosis id={diagnosis.diagnosis_id} refused={str(diagnosis.refused).lower()} "
        f"confidence={diagnosis.confidence:.3f} findings={len(diagnosis.findings)} "
        f"governance_items={len(diagnosis.related_governance_items)}"
    )
    print(f"answer_id={diagnosis.answer_id}")
    print(f"trace_id={diagnosis.trace_id}")
    if not diagnosis.context.is_empty():
        context_fields = []
        for field in ("surface", "repo", "branch", "error_code", "build_id", "mr_id"):
            value = getattr(diagnosis.context, field)
            if value:
                context_fields.append(f"{field}={value}")
        if context_fields:
            print("context " + " ".join(context_fields))
    for finding in diagnosis.findings:
        print(f"{finding.severity} {finding.finding_type} {finding.title}")
    if diagnosis.gap_candidate:
        print(
            f"gap_candidate priority={diagnosis.gap_candidate['priority']} "
            f"owner={diagnosis.gap_candidate['suggested_owner']} "
            f"event={diagnosis.gap_candidate['source_event']}"
        )
    for action in diagnosis.suggested_actions:
        print(f"action={action}")
    if diagnosis.citations:
        print("citations=" + ",".join(f"{item.docid}#{item.anchor}" for item in diagnosis.citations))


def _format_counts(counts: dict) -> str:
    return ",".join(f"{key}={value}" for key, value in sorted(counts.items())) or "-"


def _read_json_payload_arg(payload_json: str, payload_file: str, name: str) -> dict:
    if payload_json and payload_file:
        raise SystemExit(f"use either --{name}-json or --{name}-file, not both")
    if payload_file:
        raw = Path(payload_file).read_text(encoding="utf-8")
    elif payload_json:
        raw = payload_json
    else:
        raise SystemExit(f"{name} is required: use --{name}-json or --{name}-file")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{name} must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"{name} must be a JSON object")
    return payload


def _parse_key_value_args(items: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"expected key=value: {item}")
        key, value = item.split("=", 1)
        normalized_key = key.strip()
        if not normalized_key:
            raise SystemExit(f"expected non-empty key in key=value: {item}")
        parsed[normalized_key] = value.strip()
    return parsed


def _read_content_arg(content: str | None, content_file: str | None) -> str:
    if content and content_file:
        raise SystemExit("use either --content or --content-file, not both")
    if content_file:
        return Path(content_file).read_text(encoding="utf-8")
    if content:
        return content
    raise SystemExit("content is required: use --content or --content-file")


if __name__ == "__main__":
    raise SystemExit(main())
