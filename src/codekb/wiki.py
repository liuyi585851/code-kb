from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, Protocol

from .models import RawDocument


class WikiClient(Protocol):
    def metadata(self, docid: str) -> dict[str, Any]: ...

    def get_document(self, docid: str) -> str: ...

    def list_children(self, parentid: str) -> list[dict[str, Any]]: ...


class WikiSourceConnector:
    def __init__(self, client: WikiClient) -> None:
        self.client = client

    def get_document(self, docid: str) -> RawDocument:
        metadata = self.client.metadata(docid)
        body = self.client.get_document(docid)
        return RawDocument(
            docid=str(metadata.get("contentid", docid)),
            title=str(metadata.get("title", "")),
            content_type=str(metadata.get("content_type", "DOC")),
            body=body,
            url=f"https://wiki.example.com/p/{docid}",
            metadata=_normalize_metadata(metadata),
        )

    def list_children(self, parentid: str) -> list[dict[str, Any]]:
        return self.client.list_children(parentid)


def _normalize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    parents = metadata.get("parents_obj") or []
    parent_path = " / ".join(str(item.get("title", "")) for item in parents if item.get("title"))
    acl_snapshot = {
        "spaceid": metadata.get("spaceid"),
        "spacekey": metadata.get("spacekey"),
        "can_edit": bool(metadata.get("can_edit", False)),
        "parents": metadata.get("parents", []),
        "status": metadata.get("status"),
    }
    return {
        "system": "wiki",
        "owner": metadata.get("owner"),
        "owner_displayname": metadata.get("owner_displayname"),
        "author": metadata.get("creator"),
        "last_modified": metadata.get("content_changetime") or metadata.get("updatetime"),
        "page_updated_at": metadata.get("updatetime"),
        "parent_path": parent_path,
        "can_edit": bool(metadata.get("can_edit", False)),
        "visibility": "wiki_acl_snapshot",
        "source_acl_hash": sha256(
            json.dumps(acl_snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "spacekey": metadata.get("spacekey"),
        "spaceid": metadata.get("spaceid"),
        "version": metadata.get("version"),
        "raw": metadata,
    }
