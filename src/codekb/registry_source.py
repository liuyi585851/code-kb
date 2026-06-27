from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from .models import KbRegistry, RawDocument, SourceDocConfig
from .source import SourceBundle


class SourceConnector(Protocol):
    def get_document(self, docid: str) -> RawDocument: ...

    def list_children(self, parentid: str) -> list[dict]: ...


def build_registry_source_bundle(
    registry: KbRegistry,
    connector: SourceConnector,
    *,
    include_statuses: set[str] | None = None,
) -> SourceBundle:
    statuses = include_statuses or {"pilot"}
    documents: list[RawDocument] = []
    sub_kbs: dict[str, str] = {}
    seen: set[str] = set()

    for sub_kb in registry.sub_kbs:
        if sub_kb.status not in statuses:
            continue
        for source in sub_kb.source_docs:
            if source.system != "wiki":
                continue
            if source.mode == "enumerate_children":
                _append_source_document(documents, sub_kbs, seen, connector, source, sub_kb.id)
                for child in connector.list_children(source.docid):
                    child_docid = str(child.get("docid") or child.get("contentid") or "")
                    if child_docid:
                        child_source = SourceDocConfig(
                            system="wiki",
                            docid=child_docid,
                            title=str(child.get("title", "")),
                            mode="deep_candidate",
                            priority=source.priority,
                        )
                        _append_source_document(documents, sub_kbs, seen, connector, child_source, sub_kb.id)
                continue
            _append_source_document(documents, sub_kbs, seen, connector, source, sub_kb.id)

    return SourceBundle(tuple(documents), sub_kbs)


def _append_source_document(
    documents: list[RawDocument],
    sub_kbs: dict[str, str],
    seen: set[str],
    connector: SourceConnector,
    source: SourceDocConfig,
    sub_kb_id: str,
) -> None:
    if source.docid in seen:
        return
    raw = connector.get_document(source.docid)
    raw = _ensure_indexable_body(raw, source)
    documents.append(raw)
    sub_kbs[raw.docid] = sub_kb_id
    seen.add(raw.docid)


def _ensure_indexable_body(raw: RawDocument, source: SourceDocConfig) -> RawDocument:
    body = raw.body.strip()
    if body and not _looks_like_wiki_macro_only(body):
        return raw
    metadata = dict(raw.metadata)
    metadata["p1_note"] = "metadata_only_entry"
    return replace(raw, body=_metadata_body(raw, source), metadata=metadata)


def _looks_like_wiki_macro_only(body: str) -> bool:
    return "data-macro-name" in body and len(body) < 2000


def _metadata_body(raw: RawDocument, source: SourceDocConfig) -> str:
    parent_path = raw.metadata.get("parent_path") or "未知目录"
    owner = raw.metadata.get("owner_displayname") or raw.metadata.get("owner") or "未知 owner"
    modified = raw.metadata.get("last_modified") or raw.metadata.get("page_updated_at") or "未知更新时间"
    return "\n".join(
        [
            f"# {raw.title or source.title}",
            "",
            "P1 采集注记：该 Wiki 来源当前按 metadata/目录入口处理，未抽取到可直接回答具体步骤的正文。",
            "",
            f"- docid: {raw.docid}",
            f"- source_mode: {source.mode}",
            f"- parent_path: {parent_path}",
            f"- owner: {owner}",
            f"- last_modified: {modified}",
            "",
            "回答入口类问题时可以引用该文档；回答具体操作、原因或修复步骤时，应继续检索其子文档或等待正文清洗补充。",
        ]
    )
