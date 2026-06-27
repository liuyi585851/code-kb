"""LLM 驱动的查询展开——把自然语言(常常是中文)的提问,转换成源码里真正使用的
英文代码标识符,让单次检索就能跨过"自然语言→代码"的候选鸿沟,而**无需**人工维护别名表。

它把客户端 `code-kb` skill 的展开步骤搬到了服务端自动完成:按需启用
(CODEKB_QUERY_EXPANSION),并在任何 LLM 调用或解析失败时退化为空操作。
"""
from __future__ import annotations

import json
import re
from typing import Any

from .llm import GenerationRequest

_SYSTEM = "You convert a developer's question into code-search keywords. Output JSON only."
_PROMPT = (
    "Question (may be Chinese): {q}\n\n"
    "List 6-12 short keywords/identifiers that the actual SOURCE CODE, symbol names, or file "
    "paths would contain to answer this. Translate concepts to the English terms code uses, e.g. "
    "第三方登录 -> login, account, oauth, sdk, msdk, union; 支付 -> pay, billing, iap, order; "
    "好友 -> friend, relation. Output ONLY a JSON array of lowercase strings, no prose."
)

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")


def expand_query(query: str, llm_client: Any, *, max_terms: int = 12) -> list[str]:
    """为查询返回至多 max_terms 个代码检索关键词(任何环节失败都返回空列表)。"""
    if not query or not query.strip() or llm_client is None:
        return []
    try:
        request = GenerationRequest(system=_SYSTEM, prompt=_PROMPT.format(q=query[:500]), max_tokens=200)
        text = llm_client.generate(request).text
    except Exception:  # noqa: BLE001 - 展开只是尽力而为,绝不能因此中断检索
        return []
    return parse_terms(text, max_terms=max_terms)


def parse_terms(text: str, *, max_terms: int = 12) -> list[str]:
    """从 LLM 输出中提取关键词列表:优先解析 JSON 数组,失败再退回按 token 切分。"""
    terms: list[str] = []
    match = re.search(r"\[.*\]", text or "", re.S)
    if match:
        try:
            terms = [str(item).strip().lower() for item in json.loads(match.group(0))]
        except Exception:  # noqa: BLE001
            terms = []
    if not terms:
        terms = [tok.lower() for tok in _TOKEN_RE.findall(text or "")]
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        if term and term not in seen:
            seen.add(term)
            out.append(term)
    return out[:max_terms]
