"""面向 ISSUE_TRACKER 与 Git 的 HTTP 工单客户端。

实现 ``governance.py`` 里的 :class:`GovernanceTicketClient` 协议
(``create_issue_tracker_ticket`` / ``create_git_issue``)。所有网络 I/O 都走
**注入进来的 transport 可调用对象**,所以单测里可以用 FakeTransport 完全打桩,
绝不真正联网。默认 transport 是一层很薄的 ``urllib`` 封装,只在真实部署时经由
:meth:`HttpGovernanceTicketClient.from_env` 使用。

transport 的约定与 Wiki 发布客户端一致::

    transport(*, method: str, url: str, headers: dict[str, str], body: dict) -> dict

抽象工单字段(title/description/priority/assignee/labels)会映射到各系统真实的
请求体上,再把系统返回解析回统一的 ``ticket_id``。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Optional


DEFAULT_ISSUE_TRACKER_API_BASE_URL = "https://api.issue_tracker.cn"
DEFAULT_GIT_API_BASE_URL = "https://git.example.com/api/v3"

# 抽象严重级别(P0..P3)映射到 ISSUE_TRACKER 的优先级枚举。
ISSUE_TRACKER_PRIORITY_MAP = {"P0": "High", "P1": "High", "P2": "Middle", "P3": "Low"}
ISSUE_TRACKER_DEFAULT_PRIORITY = "Middle"

Transport = Callable[..., dict]


class HttpGovernanceTicketClient:
    def __init__(
        self,
        *,
        issue_tracker_base_url: str = DEFAULT_ISSUE_TRACKER_API_BASE_URL,
        issue_tracker_token: str = "",
        issue_tracker_workspace_id: str = "",
        git_base_url: str = DEFAULT_GIT_API_BASE_URL,
        git_token: str = "",
        git_project_id: str = "",
        transport: Optional[Transport] = None,
    ) -> None:
        self.issue_tracker_base_url = str(issue_tracker_base_url or DEFAULT_ISSUE_TRACKER_API_BASE_URL).rstrip("/")
        self.issue_tracker_token = str(issue_tracker_token or "")
        self.issue_tracker_workspace_id = str(issue_tracker_workspace_id or "")
        self.git_base_url = str(git_base_url or DEFAULT_GIT_API_BASE_URL).rstrip("/")
        self.git_token = str(git_token or "")
        self.git_project_id = str(git_project_id or "")
        self._transport = transport or _urllib_transport

    @classmethod
    def from_env(
        cls,
        env: Optional[Mapping[str, str]] = None,
        *,
        transport: Optional[Transport] = None,
    ) -> "HttpGovernanceTicketClient":
        import os

        env = os.environ if env is None else env
        return cls(
            issue_tracker_base_url=str(env.get("CODEKB_ISSUE_TRACKER_API_BASE_URL", "") or DEFAULT_ISSUE_TRACKER_API_BASE_URL).strip(),
            issue_tracker_token=str(env.get("CODEKB_ISSUE_TRACKER_API_TOKEN", "") or "").strip(),
            issue_tracker_workspace_id=str(env.get("CODEKB_ISSUE_TRACKER_WORKSPACE_ID", "") or "").strip(),
            git_base_url=str(
                env.get("CODEKB_GIT_API_BASE_URL", "") or DEFAULT_GIT_API_BASE_URL
            ).strip(),
            git_token=str(env.get("CODEKB_GIT_API_TOKEN", "") or "").strip(),
            git_project_id=str(env.get("CODEKB_GIT_PROJECT_ID", "") or "").strip(),
            transport=transport,
        )

    def create_issue_tracker_ticket(
        self,
        *,
        title: str,
        description: str,
        priority: str,
        assignee: str,
        labels: tuple[str, ...],
    ) -> dict[str, Any]:
        body = {
            "workspace_id": self.issue_tracker_workspace_id,
            "name": str(title),
            "description": str(description),
            "priority": ISSUE_TRACKER_PRIORITY_MAP.get(str(priority).strip().upper(), ISSUE_TRACKER_DEFAULT_PRIORITY),
            "owner": str(assignee or ""),
            "label": ";".join(str(label) for label in labels),
        }
        response = self._request(self.issue_tracker_base_url, "/bugs", body, token=self.issue_tracker_token)
        return _with_ticket_id(response, keys=("ticket_id", "id"))

    def create_git_issue(
        self,
        *,
        title: str,
        description: str,
        priority: str,
        assignee: str,
        labels: tuple[str, ...],
    ) -> dict[str, Any]:
        body = {
            "project_id": self.git_project_id,
            "title": str(title),
            "description": str(description),
            "labels": ",".join(str(label) for label in labels),
            "assignee_id": str(assignee or ""),
            "priority": str(priority),
        }
        path = f"/projects/{self.git_project_id}/issues"
        response = self._request(self.git_base_url, path, body, token=self.git_token)
        return _with_ticket_id(response, keys=("iid", "ticket_id", "issue_id", "id"))

    def _request(self, base_url: str, path: str, body: dict[str, Any], *, token: str) -> dict[str, Any]:
        url = f"{base_url}{path}"
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = self._transport(method="POST", url=url, headers=headers, body=body)
        if not isinstance(response, dict):
            raise ValueError("governance ticket transport must return a JSON object")
        return response


def _with_ticket_id(response: Mapping[str, Any], *, keys: tuple[str, ...]) -> dict[str, Any]:
    result = dict(response)
    ticket_id = _parse_ticket_id(response, keys=keys)
    if ticket_id:
        result["ticket_id"] = ticket_id
    return result


def _parse_ticket_id(response: Mapping[str, Any], *, keys: tuple[str, ...]) -> str:
    sources: list[Mapping[str, Any]] = [response]
    data = response.get("data")
    if isinstance(data, Mapping):
        sources.append(data)
        # ISSUE_TRACKER 会把新建的实体再包一层,例如 {"data": {"Bug": {"id": ...}}}。
        for value in data.values():
            if isinstance(value, Mapping):
                sources.append(value)
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def _urllib_transport(*, method: str, url: str, headers: Mapping[str, str], body: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover - 真实联网路径,只在实跑时才会走到
    import urllib.request

    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method=method)
    for key, value in headers.items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=30) as handle:  # noqa: S310 - 受控的内部端点
        raw = handle.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else {}
