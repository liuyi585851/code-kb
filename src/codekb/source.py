from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import RawDocument


@dataclass(frozen=True)
class SourceBundle:
    documents: tuple[RawDocument, ...]
    sub_kbs: dict[str, str]


class FixtureSourceConnector:
    """从 JSONL 读取 RawDocument 测试数据,用于离线 P1 评测。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._docs = self._load(self.path)

    def get_document(self, docid: str) -> RawDocument:
        try:
            return self._docs[docid]
        except KeyError as exc:
            raise KeyError(f"fixture doc not found: {docid}") from exc

    def list_documents(self) -> list[RawDocument]:
        return list(self._docs.values())

    @staticmethod
    def _load(path: Path) -> dict[str, RawDocument]:
        bundle = _load_jsonl_bundle(path)
        return {doc.docid: doc for doc in bundle.documents}


class ManifestSourceConnector:
    """从 YAML 清单加配套的 Markdown 原文读取 RawDocument 测试数据。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._bundle = _load_manifest_bundle(self.path)
        self._docs = {doc.docid: doc for doc in self._bundle.documents}

    def get_document(self, docid: str) -> RawDocument:
        try:
            return self._docs[docid]
        except KeyError as exc:
            raise KeyError(f"manifest doc not found: {docid}") from exc

    def list_documents(self) -> list[RawDocument]:
        return list(self._bundle.documents)

    def sub_kbs(self) -> dict[str, str]:
        return dict(self._bundle.sub_kbs)


class PendingDocsSourceConnector:
    """从待入库文档目录读取已审核通过的候选 Markdown 文件。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._bundle = _load_pending_docs_bundle(self.path)
        self._docs = {doc.docid: doc for doc in self._bundle.documents}

    def get_document(self, docid: str) -> RawDocument:
        try:
            return self._docs[docid]
        except KeyError as exc:
            raise KeyError(f"pending doc not found: {docid}") from exc

    def list_documents(self) -> list[RawDocument]:
        return list(self._bundle.documents)

    def sub_kbs(self) -> dict[str, str]:
        return dict(self._bundle.sub_kbs)


def load_source_bundle(path: str | Path) -> SourceBundle:
    source_path = Path(path)
    if not source_path.exists():
        # 尚未创建的源(比如首次审核前空的待入库目录)只是不贡献任何文档,
        # 而不是用 FileNotFoundError 把索引重建搞崩。
        return SourceBundle((), {})
    if source_path.is_dir():
        return _load_pending_docs_bundle(source_path)
    if source_path.suffix.lower() in {".yaml", ".yml"}:
        return _load_manifest_bundle(source_path)
    return _load_jsonl_bundle(source_path)


def load_combined_source_bundle(paths: tuple[str | Path, ...] | list[str | Path]) -> SourceBundle:
    docs: list[RawDocument] = []
    sub_kbs: dict[str, str] = {}
    seen: set[str] = set()
    for source_path in paths:
        bundle = load_source_bundle(source_path)
        for doc in bundle.documents:
            if doc.docid in seen:
                raise ValueError(f"duplicate source docid {doc.docid} across source bundles")
            seen.add(doc.docid)
            docs.append(doc)
            sub_kbs[doc.docid] = bundle.sub_kbs[doc.docid]
    return SourceBundle(tuple(docs), sub_kbs)


def load_fixture_sub_kbs(path: str | Path) -> dict[str, str]:
    return load_source_bundle(path).sub_kbs


def _load_jsonl_bundle(path: Path) -> SourceBundle:
    docs: list[RawDocument] = []
    sub_kbs: dict[str, str] = {}
    mapping: dict[str, str] = {}
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        data = json.loads(line)
        doc = _raw_document_from_mapping(data, body=str(data["body"]))
        if doc.docid in mapping:
            raise ValueError(f"duplicate fixture docid {doc.docid} at {path}:{line_no}")
        mapping[doc.docid] = doc.docid
        docs.append(doc)
        sub_kbs[doc.docid] = str(data["sub_kb_id"])
    return SourceBundle(tuple(docs), sub_kbs)


def _load_manifest_bundle(path: Path) -> SourceBundle:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    documents = data.get("documents")
    if not isinstance(documents, list):
        raise ValueError(f"manifest must contain documents list: {path}")

    docs: list[RawDocument] = []
    sub_kbs: dict[str, str] = {}
    seen: set[str] = set()
    for index, item in enumerate(documents, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"manifest document #{index} must be a mapping: {path}")
        docid = str(item["docid"])
        if docid in seen:
            raise ValueError(f"duplicate manifest docid {docid} at {path}")
        seen.add(docid)
        body_path = path.parent / str(item["body_path"])
        body = body_path.read_text(encoding="utf-8")
        docs.append(_raw_document_from_mapping(item, body=body))
        sub_kbs[docid] = str(item["sub_kb_id"])
    return SourceBundle(tuple(docs), sub_kbs)


def _load_pending_docs_bundle(path: Path) -> SourceBundle:
    if not path.exists():
        return SourceBundle((), {})
    if not path.is_dir():
        raise ValueError(f"pending docs source must be a directory: {path}")

    docs: list[RawDocument] = []
    sub_kbs: dict[str, str] = {}
    seen: set[str] = set()
    for md_path in sorted(path.rglob("*.md")):
        if md_path.name.startswith("."):
            continue
        raw_text = md_path.read_text(encoding="utf-8")
        metadata, body = _split_front_matter(raw_text)
        sub_kb_id = str(metadata.get("sub_kb_id") or md_path.parent.name).strip()
        candidate_id = str(metadata.get("candidate_id") or md_path.stem).strip()
        if not sub_kb_id:
            raise ValueError(f"pending doc missing sub_kb_id: {md_path}")
        if not candidate_id:
            raise ValueError(f"pending doc missing candidate_id: {md_path}")
        docid = candidate_id
        if docid in seen:
            raise ValueError(f"duplicate pending docid {docid}: {md_path}")
        seen.add(docid)
        title = str(metadata.get("title") or _extract_markdown_title(body) or md_path.stem).strip()
        normalized_metadata = {
            **metadata,
            "system": "pending",
            "candidate_id": candidate_id,
            "pending_doc_path": str(md_path),
            "source_acl_hash": metadata.get("source_acl_hash", ""),
            "last_modified": metadata.get("approved_at", ""),
        }
        docs.append(
            RawDocument(
                docid=docid,
                title=title,
                content_type="MD",
                body=body.strip(),
                url=f"pending://{sub_kb_id}/{candidate_id}",
                metadata=normalized_metadata,
            )
        )
        sub_kbs[docid] = sub_kb_id
    return SourceBundle(tuple(docs), sub_kbs)


def _raw_document_from_mapping(data: dict, *, body: str) -> RawDocument:
    return RawDocument(
        docid=str(data["docid"]),
        title=str(data["title"]),
        content_type=str(data.get("content_type", "DOC")),
        body=body,
        url=str(data.get("url", "")),
        metadata=dict(data.get("metadata", {})),
    )


def _split_front_matter(text: str) -> tuple[dict, str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            metadata = yaml.safe_load("\n".join(lines[1:index])) or {}
            if not isinstance(metadata, dict):
                raise ValueError("pending doc front matter must be a mapping")
            return metadata, "\n".join(lines[index + 1 :]).strip()
    return {}, text


def _extract_markdown_title(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""
