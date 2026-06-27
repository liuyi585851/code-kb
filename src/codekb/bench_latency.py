from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .golden import load_golden_questions
from .service import OfflineKbService

DEFAULT_FIXTURES = "data/fixtures/sample_corpus.jsonl"
DEFAULT_QUESTIONS = "data/fixtures/golden_questions.md"
DEFAULT_ALIASES = "data/entity_aliases.yaml"

_PREFIX_TO_SUB_KB = {"REL": "release", "TST": "testing", "INC": "incident"}


@dataclass(frozen=True)
class LatencyBenchmark:
    samples: int
    warmup: int
    repeats: int
    queries: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    min_ms: float
    mean_ms: float

    def to_dict(self) -> dict:
        return {
            "samples": self.samples,
            "warmup": self.warmup,
            "repeats": self.repeats,
            "queries": self.queries,
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
            "p99_ms": self.p99_ms,
            "max_ms": self.max_ms,
            "min_ms": self.min_ms,
            "mean_ms": self.mean_ms,
        }


def _percentile(sorted_values: list[float], q: float) -> float:
    """对已排序列表做线性插值求分位数(q 取 [0, 100])。"""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (q / 100.0) * (len(sorted_values) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    frac = rank - low
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * frac


def _routed_queries(
    questions_path: str,
    include_prefixes: set[str] | None,
    limit: int | None,
) -> list[tuple[str, str]]:
    prefixes = include_prefixes or {"REL", "TST", "INC"}
    routed: list[tuple[str, str]] = []
    for question in load_golden_questions(questions_path):
        prefix = question.question_id.split("-", 1)[0]
        if prefix not in prefixes:
            continue
        sub_kb = _PREFIX_TO_SUB_KB.get(prefix)
        if sub_kb is None:
            continue
        routed.append((question.question, sub_kb))
    if limit is not None:
        routed = routed[:limit]
    return routed


def run_latency_benchmark(
    *,
    fixture_path: str = DEFAULT_FIXTURES,
    questions_path: str = DEFAULT_QUESTIONS,
    aliases_path: str = DEFAULT_ALIASES,
    include_prefixes: set[str] | None = None,
    top_k: int = 4,
    warmup: int = 1,
    repeats: int = 1,
    limit: int | None = None,
    output_path: str | None = None,
) -> LatencyBenchmark:
    """在 golden 题集上逐条测量 ``OfflineKbService.ask`` 的延迟。

    计时只用 ``time.perf_counter``(不掺墙钟时间和随机性),所以在 CI 里结果可复现。把
    ``fixture_path`` 指向真实服务后端,同一套测量框架就能用于端上实测。"""
    service = OfflineKbService(fixture_path=fixture_path, aliases_path=aliases_path)
    queries = _routed_queries(questions_path, include_prefixes, limit)

    for _ in range(max(warmup, 0)):
        for question, sub_kb in queries:
            service.ask(question, sub_kbs={sub_kb}, top_k=top_k)

    latencies_ms: list[float] = []
    for _ in range(max(repeats, 1)):
        for question, sub_kb in queries:
            start = time.perf_counter()
            service.ask(question, sub_kbs={sub_kb}, top_k=top_k)
            latencies_ms.append((time.perf_counter() - start) * 1000.0)

    ordered = sorted(latencies_ms)
    samples = len(ordered)
    benchmark = LatencyBenchmark(
        samples=samples,
        warmup=max(warmup, 0),
        repeats=max(repeats, 1),
        queries=len(queries),
        p50_ms=round(_percentile(ordered, 50), 3),
        p95_ms=round(_percentile(ordered, 95), 3),
        p99_ms=round(_percentile(ordered, 99), 3),
        max_ms=round(ordered[-1], 3) if ordered else 0.0,
        min_ms=round(ordered[0], 3) if ordered else 0.0,
        mean_ms=round(sum(ordered) / samples, 3) if samples else 0.0,
    )

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(
            json.dumps(benchmark.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return benchmark
