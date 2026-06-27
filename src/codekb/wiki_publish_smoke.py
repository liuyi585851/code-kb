"""Wiki 发布的「写入后回读」冒烟测试。

通过注入的写客户端(:class:`WikiPublishClient` 协议)写一次,再通过注入的读客户端
(:class:`WikiClient` 协议 —— ``get_document`` / ``metadata``)把文档读回来,
确认标题和正文关键片段确实落库了。

两个客户端都是注入的,所以整条约定能确定性地、不碰网络地验证。真实沙箱里的
写入后回读是任务 E3b(track two),它复用这套同样的编排,只是换成实跑的客户端。
"""

from __future__ import annotations

from typing import Any, Optional, Protocol


BODY_FRAGMENT_LEN = 48


class WriteClient(Protocol):
    def save_document(
        self, *, docid: int, title: str, body: str, is_html: bool = False, raw: bool = False
    ) -> dict[str, Any]: ...


class ReadClient(Protocol):
    def metadata(self, docid: str) -> dict[str, Any]: ...

    def get_document(self, docid: str) -> str: ...


def publish_writeback_smoke(
    *,
    docid: int,
    title: str,
    body: str,
    write_client: WriteClient,
    read_client: ReadClient,
    is_html: bool = False,
    raw: bool = False,
) -> dict[str, Any]:
    write_response = write_client.save_document(
        docid=int(docid),
        title=str(title),
        body=str(body),
        is_html=bool(is_html),
        raw=bool(raw),
    )

    readback_body = str(read_client.get_document(str(docid)) or "")
    readback_meta = read_client.metadata(str(docid))
    readback_title = str((readback_meta or {}).get("title", ""))

    title_match = str(title).strip() == readback_title.strip()
    fragment = _key_fragment(str(body))
    body_match = bool(fragment) and fragment in readback_body

    if title_match and body_match:
        status = "verified"
    elif not write_response:
        status = "write_failed"
    else:
        status = "mismatch"

    return {
        "status": status,
        "docid": str(docid),
        "title_match": title_match,
        "body_match": body_match,
        "write_ok": bool(write_response),
        "checked_fragment": fragment,
        "readback_title": readback_title,
    }


def _key_fragment(body: str) -> str:
    # 优先取第一行非标题正文,这样片段断言的是实打实的正文,
    # 而不只是(已单独校验过的)标题。
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:BODY_FRAGMENT_LEN]
    for line in body.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:BODY_FRAGMENT_LEN]
    return body.strip()[:BODY_FRAGMENT_LEN]
