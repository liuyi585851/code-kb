from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from .governance import GovernanceReport, GovernanceTicketPlan
from .postgres import governance_item_upsert, governance_ticket_plan_upsert


def export_governance_artifacts(
    report: GovernanceReport,
    plans: Sequence[GovernanceTicketPlan],
    output_dir: str | Path,
) -> dict[str, int]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    plans = tuple(plans)
    _write_jsonl(output / "governance_items.jsonl", [item.to_dict() for item in report.items])
    _write_jsonl(output / "governance_ticket_plans.jsonl", [plan.to_dict() for plan in plans])
    _write_jsonl(output / "postgres_upserts.jsonl", _postgres_payloads(report, plans))
    summary = {
        "governance_items": len(report.items),
        "governance_ticket_plans": len(plans),
        "postgres_upserts": len(report.items) + len(plans),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def _postgres_payloads(report: GovernanceReport, plans: tuple[GovernanceTicketPlan, ...]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for item in report.items:
        stmt = governance_item_upsert(item, first_seen_at=report.generated_at, last_seen_at=report.generated_at)
        payloads.append({"target": "governance_items", "sql": stmt.sql, "params": list(stmt.params)})
    for plan in plans:
        stmt = governance_ticket_plan_upsert(plan)
        payloads.append({"target": "governance_ticket_plans", "sql": stmt.sql, "params": list(stmt.params)})
    return payloads


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=_json_default) + "\n")


def _json_default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return str(value)
