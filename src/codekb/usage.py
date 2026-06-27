"""轻量级用量埋点 —— 把每次 KB 工具/MCP/web 调用记成一条 JSONL 事件,
再聚合给可观测性看板用。只追加写文件,不动库表结构。

需手动开启:设置 CODEKB_USAGE_LOG(否则 record_event 直接空跑)。尽力而为,
绝不向请求链路抛异常。
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def usage_log_path() -> str:
    return os.getenv("CODEKB_USAGE_LOG", "").strip()


def record_event(
    tool: str,
    *,
    source: str = "http",
    query: str = "",
    results: int | None = None,
    latency_ms: float | None = None,
    refused: bool | None = None,
    ok: bool = True,
    path: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """追加一条用量事件。没配日志路径时直接空跑。"""
    target = path or usage_log_path()
    if not target:
        return
    event: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "tool": tool,
        "source": source,
        "ok": ok,
    }
    if query:
        event["query"] = query[:200]
    if results is not None:
        event["results"] = int(results)
    if latency_ms is not None:
        event["latency_ms"] = round(float(latency_ms), 1)
    if refused is not None:
        event["refused"] = bool(refused)
    if extra:
        event.update(extra)
    try:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - 埋点绝不能拖垮请求
        pass


def _read_events(target: str, *, max_events: int = 20000) -> list[dict[str, Any]]:
    if not target or not Path(target).exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        with open(target, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except Exception:  # noqa: BLE001 - 跳过损坏的行
                    continue
    except Exception:  # noqa: BLE001
        return events
    return events[-max_events:]


def summarize_usage(target: str | None = None, *, limit_recent: int = 50) -> dict[str, Any]:
    """聚合用量事件,供看板展示。"""
    target = target or usage_log_path()
    events = _read_events(target)
    by_tool: dict[str, dict[str, Any]] = {}
    by_source: dict[str, int] = {}
    by_day: dict[str, int] = {}
    for ev in events:
        tool = str(ev.get("tool", "?"))
        slot = by_tool.setdefault(tool, {"tool": tool, "count": 0, "empty": 0, "_lat_sum": 0.0, "_lat_n": 0})
        slot["count"] += 1
        res = ev.get("results")
        if res == 0 or ev.get("refused") is True:
            slot["empty"] += 1
        lat = ev.get("latency_ms")
        if isinstance(lat, (int, float)):
            slot["_lat_sum"] += float(lat)
            slot["_lat_n"] += 1
        by_source[str(ev.get("source", "?"))] = by_source.get(str(ev.get("source", "?")), 0) + 1
        day = str(ev.get("ts", ""))[:10]
        if day:
            by_day[day] = by_day.get(day, 0) + 1
    tools = []
    for slot in by_tool.values():
        n = slot.pop("_lat_n")
        s = slot.pop("_lat_sum")
        slot["avg_latency_ms"] = round(s / n, 1) if n else None
        slot["empty_rate"] = round(slot["empty"] / slot["count"], 3) if slot["count"] else 0.0
        tools.append(slot)
    tools.sort(key=lambda t: t["count"], reverse=True)
    return {
        "configured": bool(target),
        "total": len(events),
        "by_tool": tools,
        "by_source": by_source,
        "by_day": [{"day": d, "count": c} for d, c in sorted(by_day.items())],
        "recent": list(reversed(events[-limit_recent:])),
    }
