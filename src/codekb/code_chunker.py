"""结构感知的代码切分器,产出能自解释的代码原子。

目标(对应本模块职责):KB 返回的代码片段要让*客户端*模型自己就能看懂。所以每个分块
做到两点:(a) 自成一体 —— 在定义边界(函数/类/标题)处切分,小块按预算往上拼,
超大函数按窗口带重叠拆开,尽量不从语句中间断开;(b) 自解释 —— 每个分块文本开头都带
一行出处头 ``« 代码大仓 · <repo> · <path>:L<a>-<b> · <lang> · <symbol> »``,位置信息
同时写进现有字段(source_docid/source_anchor/section_path),让
code_location.parse_code_location 能还原出精确的 file:line —— 无需新增字段。

本模块同时也是入库来源:``walk_repo`` 会遍历整个工作区产出 AtomDraft,剔除
第三方/构建/缓存目录,跳过二进制、压缩混淆和超大文件。
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator

from .chunker import Chunker
from .code_location import LANG_BY_EXT, language_for_path
from .models import AtomDraft, NormalizedDocument, NormalizedSection

DEFAULT_SUB_KB = "code"
DOC_SUB_KB = "docs"

# Markdown 是知识正文,走文档管线(docs);其余按代码处理。
MD_EXT = {".md"}
CODE_EXT = set(LANG_BY_EXT) - {".md", ".txt"}  # .txt 是数据噪声,跳过

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def _slug(text: str) -> str:
    return re.sub(r"\s+", "-", text.strip())[:80]


def _markdown_document(repo: str, rel_path: str, text: str) -> NormalizedDocument:
    """把仓库里的 markdown 文件解析成 NormalizedDocument(按标题分节)。"""
    title = rel_path.rsplit("/", 1)[-1]
    sections: list[NormalizedSection] = []
    stack: list[tuple[int, str]] = []
    headtrail: tuple[str, ...] = ()
    anchor = ""
    body: list[str] = []
    first_h1: str | None = None

    def flush() -> None:
        joined = "\n".join(body).strip()
        if joined:
            path = headtrail or (title,)
            sections.append(NormalizedSection(path=path, anchor=anchor or _slug(title), text=joined))

    for line in text.split("\n"):
        match = _HEADING_RE.match(line)
        if match:
            flush()
            body = []
            level = len(match.group(1))
            heading = match.group(2).strip()
            if first_h1 is None and level == 1:
                first_h1 = heading
            stack = [(lvl, txt) for lvl, txt in stack if lvl < level]
            stack.append((level, heading))
            headtrail = tuple(txt for _, txt in stack)
            anchor = _slug(heading)
        else:
            body.append(line)
    flush()
    if not sections:
        sections = [NormalizedSection(path=(title,), anchor=_slug(title), text=text.strip())]
    return NormalizedDocument(docid=f"{repo}/{rel_path}", title=first_h1 or title, sections=tuple(sections))


_MD_MAX_BYTES = 150 * 1024  # 跳过超大(通常是生成的)markdown
_MD_MAX_CHUNKS = 40  # 限制单文件的文档分块数,免得一张大表撑爆索引


def _is_data_table_markdown(text: str) -> bool:
    """生成的配置/资源/图数据导出基本都是 markdown 表格,不算知识正文。"""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    body = [ln for ln in lines if not ln.startswith("#")]
    if len(body) < 20:
        return False
    pipes = sum(1 for ln in body if ln.startswith("|") or ln.startswith("+--") or ln.count("|") >= 3)
    return pipes / len(body) > 0.6


def markdown_to_drafts(repo: str, rel_path: str, text: str, *, sub_kb: str = DOC_SUB_KB) -> list[AtomDraft]:
    """把仓库的 markdown 文件按知识(文档管线)切分,而不是按代码。

    跳过超大或以表格为主的生成式 markdown(配置表、资源报表之类的导出),并限制单文件
    分块数,避免一篇大文档淹没索引。
    """
    if len(text.encode("utf-8", errors="ignore")) > _MD_MAX_BYTES or _is_data_table_markdown(text):
        return []
    doc = _markdown_document(repo, rel_path.replace("\\", "/"), text)
    drafts = Chunker(min_chars=400, max_chars=1600).chunk(doc, sub_kb)
    return drafts[:_MD_MAX_CHUNKS]

# --- 边界检测(策略偏保守:宁可漏检,再靠窗口拆分兜底)---
_MD = re.compile(r"^#{1,6}\s")
_PY = re.compile(r"^\s{0,16}(async\s+def\s|def\s|class\s)")
_LUA = re.compile(
    r"^\s{0,12}(local\s+function\s|function[\s(])"
    r"|^\s{0,12}[\w.:\[\]\"']+\s*=\s*function[\s(]"
)
_GO = re.compile(r"^\s{0,4}func\s")
_CLIKE_TYPE = re.compile(
    r"^\s{0,6}(?:export\s+|default\s+|public\s+|private\s+|protected\s+|internal\s+"
    r"|abstract\s+|sealed\s+|static\s+|final\s+|partial\s+)*"
    r"(class|struct|enum|interface|namespace|record|module)\s+\w"
)
_CLIKE_FUNC = re.compile(
    r"^\s{0,6}[\w<>\[\]\*&,:\s~]*\b[A-Za-z_]\w*\s*\([^;{}]*\)\s*"
    r"(?:const\s*)?(?:noexcept\s*)?(?:override\s*)?(?:->[^{};]+)?(?::\s*[^{};]+)?\{?\s*$"
)
_CTRL = re.compile(r"^\s*(if|for|while|switch|catch|return|else|do|case|default|elif|except|with)\b")

_CLIKE = {"c", "cpp", "c-header", "csharp", "java", "typescript", "javascript"}


def _is_boundary(line: str, lang: str) -> bool:
    if not line.strip():
        return False
    if lang in {"markdown", "text"}:
        return bool(_MD.match(line))
    if lang == "python":
        return bool(_PY.match(line))
    if lang == "lua":
        return bool(_LUA.match(line))
    if lang == "go":
        return bool(_GO.match(line)) or bool(_CLIKE_TYPE.match(line))
    if lang in _CLIKE:
        if _CTRL.match(line):
            return False
        if _CLIKE_TYPE.match(line):
            return True
        return "(" in line and bool(_CLIKE_FUNC.match(line))
    return False


def _signature(line: str) -> str:
    return line.strip().rstrip("{").strip()[:160]


def chunk_code(
    text: str,
    *,
    repo: str,
    rel_path: str,
    sub_kb: str = DEFAULT_SUB_KB,
    target_lines: int = 110,
    max_seg_lines: int = 200,
    overlap: int = 12,
    max_chunks: int = 120,
) -> list[AtomDraft]:
    """把单个文件的文本切成能自解释的代码原子。"""
    rel_path = rel_path.replace("\\", "/")
    lang = language_for_path(rel_path) or "code"
    lines = text.split("\n")
    n = len(lines)
    if n == 0:
        return []

    bounds = [i for i, line in enumerate(lines) if _is_boundary(line, lang)]
    bound_set = set(bounds)
    starts = sorted({0, *bounds})
    segments: list[tuple[int, int]] = []
    for j, s in enumerate(starts):
        e = starts[j + 1] if j + 1 < len(starts) else n
        if e > s:
            segments.append((s, e))

    # 整段往上拼到目标行数;单段过大的就按窗口拆开
    chunks: list[tuple[int, int]] = []
    cs: int | None = None
    ce = 0
    for s, e in segments:
        if e - s > max_seg_lines:
            if cs is not None:
                chunks.append((cs, ce))
                cs = None
            k = s
            while k < e:
                ke = min(k + target_lines, e)
                chunks.append((k, ke))
                if ke >= e:
                    break
                k = ke - overlap
            continue
        if cs is None:
            cs, ce = s, e
        elif e - cs <= target_lines:
            ce = e
        else:
            chunks.append((cs, ce))
            cs, ce = s, e
    if cs is not None:
        chunks.append((cs, ce))

    # 每一行往前找最近的外层符号
    sig_at: list[str] = [""] * n
    cur = ""
    for i in range(n):
        if i in bound_set:
            cur = _signature(lines[i])
        sig_at[i] = cur

    docid = f"{repo}/{rel_path}"
    name = rel_path.rsplit("/", 1)[-1]
    dirs = [p for p in rel_path.split("/")[:-1] if p]

    drafts: list[AtomDraft] = []
    for s, e in chunks[:max_chunks]:
        body = "\n".join(lines[s:e]).strip("\n")
        if len(body.strip()) < 12:
            continue
        symbol = sig_at[s] if s < n else ""
        start_no, end_no = s + 1, e
        header_bits = ["代码大仓", repo, f"{rel_path}:L{start_no}-{end_no}", lang]
        if symbol:
            header_bits.append(symbol)
        header = "« " + " · ".join(header_bits) + " »"
        section = [repo, *dirs]
        if symbol:
            section.append(symbol)
        ctx = f"代码大仓 仓库 {repo} 文件 {rel_path} 第 {start_no}-{end_no} 行 语言 {lang}"
        if symbol:
            ctx += f" 符号 {symbol}"
        drafts.append(
            AtomDraft(
                sub_kb_id=sub_kb,
                source_docid=docid,
                source_title=name,
                source_anchor=f"{docid}#L{start_no}-{end_no}",
                section_path=tuple(section),
                text=header + "\n" + body,
                contextual_prefix=ctx,
            )
        )
    return drafts


# --- 仓库遍历 / 入库来源 ---------------------------------------------

INCLUDE_EXT = set(LANG_BY_EXT)
EXCLUDE_DIRS = {
    ".git", "node_modules", "third_party", "thirdparty", "vendor", "build",
    "dist", "obj", ".vs", ".idea", "__pycache__", "intermediate",
    "binaries", "deriveddatacache", "saved", ".cache", "site-packages", ".venv",
    # AI 工具的临时目录/草稿/备份 —— 绝不是知识。
    ".claude", "tmp", "temp", "backup", "backups", ".trash",
}
MAX_BYTES = 256 * 1024


def _looks_minified(lines: list[str]) -> bool:
    if not lines:
        return True
    longest = max((len(x) for x in lines), default=0)
    avg = sum(len(x) for x in lines) / len(lines)
    return longest > 2000 or avg > 300


def _read_text(path: str) -> str | None:
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    if b"\x00" in raw[:4096]:
        return None
    text = raw.decode("utf-8", errors="replace")
    if _looks_minified(text.splitlines()):
        return None
    return text


def walk_repo(
    root: str, *, code_sub_kb: str = DEFAULT_SUB_KB, doc_sub_kb: str = DOC_SUB_KB
) -> Iterator[AtomDraft]:
    """遍历整个工作区产出原子,按文件类型分流。

    Markdown 走文档管线(``doc_sub_kb``);代码走结构感知切分器(``code_sub_kb``);
    ``.txt`` 和非源码文件跳过。``root`` 下的第一段路径就是子仓库 id(``<repo>/<path>``)。
    """
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in EXCLUDE_DIRS]
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            is_md = ext in MD_EXT
            if not is_md and ext not in CODE_EXT:
                continue
            if fname.endswith((".min.js", ".min.css")):
                continue
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, root).replace("\\", "/")
            parts = rel.split("/")
            if len(parts) > 1:
                repo, rel_path = parts[0], "/".join(parts[1:])
            else:
                repo, rel_path = "_root", parts[0]
            text = _read_text(full)
            if text is None:
                continue
            if is_md:
                yield from markdown_to_drafts(repo, rel_path, text, sub_kb=doc_sub_kb)
            else:
                yield from chunk_code(text, repo=repo, rel_path=rel_path, sub_kb=code_sub_kb)
