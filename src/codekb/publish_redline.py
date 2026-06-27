"""发布前的红线扫描与脱敏。

一道保守、确定、不走网络的关卡:在真正写入 Wiki 前,扫描发布内容
(渲染正文 + 操作参数)里明显的敏感信息 —— 凭证赋值、API token、邮箱、
大陆手机号、内网域名。命中规则就拦下这次发布(``blocked_redline``),
同时给出脱敏(打码)后的预览,让操作者知道是哪一类东西触发了关卡,
而报告本身不会泄露真实的密文。

默认规则刻意放得保守,以免误拦已审核通过的非敏感 KB 内容。
调用方可以传 ``rules=()`` 来覆盖或关闭扫描。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence


MASK = "***REDACTED***"


@dataclass(frozen=True)
class RedlineRule:
    name: str
    pattern: str
    flags: int = 0


# 保守的默认规则,每条 pattern 都对应一类明确的敏感数据。
DEFAULT_REDLINE_RULES: tuple[RedlineRule, ...] = (
    RedlineRule(
        name="credential_assignment",
        pattern=r"(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|私钥|密钥|口令|密码)\s*[:=：]\s*\S+",
        flags=re.IGNORECASE,
    ),
    RedlineRule(name="email", pattern=r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    RedlineRule(name="phone_cn", pattern=r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    RedlineRule(name="internal_domain", pattern=r"[A-Za-z0-9-]+\.(?:corp|internal|intra|lan)\b"),
)


@dataclass(frozen=True)
class RedlineMatch:
    rule: str
    count: int


@dataclass(frozen=True)
class RedlineResult:
    matched: bool
    matches: tuple[RedlineMatch, ...]
    sanitized: str

    @property
    def matched_rules(self) -> list[str]:
        return [match.rule for match in self.matches]

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "matched_rules": self.matched_rules,
            "matches": [{"rule": m.rule, "count": m.count} for m in self.matches],
            "sanitized_preview": self.sanitized,
        }


def publish_redline(text: str, *, rules: Optional[Sequence[RedlineRule]] = None) -> RedlineResult:
    """扫描 ``text``,返回命中结果以及一份打码(脱敏)后的副本。"""

    active_rules = DEFAULT_REDLINE_RULES if rules is None else tuple(rules)
    source = str(text or "")
    matches: list[RedlineMatch] = []
    sanitized = source
    for rule in active_rules:
        compiled = re.compile(rule.pattern, rule.flags)
        found = compiled.findall(source)
        if found:
            matches.append(RedlineMatch(rule=rule.name, count=len(found)))
            sanitized = compiled.sub(MASK, sanitized)
    return RedlineResult(matched=bool(matches), matches=tuple(matches), sanitized=sanitized)


def scan_operation_redline(
    rendered_body: str,
    params: dict[str, Any],
    *,
    rules: Optional[Sequence[RedlineRule]] = None,
) -> RedlineResult:
    """把渲染正文和操作参数合并成一份文本一起扫描。"""

    param_text = _stringify_params(params)
    corpus = f"{rendered_body}\n{param_text}" if rendered_body else param_text
    return publish_redline(corpus, rules=rules)


def _stringify_params(params: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in sorted(params):
        value = params[key]
        if isinstance(value, (str, int, float, bool)):
            parts.append(f"{key}={value}")
        else:
            parts.append(f"{key}={value!r}")
    return "\n".join(parts)


def sanitize_text(text: str, *, rules: Optional[Iterable[RedlineRule]] = None) -> str:
    rule_seq = None if rules is None else tuple(rules)
    return publish_redline(text, rules=rule_seq).sanitized
