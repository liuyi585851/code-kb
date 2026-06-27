"""实现 :class:`WikiPublishClient` 协议的 Wiki 发布客户端(走 HTTP)。

所有网络 I/O 都经由**注入的 transport 可调用对象**完成,所以用 FakeTransport
就能完整测试,单元测试里绝不碰网络。默认 transport 是对 ``urllib`` 的薄封装,
仅在未注入 transport 时使用(即经 :meth:`HttpWikiPublishClient.from_env` 的真实部署)。

transport 的约定如下::

    transport(*, method: str, url: str, headers: dict[str, str], body: dict) -> dict

它必须返回从 Wiki 解析出来的 JSON 对象(一个 ``dict``)。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Optional


DEFAULT_WIKI_API_BASE_URL = "https://wiki.example.com/api/v1"

Transport = Callable[..., dict]


class HttpWikiPublishClient:
    def __init__(
        self,
        *,
        base_url: str = DEFAULT_WIKI_API_BASE_URL,
        token: str = "",
        transport: Optional[Transport] = None,
    ) -> None:
        self.base_url = str(base_url or DEFAULT_WIKI_API_BASE_URL).rstrip("/")
        self.token = str(token or "")
        self._transport = transport or _urllib_transport

    @classmethod
    def from_env(
        cls,
        env: Optional[Mapping[str, str]] = None,
        *,
        transport: Optional[Transport] = None,
    ) -> "HttpWikiPublishClient":
        import os

        env = os.environ if env is None else env
        base_url = str(env.get("CODEKB_WIKI_API_BASE_URL", "") or DEFAULT_WIKI_API_BASE_URL).strip()
        token = str(env.get("CODEKB_WIKI_API_TOKEN", "") or "").strip()
        return cls(base_url=base_url, token=token, transport=transport)

    def save_document_parts(self, *, id: int, title: str, after: str = "", before: str = "") -> dict[str, Any]:
        return self._request(
            "/saveDocumentParts",
            {"id": int(id), "title": str(title), "after": str(after), "before": str(before)},
        )

    def copy_document(
        self,
        *,
        docid: int,
        new_parentid: int,
        is_single: int = 1,
        language: str = "zh_CN",
    ) -> dict[str, Any]:
        response = self._request(
            "/copyDocument",
            {
                "docid": int(docid),
                "new_parentid": int(new_parentid),
                "is_single": int(is_single),
                "language": str(language),
            },
        )
        result = dict(response)
        new_docid = _parse_new_docid(response)
        if new_docid:
            # 把复制出来的文档 id 归一化写回 ``docid``,好让下游的
            # saveDocument 能定位到它(publish.py 读的是 docid/contentid)。
            result["docid"] = new_docid
        return result

    def save_document(
        self,
        *,
        docid: int,
        title: str,
        body: str,
        is_html: bool = False,
        raw: bool = False,
    ) -> dict[str, Any]:
        return self._request(
            "/saveDocument",
            {
                "docid": int(docid),
                "title": str(title),
                "body": str(body),
                "is_html": bool(is_html),
                "raw": bool(raw),
            },
        )

    def _request(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        response = self._transport(method="POST", url=url, headers=headers, body=body)
        if not isinstance(response, dict):
            raise ValueError("Wiki transport must return a JSON object")
        return response


def _parse_new_docid(response: Mapping[str, Any]) -> int:
    """从 Wiki 常见的几种返回结构里取出复制后的文档 id。"""

    candidates = [
        response.get("contentid"),
        response.get("docid"),
        response.get("new_docid"),
    ]
    data = response.get("data")
    if isinstance(data, Mapping):
        candidates.extend([data.get("contentid"), data.get("docid"), data.get("new_docid")])
    for candidate in candidates:
        if candidate in (None, ""):
            continue
        try:
            parsed = int(str(candidate).strip())
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return 0


def _urllib_transport(*, method: str, url: str, headers: Mapping[str, str], body: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover - 真实网络路径,仅在实跑时才会走到
    import urllib.request

    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method=method)
    for key, value in headers.items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=30) as handle:  # noqa: S310 - 受控的内部 Wiki 接口
        raw = handle.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else {}
