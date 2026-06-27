"""从原子的现有字段里解析代码位置信息(仓库 / 路径 / 行号区间 / 语言 / 符号),
无需新增存储字段。

代码原子(由 :mod:`code_chunker` 产出)把位置完全编码在各存储已经持久化的字段里:
- ``source_docid``  = ``<repo>/<repo-relative-path>``           例如 ``AIKnowledge/Source/Weapon/Weapon.lua``
- ``source_anchor`` = ``<source_docid>#L<start>-<end>``         例如 ``...Weapon.lua#L120-178``
- ``section_path``  = ``(repo, dir, ..., symbol?)``

这样引用 / MCP 工具不用任何表结构迁移,就能为客户端模型还原出精确的 ``file:line``。
非代码原子(没有 ``#L`` 标记)返回 ``None``,现有的文档引用行为保持不变。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

_ANCHOR_RE = re.compile(r"#L(\d+)-(\d+)\s*$")

LANG_BY_EXT = {
    ".py": "python",
    ".lua": "lua",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "c",
    ".h": "c-header",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".java": "java",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".md": "markdown",
    ".txt": "text",
}


@dataclass(frozen=True)
class CodeLocation:
    repo_id: str
    file_path: str  # 仓库相对完整路径,含仓库段在内
    start_line: int
    end_line: int
    language: str
    qualified_symbol: str = ""


def language_for_path(path: str) -> str:
    return LANG_BY_EXT.get(os.path.splitext(path)[1].lower(), "")


def parse_code_location(draft: object) -> CodeLocation | None:
    """从类 draft 对象还原代码位置;不是代码就返回 ``None``。"""
    anchor = str(getattr(draft, "source_anchor", "") or "")
    match = _ANCHOR_RE.search(anchor)
    if not match:
        return None
    docid = str(getattr(draft, "source_docid", "") or "").replace("\\", "/")
    if not docid:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    repo_id = docid.split("/", 1)[0]
    language = language_for_path(docid)
    symbol = ""
    section_path = tuple(getattr(draft, "section_path", ()) or ())
    if section_path:
        last = str(section_path[-1])
        # 切分器会把外层符号作为 section_path 的最后一项追加进去,
        # 这里要把它和普通目录段区分开。
        if "(" in last or ":" in last or last not in docid.split("/"):
            symbol = last
    return CodeLocation(
        repo_id=repo_id,
        file_path=docid,
        start_line=start,
        end_line=end,
        language=language,
        qualified_symbol=symbol,
    )
