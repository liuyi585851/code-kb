from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class FeedbackRecord:
    feedback_id: str
    answer_id: str
    trace_id: str
    rating: int
    reason: str
    user_id_hash: str
    corrected_answer: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "feedback_id": self.feedback_id,
            "answer_id": self.answer_id,
            "trace_id": self.trace_id,
            "rating": self.rating,
            "reason": self.reason,
            "user_id_hash": self.user_id_hash,
            "corrected_answer": self.corrected_answer,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class FeedbackSummary:
    total: int
    positive: int
    neutral: int
    negative: int
    corrected: int
    invalid_lines: int
    negative_rate: float
    corrected_rate: float
    latest_created_at: str
    by_answer_id: tuple[dict[str, Any], ...]
    badcases: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "positive": self.positive,
            "neutral": self.neutral,
            "negative": self.negative,
            "corrected": self.corrected,
            "invalid_lines": self.invalid_lines,
            "negative_rate": self.negative_rate,
            "corrected_rate": self.corrected_rate,
            "latest_created_at": self.latest_created_at,
            "by_answer_id": list(self.by_answer_id),
            "badcases": list(self.badcases),
        }


class JsonlFeedbackStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(
        self,
        *,
        answer_id: str,
        trace_id: str,
        rating: int,
        reason: str = "",
        user_id_hash: str = "",
        corrected_answer: str = "",
    ) -> FeedbackRecord:
        record = FeedbackRecord(
            feedback_id=str(uuid4()),
            answer_id=answer_id,
            trace_id=trace_id,
            rating=rating,
            reason=reason,
            user_id_hash=user_id_hash,
            corrected_answer=corrected_answer,
            created_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        return record


def load_feedback_records(path: str | Path) -> tuple[FeedbackRecord, ...]:
    records, _ = _read_feedback_records(path)
    return records


def summarize_feedback(path: str | Path, *, badcase_limit: int = 20) -> FeedbackSummary:
    records, invalid_lines = _read_feedback_records(path)
    total = len(records)
    rating_counts = Counter(record.rating for record in records)
    corrected = sum(1 for record in records if record.corrected_answer)
    badcases = tuple(
        record.to_dict()
        for record in sorted(
            (item for item in records if item.rating < 0 or item.corrected_answer),
            key=lambda item: item.created_at,
            reverse=True,
        )[: max(0, badcase_limit)]
    )
    latest_created_at = max((record.created_at for record in records), default="")
    return FeedbackSummary(
        total=total,
        positive=rating_counts[1],
        neutral=rating_counts[0],
        negative=rating_counts[-1],
        corrected=corrected,
        invalid_lines=invalid_lines,
        negative_rate=round(rating_counts[-1] / total, 3) if total else 0.0,
        corrected_rate=round(corrected / total, 3) if total else 0.0,
        latest_created_at=latest_created_at,
        by_answer_id=_summarize_by_answer(records),
        badcases=badcases,
    )


def parse_feedback_payload(payload: dict[str, Any]) -> dict[str, Any]:
    answer_id = str(payload.get("answer_id", "")).strip()
    trace_id = str(payload.get("trace_id", "")).strip()
    if not answer_id:
        raise ValueError("answer_id is required")
    if not trace_id:
        raise ValueError("trace_id is required")
    try:
        rating = int(payload.get("rating"))
    except (TypeError, ValueError) as exc:
        raise ValueError("rating must be -1, 0, or 1") from exc
    if rating not in {-1, 0, 1}:
        raise ValueError("rating must be -1, 0, or 1")
    return {
        "answer_id": answer_id,
        "trace_id": trace_id,
        "rating": rating,
        "reason": str(payload.get("reason", "")).strip(),
        "user_id_hash": str(payload.get("user_id_hash", "")).strip(),
        "corrected_answer": str(payload.get("corrected_answer", "")).strip(),
    }


def _read_feedback_records(path: str | Path) -> tuple[tuple[FeedbackRecord, ...], int]:
    feedback_path = Path(path)
    if not feedback_path.exists():
        return (), 0
    records: list[FeedbackRecord] = []
    invalid_lines = 0
    for line in feedback_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(_feedback_record_from_dict(json.loads(line)))
        except (TypeError, ValueError, json.JSONDecodeError):
            invalid_lines += 1
    return tuple(records), invalid_lines


def _feedback_record_from_dict(payload: dict[str, Any]) -> FeedbackRecord:
    rating = int(payload.get("rating"))
    if rating not in {-1, 0, 1}:
        raise ValueError("rating must be -1, 0, or 1")
    answer_id = str(payload.get("answer_id", "")).strip()
    trace_id = str(payload.get("trace_id", "")).strip()
    if not answer_id or not trace_id:
        raise ValueError("answer_id and trace_id are required")
    return FeedbackRecord(
        feedback_id=str(payload.get("feedback_id", "")).strip(),
        answer_id=answer_id,
        trace_id=trace_id,
        rating=rating,
        reason=str(payload.get("reason", "")).strip(),
        user_id_hash=str(payload.get("user_id_hash", "")).strip(),
        corrected_answer=str(payload.get("corrected_answer", "")).strip(),
        created_at=str(payload.get("created_at", "")).strip(),
    )


def _summarize_by_answer(records: tuple[FeedbackRecord, ...]) -> tuple[dict[str, Any], ...]:
    summary: dict[str, dict[str, Any]] = {}
    for record in records:
        item = summary.setdefault(
            record.answer_id,
            {
                "answer_id": record.answer_id,
                "total": 0,
                "positive": 0,
                "neutral": 0,
                "negative": 0,
                "corrected": 0,
                "latest_trace_id": "",
                "latest_created_at": "",
            },
        )
        item["total"] += 1
        if record.rating > 0:
            item["positive"] += 1
        elif record.rating < 0:
            item["negative"] += 1
        else:
            item["neutral"] += 1
        if record.corrected_answer:
            item["corrected"] += 1
        if record.created_at >= item["latest_created_at"]:
            item["latest_trace_id"] = record.trace_id
            item["latest_created_at"] = record.created_at

    return tuple(
        sorted(
            summary.values(),
            key=lambda item: (item["negative"], item["corrected"], item["latest_created_at"]),
            reverse=True,
        )
    )
