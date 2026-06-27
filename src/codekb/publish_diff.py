"""把发布操作和 Wiki 当前内容做 diff 预览。

对于 ``saveDocument`` / ``saveDocumentParts`` 这类目标,通过注入的读取客户端
(:class:`WikiClient` 协议)拉取文档当前正文,再和渲染后的正文生成一份 unified diff,
统计新增/删除行数,并给出截断后的 diff 文本预览。

没有读取客户端,或目标 docid 还是占位符 ``<copied_docid>``(副本尚未创建)时,
预览退化为全量插入(全部算新增、没有删除)。不走网络:测试里读取客户端始终是注入的。
"""

from __future__ import annotations

import difflib
from typing import Any, Optional, Protocol

from .publish import PublishOperation


DIFF_PREVIEW_MAX_LINES = 200
DIFFABLE_TOOLS = {"saveDocument", "saveDocumentParts"}
PLACEHOLDER_DOCID = "<copied_docid>"


class DocumentReader(Protocol):
    def get_document(self, docid: str) -> str: ...


def publish_diff(
    operation: PublishOperation,
    rendered_body: str,
    *,
    read_client: Optional[DocumentReader] = None,
) -> dict[str, Any]:
    tool = operation.tool
    new_body = str(rendered_body or "")
    if tool not in DIFFABLE_TOOLS:
        return {
            "tool": tool,
            "mode": "not_applicable",
            "docid": "",
            "added": 0,
            "removed": 0,
            "diff": "",
        }

    docid = str(operation.params.get("docid") or operation.params.get("id") or "").strip()

    if read_client is None or not docid or docid == PLACEHOLDER_DOCID:
        return _full_insert_preview(tool, docid, new_body)

    try:
        current_body = str(read_client.get_document(docid) or "")
    except Exception as exc:  # pragma: no cover - 对接真实读取客户端时的防御性兜底
        preview = _full_insert_preview(tool, docid, new_body)
        preview["mode"] = "full_insert_read_error"
        preview["detail"] = f"{exc.__class__.__name__}: {exc}"
        return preview

    diff_lines = list(
        difflib.unified_diff(
            current_body.splitlines(),
            new_body.splitlines(),
            fromfile=f"wiki:{docid}",
            tofile="rendered_body",
            lineterm="",
        )
    )
    added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
    return {
        "tool": tool,
        "mode": "diff",
        "docid": docid,
        "added": added,
        "removed": removed,
        "diff": "\n".join(diff_lines[:DIFF_PREVIEW_MAX_LINES]),
    }


def _full_insert_preview(tool: str, docid: str, new_body: str) -> dict[str, Any]:
    lines = new_body.splitlines()
    diff_lines = [f"+{line}" for line in lines]
    return {
        "tool": tool,
        "mode": "full_insert",
        "docid": docid,
        "added": len(lines),
        "removed": 0,
        "diff": "\n".join(diff_lines[:DIFF_PREVIEW_MAX_LINES]),
    }
