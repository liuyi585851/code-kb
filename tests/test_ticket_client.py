import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.ticket_client import HttpGovernanceTicketClient


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, *, method, url, headers, body):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": dict(body)})
        return dict(self.response)


class HttpGovernanceTicketClientTests(unittest.TestCase):
    def test_create_issue_tracker_ticket_maps_fields_and_parses_ticket_id(self):
        transport = FakeTransport({"data": {"Bug": {"id": "1037012345"}}})
        client = HttpGovernanceTicketClient(
            issue_tracker_base_url="https://issue_tracker.example/api",
            issue_tracker_token="issue_tracker-token",
            issue_tracker_workspace_id="20012345",
            transport=transport,
        )

        response = client.create_issue_tracker_ticket(
            title="补充 DEVICE_SEQ 文档",
            description="治理项需补充",
            priority="P1",
            assignee="alice",
            labels=("codekb", "stale", "testing"),
        )

        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], "https://issue_tracker.example/api/bugs")
        self.assertEqual(call["headers"]["Authorization"], "Bearer issue_tracker-token")
        self.assertEqual(call["body"]["workspace_id"], "20012345")
        self.assertEqual(call["body"]["name"], "补充 DEVICE_SEQ 文档")
        self.assertEqual(call["body"]["description"], "治理项需补充")
        self.assertEqual(call["body"]["priority"], "High")  # P1 -> High
        self.assertEqual(call["body"]["owner"], "alice")
        self.assertEqual(call["body"]["label"], "codekb;stale;testing")
        # 嵌套的 ISSUE_TRACKER 响应结构会被解析回规范化的 ticket_id。
        self.assertEqual(response["ticket_id"], "1037012345")

    def test_issue_tracker_priority_mapping_defaults_to_middle(self):
        transport = FakeTransport({"ticket_id": "x"})
        client = HttpGovernanceTicketClient(issue_tracker_base_url="https://t/api", transport=transport)
        client.create_issue_tracker_ticket(title="t", description="d", priority="P9", assignee="", labels=())
        self.assertEqual(transport.calls[0]["body"]["priority"], "Middle")
        self.assertNotIn("Authorization", transport.calls[0]["headers"])  # no token

    def test_create_git_issue_maps_fields_and_parses_iid(self):
        transport = FakeTransport({"iid": 42, "id": 99999, "title": "t"})
        client = HttpGovernanceTicketClient(
            git_base_url="https://git.example/api/v3",
            git_token="gf-token",
            git_project_id="881",
            transport=transport,
        )

        response = client.create_git_issue(
            title="Git issue",
            description="desc",
            priority="P2",
            assignee="77",
            labels=("codekb", "gap"),
        )

        call = transport.calls[0]
        self.assertEqual(call["url"], "https://git.example/api/v3/projects/881/issues")
        self.assertEqual(call["headers"]["Authorization"], "Bearer gf-token")
        self.assertEqual(call["body"]["project_id"], "881")
        self.assertEqual(call["body"]["title"], "Git issue")
        self.assertEqual(call["body"]["labels"], "codekb,gap")
        self.assertEqual(call["body"]["assignee_id"], "77")
        # 项目内的工单引用优先用 iid 而非 id。
        self.assertEqual(response["ticket_id"], "42")

    def test_git_parses_top_level_id_when_no_iid(self):
        transport = FakeTransport({"id": 555})
        client = HttpGovernanceTicketClient(git_base_url="https://g/api", git_project_id="1", transport=transport)
        response = client.create_git_issue(title="t", description="d", priority="P1", assignee="", labels=())
        self.assertEqual(response["ticket_id"], "555")

    def test_from_env_reads_all_settings(self):
        env = {
            "CODEKB_ISSUE_TRACKER_API_BASE_URL": "https://issue_tracker.internal/api",
            "CODEKB_ISSUE_TRACKER_API_TOKEN": "t-tok",
            "CODEKB_ISSUE_TRACKER_WORKSPACE_ID": "9001",
            "CODEKB_GIT_API_BASE_URL": "https://gf.internal/api/v3",
            "CODEKB_GIT_API_TOKEN": "g-tok",
            "CODEKB_GIT_PROJECT_ID": "555",
        }
        transport = FakeTransport({"ticket_id": "1"})
        client = HttpGovernanceTicketClient.from_env(env, transport=transport)

        client.create_issue_tracker_ticket(title="t", description="d", priority="P0", assignee="a", labels=())
        self.assertEqual(transport.calls[0]["url"], "https://issue_tracker.internal/api/bugs")
        self.assertEqual(transport.calls[0]["body"]["workspace_id"], "9001")
        self.assertEqual(transport.calls[0]["body"]["priority"], "High")

        client.create_git_issue(title="t", description="d", priority="P1", assignee="a", labels=())
        self.assertEqual(transport.calls[1]["url"], "https://gf.internal/api/v3/projects/555/issues")

    def test_transport_must_return_dict(self):
        client = HttpGovernanceTicketClient(issue_tracker_base_url="https://t/api", transport=lambda **kw: "nope")
        with self.assertRaises(ValueError):
            client.create_issue_tracker_ticket(title="t", description="d", priority="P1", assignee="", labels=())


if __name__ == "__main__":
    unittest.main()
