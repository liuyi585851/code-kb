"""Code-KB 的 LangChain 工具适配。

把 Code-KB 的 HTTP API 封装成 LangChain 的 ``@tool`` 函数,这样任何 LangChain /
LangGraph 智能体都能驱动知识库:先用 KB 定位,再去读真实文件
(本地有 checkout 就读本地,否则走 ``read_file_range``)。

需要安装可选依赖::

    pip install -e '.[agents]'

用 ``CODEKB_URL`` 指向正在运行的 Code-KB 服务(默认
``http://localhost:8000``)。
"""
from __future__ import annotations

import os

import requests
from langchain_core.tools import tool

CODEKB_URL = os.getenv("CODEKB_URL", "http://localhost:8000").rstrip("/")
_HEADERS = {"x-codekb-source": "langchain"}


def _post(path: str, body: dict) -> dict:
    resp = requests.post(f"{CODEKB_URL}{path}", json=body, headers=_HEADERS, timeout=60)
    resp.raise_for_status()
    return resp.json()


@tool
def code_search(query: str) -> str:
    """在代码+文档知识库上做混合检索。遇到自然语言或非英文的概念,
    先把它展开成可能的英文标识符。返回 repo/file:line 命中和片段——
    要看当前代码,请打开本地真实文件。"""
    hits = _post("/code/search", {"query": query, "top_k": 6}).get("hits", [])
    return "\n".join(
        f"{h.get('file_path') or h.get('docid')}:{h.get('start_line', '')} — {(h.get('snippet') or '')[:200]}"
        for h in hits
    ) or "no hits"


@tool
def find_files(pattern: str) -> str:
    """在已索引文件里按路径包含的词来查找——当概念检索只返回泛泛的文档时,
    可以靠模块名把它找出来(比如 'login' 或 'auth account')。"""
    files = _post("/code/files", {"pattern": pattern, "limit": 40}).get("files", [])
    return "\n".join(files) or "no files"


@tool
def list_dir(prefix: str = "") -> str:
    """列出某个 repo/path 前缀下的直接子目录和文件,用来浏览目录树。"""
    data = _post("/code/dir", {"prefix": prefix})
    return "dirs:\n" + "\n".join(data.get("dirs", [])) + "\n\nfiles:\n" + "\n".join(data.get("files", []))


@tool
def get_symbol(name: str) -> str:
    """查找定义或包含某个精确符号(函数/类/方法/常量)的代码。"""
    matches = _post("/code/symbol", {"name": name}).get("matches", [])
    return "\n".join(f"{m.get('file_path')}:{m.get('start_line', '')} — {m.get('symbol', '')}" for m in matches) or "no match"


@tool
def read_file_range(path: str, start_line: int, end_line: int) -> str:
    """读取某个文件指定行区间的已索引代码。本地没有该仓库时用它兜底——
    KB 存的是快照;本地有实时文件时优先读本地。"""
    segments = _post("/code/read", {"path": path, "start_line": start_line, "end_line": end_line}).get("segments", [])
    return "\n".join(s.get("text", "") for s in segments) or "not found"


# 开箱即用的工具列表,可直接交给 create_react_agent(...) / AgentExecutor。
CODEKB_TOOLS = [code_search, find_files, list_dir, get_symbol, read_file_range]
