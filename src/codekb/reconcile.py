"""对账:在已通过的候选和待入库文档之间做只读核对。

本模块纯本地运行,不做任何**外部写入**。它把 :class:`JsonCandidateStore` 里记录的
已通过候选,和落地在 ``pending_docs_dir`` 下的 Markdown 文件逐一对照,产出:

* ``ok`` —— 已通过、且待入库文件确实存在的候选;
* ``orphan_docs`` —— 找不到对应已通过候选的待入库文件;
* ``missing_docs`` —— 已通过、但待入库文件已丢失的候选。

报告里带一个 ``sections`` 字典作为扩展点,方便日后接入别的核对面(如外部存储/索引)
而不动顶层结构。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .candidate import APPROVED_STATUS, JsonCandidateStore


def reconcile_candidates(
    store: JsonCandidateStore, pending_docs_dir: str | Path | None = None
) -> dict[str, Any]:
    # 默认用 store 自带的待入库目录,免得调用方把同一个目录传两遍(还可能传错)。
    if pending_docs_dir is None:
        pending_docs_dir = store.pending_docs_dir
    pending_docs_dir = Path(pending_docs_dir)
    state = store._read_state()
    approved = [item for item in state["candidates"] if item.status == APPROVED_STATUS]

    # 已通过候选以 (sub_kb_id, candidate_id) 为键 —— 和 JsonCandidateStore 落地
    # <sub_kb_id>/<candidate_id>.md 时用的布局一致。
    expected = {(item.sub_kb_id, item.candidate_id): item for item in approved}

    actual: dict[tuple[str, str], Path] = {}
    if pending_docs_dir.exists():
        for path in sorted(pending_docs_dir.rglob("*.md")):
            if not path.is_file():
                continue
            actual[(path.parent.name, path.stem)] = path

    ok = []
    missing_docs = []
    for (sub_kb_id, candidate_id), candidate in expected.items():
        if (sub_kb_id, candidate_id) in actual:
            ok.append(
                {
                    "candidate_id": candidate_id,
                    "sub_kb_id": sub_kb_id,
                    "path": str(actual[(sub_kb_id, candidate_id)]),
                }
            )
        else:
            missing_docs.append(
                {
                    "candidate_id": candidate_id,
                    "sub_kb_id": sub_kb_id,
                    "expected_path": str(pending_docs_dir / sub_kb_id / f"{candidate_id}.md"),
                }
            )

    orphan_docs = []
    for (sub_kb_id, candidate_id), path in actual.items():
        if (sub_kb_id, candidate_id) not in expected:
            orphan_docs.append(
                {
                    "candidate_id": candidate_id,
                    "sub_kb_id": sub_kb_id,
                    "path": str(path),
                }
            )

    def _sort_key(entry: dict[str, Any]) -> tuple[str, str]:
        return (entry["sub_kb_id"], entry["candidate_id"])

    ok.sort(key=_sort_key)
    missing_docs.sort(key=_sort_key)
    orphan_docs.sort(key=_sort_key)

    return {
        "pending_docs_dir": str(pending_docs_dir),
        "approved_candidates": len(approved),
        "total_docs": len(actual),
        "counts": {
            "ok": len(ok),
            "orphan_docs": len(orphan_docs),
            "missing_docs": len(missing_docs),
        },
        "ok": ok,
        "orphan_docs": orphan_docs,
        "missing_docs": missing_docs,
        "sections": {},
    }
