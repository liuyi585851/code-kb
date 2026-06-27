"""代码导航原语,供客户端的强模型驱动多跳检索。

KB 不负责推理代码,只返回正确、可定位、能自解释的片段,让客户端(一个强 LLM agent)
通过反复调用这些原语来导航:
- search_code : 对代码/文档原子做语义+词法检索(file:line + 片段)
- get_symbol  : 找出定义或包含某符号的原子
- read_file_range / file_outline : 按文件位置取出精确代码(原子本身就是代码文本,
  所以即便服务端没有这个仓库也照样能用)
"""

from __future__ import annotations

import re
from typing import Any

from .code_location import parse_code_location

DEFAULT_CODE_SUB_KB = "code"
# 仓库的 markdown 落在 docs 里;默认检索同时覆盖 code + docs,这样像错误码说明这类
# 正文不用调用方特意指定也能检索到。
DEFAULT_SUB_KBS = ("code", "docs")
_SNIPPET_CHARS = 2000


def _subs(sub_kbs: Any) -> set[str]:
    """归一化 sub_kbs:None 取默认;裸字符串包成 {字符串}(避免 set('code') 被拆成单个字符)。"""
    if not sub_kbs:
        return set(DEFAULT_SUB_KBS)
    if isinstance(sub_kbs, str):
        return {sub_kbs}
    return {str(s) for s in sub_kbs}


def _hit(atom: Any, score: float) -> dict[str, Any]:
    draft = atom.draft
    loc = parse_code_location(draft)
    out: dict[str, Any] = {
        "atom_id": atom.atom_id,
        "title": draft.source_title,
        "docid": draft.source_docid,
        "score": round(float(score), 4),
        "snippet": draft.text[:_SNIPPET_CHARS],
    }
    if loc:
        out.update(
            {
                "repo": loc.repo_id,
                "file_path": loc.file_path,
                "start_line": loc.start_line,
                "end_line": loc.end_line,
                "language": loc.language,
                "symbol": loc.qualified_symbol,
            }
        )
    return out


def search_code(retriever: Any, query: str, *, sub_kbs: Any = None, top_k: int = 6) -> dict[str, Any]:
    subs = _subs(sub_kbs)
    result = retriever.retrieve(query, sub_kbs=subs, top_k=top_k)
    return {
        "query": query,
        "sub_kbs": sorted(subs),
        "hits": [_hit(item.atom, item.score) for item in result.top_atoms],
    }


def get_symbol(retriever: Any, name: str, *, sub_kbs: Any = None, top_k: int = 8) -> dict[str, Any]:
    subs = _subs(sub_kbs)
    result = retriever.retrieve(name, sub_kbs=subs, top_k=top_k)
    needle = name.lower()
    matches: list[dict[str, Any]] = []
    for item in result.top_atoms:
        loc = parse_code_location(item.atom.draft)
        if loc and loc.qualified_symbol and needle in loc.qualified_symbol.lower():
            matches.append(_hit(item.atom, item.score))
    exact = bool(matches)
    if not matches:  # 没匹配到符号名,就把原始候选返回,客户端至少还有线索
        matches = [_hit(item.atom, item.score) for item in result.top_atoms]
    return {"symbol": name, "exact_symbol_match": exact, "matches": matches}


def read_file_range(store: Any, path: str, start_line: int, end_line: int) -> dict[str, Any]:
    if start_line > end_line:  # 容错:区间反了就交换
        start_line, end_line = end_line, start_line
    atoms = store.list_atoms(source_docids={path})
    spans: list[tuple[int, int, str]] = []
    for atom in atoms:
        loc = parse_code_location(atom.draft)
        if not loc:
            continue
        if loc.end_line < start_line or loc.start_line > end_line:
            continue
        spans.append((loc.start_line, loc.end_line, atom.draft.text))
    spans.sort()
    return {
        "path": path,
        "start_line": start_line,
        "end_line": end_line,
        "found": len(spans),
        "segments": [{"start_line": s, "end_line": e, "text": t} for s, e, t in spans],
    }


def file_outline(store: Any, path: str) -> dict[str, Any]:
    atoms = store.list_atoms(source_docids={path})
    items: list[dict[str, Any]] = []
    language = ""
    for atom in atoms:
        loc = parse_code_location(atom.draft)
        if not loc:
            continue
        language = language or loc.language
        items.append(
            {
                "start_line": loc.start_line,
                "end_line": loc.end_line,
                "symbol": loc.qualified_symbol,
            }
        )
    items.sort(key=lambda entry: entry["start_line"])
    return {"path": path, "language": language, "count": len(items), "symbols": items}


def _all_docids(store: Any, subs: set[str]) -> list[str]:
    """KB 里去重后的文件路径 / doc id。有高效的存储查询就用(Postgres),否则从
    list_atoms 推导(内存/sqlite 场景够用)。"""
    fn = getattr(store, "all_source_docids", None)
    if callable(fn):
        return list(fn(sub_kbs=subs))
    return sorted({atom.source_docid for atom in store.list_atoms(sub_kbs=subs)})


def find_files(store: Any, pattern: str, *, sub_kbs: Any = None, limit: int = 50) -> dict[str, Any]:
    """找出路径包含 `pattern` 的已索引文件(大小写不敏感)。这是结构发现原语:让客户端
    能按名字/路径定位模块(比如 find_files('login') -> .../auth/Login/LoginManager.cpp),
    哪怕还不知道符号名 —— 正好补上纯关键词/向量检索留下的缺口。"""
    subs = _subs(sub_kbs)
    # 把 pattern 拆成若干 token,要求它们全部出现在路径里(大小写不敏感)。
    # 这样像 "auth/Login" 或 "login model" 这种猜测,即便各段在路径里并不相邻也能命中,
    # 空结果会少很多。
    tokens = [t for t in re.split(r"[\s/,]+", pattern.lower()) if t]
    docids = _all_docids(store, subs)
    if tokens:
        matched = [d for d in docids if all(t in d.lower() for t in tokens)]
    else:
        matched = docids
    return {
        "pattern": pattern,
        "count": len(matched),
        "truncated": len(matched) > limit,
        "files": matched[:limit],
    }


def list_dir(store: Any, prefix: str = "", *, sub_kbs: Any = None, limit: int = 300) -> dict[str, Any]:
    """列出 `prefix`(仓库/路径前缀)下一层的子目录和文件,由已索引的文件路径重建。
    让客户端能自顶向下浏览目录树。"""
    subs = _subs(sub_kbs)
    base = prefix.strip().strip("/")
    head = (base + "/") if base else ""
    dirs: set[str] = set()
    files: set[str] = set()
    for docid in _all_docids(store, subs):
        if head and not docid.startswith(head):
            continue
        rest = docid[len(head):]
        if not rest:
            continue
        seg, sep, more = rest.partition("/")
        if sep:
            dirs.add(head + seg)
        else:
            files.add(docid)
    return {
        "prefix": base,
        "dir_count": len(dirs),
        "file_count": len(files),
        "dirs": sorted(dirs)[:limit],
        "files": sorted(files)[:limit],
    }
