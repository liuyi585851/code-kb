from __future__ import annotations

from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from .candidate import JsonCandidateStore
from .index_rebuild import rebuild_search_index
from .publish import build_publish_plans, process_publish_outbox, write_publish_outbox
from .service import OfflineKbService


DEFAULT_P3_SMOKE_TITLE = "LETGOKB_E2E_RULE_20260616"
DEFAULT_P3_SMOKE_CONTENT = (
    "LETGOKB_E2E_RULE_20260616 表示端到端验收知识：提交后审核通过，"
    "应该进入 pending docs、本地索引、问答引用和发布 outbox。"
)


def run_p3_usecase_smoke(
    *,
    work_dir: str | Path | None = None,
    fixture_path: str | Path = "data/fixtures/sample_corpus.jsonl",
    aliases_path: str | Path = "data/entity_aliases.yaml",
    publish_mode: str = "manual",
    index_docid: str = "",
    template_docid: str = "",
    target_parentid: str = "",
) -> dict[str, Any]:
    root = Path(work_dir) if work_dir else Path(mkdtemp(prefix="codekb-p3-usecase-"))
    paths = _smoke_paths(root)
    paths["pending_docs"].mkdir(parents=True, exist_ok=True)
    paths["logs"].mkdir(parents=True, exist_ok=True)

    store = JsonCandidateStore(paths["candidate_store"], pending_docs_dir=paths["pending_docs"])
    submission = store.submit(
        sub_kb_id="testing",
        title=DEFAULT_P3_SMOKE_TITLE,
        content=DEFAULT_P3_SMOKE_CONTENT,
        source_type="manual",
        source_ref="p3-usecase-smoke",
        submitted_by_hash="p3-usecase-smoke",
        allow_duplicate=True,
    )
    audit = store.audit(
        submission.candidate.candidate_id,
        action="approve",
        reviewer_hash="p3-usecase-smoke",
        comment="p3 usecase smoke approve",
    )
    index_rebuild = rebuild_search_index(
        fixture_path=fixture_path,
        db_path=paths["index_db"],
        include_paths=[paths["pending_docs"]],
        atomic=True,
    )
    service = OfflineKbService(
        fixture_path=str(fixture_path),
        aliases_path=str(aliases_path),
        index_db_path=str(paths["index_db"]),
        trace_log_path=str(paths["trace_log"]),
    )
    answer = service.ask(f"{DEFAULT_P3_SMOKE_TITLE} 是什么？", sub_kbs={"testing"}, top_k=4)

    plans = build_publish_plans(
        paths["pending_docs"],
        mode=publish_mode,
        index_docid=index_docid,
        template_docid=template_docid,
        target_parentid=target_parentid,
        limit=20,
    )
    written = write_publish_outbox(plans, paths["publish_outbox"])
    publish_report = process_publish_outbox(
        paths["publish_outbox"],
        execute=False,
        write_enabled=False,
        limit=20,
        report_path=paths["publish_report"],
        ledger_path=paths["publish_ledger"],
    )

    checks = {
        "ingest_accepted": submission.duplicate is False,
        "audit_approved": audit.candidate.status == "approved",
        "index_rebuilt": index_rebuild["status"] == "rebuilt",
        "ask_answered": not answer.refused,
        "ask_cites_smoke_doc": any(citation.title == DEFAULT_P3_SMOKE_TITLE for citation in answer.citations),
        "publish_outbox_written": written >= 1,
        "publish_report_validated": publish_report.status == "validated",
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "paths": {key: str(value) for key, value in paths.items()},
        "ingest": {
            "status": "duplicate" if submission.duplicate else "accepted",
            "candidate_id": submission.candidate.candidate_id,
        },
        "audit": {
            "status": audit.candidate.status,
            "output_path": audit.audit.output_path,
        },
        "index_rebuild": index_rebuild,
        "ask": {
            "refused": answer.refused,
            "confidence": answer.confidence,
            "citation_titles": [citation.title for citation in answer.citations],
            "citation_docids": [citation.docid for citation in answer.citations],
        },
        "publish": {
            "mode": publish_mode,
            "written": written,
            "process_status": publish_report.status,
            "processed": publish_report.processed,
            "blocked_operations": publish_report.blocked_operations,
        },
    }


def _smoke_paths(root: Path) -> dict[str, Path]:
    return {
        "root": root,
        "candidate_store": root / "state" / "candidates.json",
        "pending_docs": root / "pending-docs",
        "index_db": root / "local-index" / "codekb.sqlite3",
        "trace_log": root / "logs" / "ask-trace.jsonl",
        "publish_outbox": root / "outbox" / "wiki-publish-plan.jsonl",
        "publish_report": root / "logs" / "wiki-publish-report.json",
        "publish_ledger": root / "state" / "wiki-publish-ledger.jsonl",
        "logs": root / "logs",
    }
