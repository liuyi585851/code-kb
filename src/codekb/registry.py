from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import KbRegistry, RetrievalDefaults, SourceDocConfig, SubKbConfig


def load_registry(path: str | Path) -> KbRegistry:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("registry yaml must be a mapping")

    defaults = _parse_defaults(data.get("defaults", {}))
    sub_kbs = tuple(_parse_sub_kb(item) for item in data.get("sub_kbs", []))
    if not sub_kbs:
        raise ValueError("registry must define at least one sub_kb")

    return KbRegistry(
        version=str(data.get("version", "")),
        updated_at=str(data.get("updated_at", "")),
        status=str(data.get("status", "")),
        defaults=defaults,
        sub_kbs=sub_kbs,
    )


def _parse_defaults(data: dict[str, Any]) -> RetrievalDefaults:
    retrieval = data.get("retrieval", {})
    budget = data.get("context_budget", {})
    policy = data.get("answer_policy", {})

    return RetrievalDefaults(
        dense_top_k=int(retrieval.get("dense_top_k", 30)),
        sparse_top_k=int(retrieval.get("sparse_top_k", 30)),
        rrf_top_k=int(retrieval.get("rrf_top_k", 20)),
        rerank_top_k=int(retrieval.get("rerank_top_k", 4)),
        max_atoms=int(budget.get("max_atoms", 4)),
        max_atom_tokens=int(budget.get("max_atom_tokens", 800)),
        contextual_prefix_tokens=int(budget.get("contextual_prefix_tokens", 200)),
        citation_required=bool(policy.get("citation_required", True)),
        refuse_without_citation=bool(policy.get("refuse_without_citation", True)),
        layers_for_production_answer=tuple(data.get("layers_for_production_answer", ("L2", "L3"))),
    )


def _parse_sub_kb(data: dict[str, Any]) -> SubKbConfig:
    source_docs = tuple(
        SourceDocConfig(
            system=str(item["system"]),
            docid=str(item["docid"]),
            title=str(item["title"]),
            mode=str(item["mode"]),
            priority=str(item["priority"]),
        )
        for item in data.get("source_docs", [])
    )

    return SubKbConfig(
        id=str(data["id"]),
        name=str(data["name"]),
        owner_group=str(data.get("owner_group", "")),
        status=str(data.get("status", "")),
        description=str(data.get("description", "")),
        source_docs=source_docs,
    )

