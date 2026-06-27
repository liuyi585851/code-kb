import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.cli import main
from codekb.user_auth import JsonUserTokenStore
from codekb.user_confirmation import (
    JsonlUserConfirmationOutbox,
    JsonlUserConfirmationResponseStore,
    get_user_confirmation_detail,
    list_user_confirmations,
)
from codekb.user_confirmation import UserConfirmationRequest
from codekb.user_confirmation_delivery import (
    IMAppMessageClient,
    build_im_confirmation_message,
    process_user_confirmation_outbox,
    validate_im_delivery_configuration,
)
from codekb.user_confirmation_page import render_user_confirmation_page


class UserConfirmationDeliveryTests(unittest.TestCase):
    def test_process_confirmation_outbox_dry_run_validates_current_user_route(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store, outbox, issued = _write_confirmation_case(root, route_user="ww-user")
            report_path = root / "report.json"

            report = process_user_confirmation_outbox(outbox, token_store_path=token_store, report_path=report_path)

            raw_report = report_path.read_text(encoding="utf-8")

        self.assertEqual(report.status, "validated")
        self.assertEqual(report.processed, 1)
        self.assertEqual(report.results[0].status, "validated")
        self.assertEqual(report.results[0].detail, "dry_run")
        self.assertNotIn(issued["token"], raw_report)
        self.assertNotIn("ww-user", raw_report)

    def test_process_confirmation_outbox_dry_run_accepts_self_binding_robot_route(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store = root / "tokens.json"
            outbox = root / "confirmation.jsonl"
            issued = JsonUserTokenStore(token_store).issue(
                user_id_hash="u_hash",
                display_name="User",
                scopes=["diagnose"],
                metadata={
                    "source": "self_service_binding",
                    "route_type": "im_robot",
                    "im_robot_key": "robot-route-secret",
                },
            )
            JsonlUserConfirmationOutbox(outbox).append(
                user_token=issued["token"],
                reason="problem_solved",
                message="请确认问题是否已解决",
                payload={"trace_id": "trace-1"},
            )
            report_path = root / "report.json"

            report = process_user_confirmation_outbox(outbox, token_store_path=token_store, report_path=report_path)
            raw_report = report_path.read_text(encoding="utf-8")

        self.assertEqual(report.status, "validated")
        self.assertEqual(report.results[0].status, "validated")
        self.assertEqual(report.results[0].detail, "dry_run")
        self.assertNotIn(issued["token"], raw_report)
        self.assertNotIn("robot-route-secret", raw_report)

    def test_process_confirmation_outbox_filters_confirmation_id(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store, outbox, issued = _write_confirmation_case(root, route_user="ww-user")
            outbox.write_text("{invalid-json\n" + outbox.read_text(encoding="utf-8"), encoding="utf-8")
            second = JsonlUserConfirmationOutbox(outbox).append(
                user_token=issued["token"],
                reason="problem_solved",
                message="请确认第二条",
                payload={"trace_id": "trace-2"},
            )

            report = process_user_confirmation_outbox(
                outbox,
                token_store_path=token_store,
                confirmation_id=second.confirmation_id,
            )

        self.assertEqual(report.status, "validated")
        self.assertEqual(report.processed, 1)
        self.assertEqual(report.invalid_lines, 0)
        self.assertEqual(report.results[0].confirmation_id, second.confirmation_id)

    def test_process_confirmation_outbox_execute_blocks_without_write_enable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store, outbox, _issued = _write_confirmation_case(root, route_user="ww-user")

            report = process_user_confirmation_outbox(outbox, token_store_path=token_store, execute=True)

        self.assertEqual(report.status, "blocked")
        self.assertEqual(report.blocked_operations, 1)
        self.assertEqual(report.results[0].status, "blocked_write_disabled")

    def test_process_confirmation_outbox_executes_with_fake_client(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store, outbox, issued = _write_confirmation_case(root, route_user="ww-user")
            client = _FakeIMClient()

            report = process_user_confirmation_outbox(
                outbox,
                token_store_path=token_store,
                execute=True,
                write_enabled=True,
                client=client,
            )

        self.assertEqual(report.status, "executed")
        self.assertEqual(report.executed_operations, 1)
        self.assertEqual(report.results[0].response["msgid"], "msg-1")
        self.assertEqual(client.calls[0]["to_user"], "ww-user")
        self.assertNotIn(issued["token"], json.dumps(report.to_dict(), ensure_ascii=False))

    def test_process_confirmation_outbox_skips_already_delivered(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store, outbox, _issued = _write_confirmation_case(root, route_user="ww-user")
            delivery_log = root / "delivery.jsonl"
            first_client = _FakeIMClient()

            first = process_user_confirmation_outbox(
                outbox,
                token_store_path=token_store,
                execute=True,
                write_enabled=True,
                client=first_client,
                delivery_log_path=delivery_log,
            )
            second_client = _FakeIMClient()
            second = process_user_confirmation_outbox(
                outbox,
                token_store_path=token_store,
                execute=True,
                write_enabled=True,
                client=second_client,
                delivery_log_path=delivery_log,
            )
            raw_delivery_log = delivery_log.read_text(encoding="utf-8")

        self.assertEqual(first.status, "executed")
        self.assertEqual(first.executed_operations, 1)
        self.assertEqual(second.status, "skipped")
        self.assertEqual(second.executed_operations, 0)
        self.assertEqual(second.results[0].status, "skipped_already_delivered")
        self.assertEqual(len(first_client.calls), 1)
        self.assertEqual(second_client.calls, [])
        self.assertIn(first.results[0].confirmation_id, raw_delivery_log)

    def test_process_confirmation_outbox_blocks_missing_im_route(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store, outbox, _issued = _write_confirmation_case(root, route_user="")

            report = process_user_confirmation_outbox(outbox, token_store_path=token_store)

        self.assertEqual(report.status, "blocked")
        self.assertEqual(report.results[0].status, "blocked_missing_route")

    def test_cli_user_confirmation_outbox_outputs_json_report(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store, outbox, _issued = _write_confirmation_case(root, route_user="ww-user")
            report_path = root / "report.json"
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "user-confirmation-outbox",
                        "--outbox",
                        str(outbox),
                        "--token-store",
                        str(token_store),
                        "--report",
                        str(report_path),
                        "--delivery-log",
                        str(root / "delivery.jsonl"),
                        "--json",
                    ]
                )
            report = json.loads(stdout.getvalue())
            report_exists = report_path.exists()

        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "validated")
        self.assertEqual(report["processed"], 1)
        self.assertEqual(report["delivery_log_path"], str(root / "delivery.jsonl"))
        self.assertTrue(report_exists)

    def test_build_im_confirmation_message_uses_textcard_when_url_configured(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _token_store, outbox, _issued = _write_confirmation_case(root, route_user="ww-user")
            request = json.loads(outbox.read_text(encoding="utf-8"))
            message = build_im_confirmation_message(
                to_user="ww-user",
                agent_id="1000001",
                request=_request_from_payload(request),
                confirmation_url_base="https://kb.example/confirm",
            )

        self.assertEqual(message["msgtype"], "textcard")
        self.assertEqual(message["touser"], "ww-user")
        self.assertIn("confirmation_id=", message["textcard"]["url"])

    def test_build_im_confirmation_message_rejects_invalid_agent_id(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _token_store, outbox, _issued = _write_confirmation_case(root, route_user="ww-user")
            request = json.loads(outbox.read_text(encoding="utf-8"))

            with self.assertRaises(ValueError):
                build_im_confirmation_message(
                    to_user="ww-user",
                    agent_id="agent-one",
                    request=_request_from_payload(request),
                    confirmation_url_base="https://kb.example/confirm",
                )

    def test_validate_im_delivery_configuration_requires_confirm_url_for_real_send(self):
        report = validate_im_delivery_configuration(
            agent_id="1000001",
            confirmation_url_base="",
            require_confirmation_url=True,
        )

        self.assertFalse(report["ok"])
        self.assertIn("CODEKB_IM_CONFIRM_URL_BASE is required", " ".join(report["errors"]))

    def test_validate_im_delivery_configuration_rejects_relative_confirm_url(self):
        report = validate_im_delivery_configuration(
            agent_id="1000001",
            confirmation_url_base="/auth/im/confirmations/page",
            require_confirmation_url=True,
        )

        self.assertFalse(report["ok"])
        self.assertIn("absolute http(s) URL", " ".join(report["errors"]))

    def test_render_confirmation_page_escapes_confirmation_id(self):
        html = render_user_confirmation_page(confirmation_id='confirm-1"><script>')

        self.assertIn('data-ui-version="3"', html)
        self.assertIn('class="app"', html)
        self.assertIn("Code-KB Web Push Inbox", html)
        self.assertIn("/auth/im/confirmations/${encodeURIComponent(confirmationId)}/detail", html)
        self.assertIn("/auth/im/confirmations/${encodeURIComponent(confirmationId)}/response", html)
        self.assertIn("/auth/im/confirmations/pending", html)
        self.assertIn("pending-list", html)
        self.assertIn("load-pending", html)
        self.assertIn("include-responded", html)
        self.assertIn("auto-refresh", html)
        self.assertIn("setInterval", html)
        self.assertIn("confirm-1&quot;&gt;&lt;script&gt;", html)
        self.assertNotIn('confirm-1"><script>', html)

    def test_record_confirmation_response_validates_target_user(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _token_store, outbox, issued = _write_confirmation_case(root, route_user="ww-user")
            request = json.loads(outbox.read_text(encoding="utf-8"))
            responses = root / "responses.jsonl"

            response = JsonlUserConfirmationResponseStore(responses).record(
                outbox_path=outbox,
                user_token=issued["token"],
                confirmation_id=request["confirmation_id"],
                decision="confirmed",
                comment="已解决 password=abc123",
                metadata={"source": "unit-test", "url": "https://kb.example/path?token=secret"},
            )
            raw = responses.read_text(encoding="utf-8")

        self.assertEqual(response.decision, "confirmed")
        self.assertEqual(response.confirmation_id, request["confirmation_id"])
        self.assertIn("[REDACTED]", response.comment)
        self.assertNotIn("abc123", raw)
        self.assertNotIn("secret", raw)
        self.assertNotIn(issued["token"], raw)
        self.assertIn("responder_user_token_hash", raw)

    def test_record_confirmation_response_rejects_wrong_user_token(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _token_store, outbox, _issued = _write_confirmation_case(root, route_user="ww-user")
            request = json.loads(outbox.read_text(encoding="utf-8"))
            responses = root / "responses.jsonl"

            with self.assertRaises(PermissionError):
                JsonlUserConfirmationResponseStore(responses).record(
                    outbox_path=outbox,
                    user_token="wrong-token",
                    confirmation_id=request["confirmation_id"],
                    decision="confirmed",
                )

    def test_cli_user_confirmation_respond_and_summary(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store, outbox, issued = _write_confirmation_case(root, route_user="ww-user")
            request = json.loads(outbox.read_text(encoding="utf-8"))
            responses = root / "responses.jsonl"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "user-confirmation-respond",
                        "--outbox",
                        str(outbox),
                        "--responses",
                        str(responses),
                        "--token-store",
                        str(token_store),
                        "--confirmation-id",
                        request["confirmation_id"],
                        "--auth-token",
                        issued["token"],
                        "--decision",
                        "confirmed",
                        "--comment",
                        "已解决",
                        "--json",
                    ]
                )
            response = json.loads(stdout.getvalue())
            summary_stdout = io.StringIO()
            with contextlib.redirect_stdout(summary_stdout):
                summary_code = main(
                    [
                        "user-confirmation-responses",
                        "--responses",
                        str(responses),
                        "--json",
                    ]
                )
            summary = json.loads(summary_stdout.getvalue())
            raw_summary = json.dumps(summary, ensure_ascii=False)

        self.assertEqual(code, 0)
        self.assertEqual(summary_code, 0)
        self.assertEqual(response["decision"], "confirmed")
        self.assertEqual(summary["total"], 1)
        self.assertNotIn(issued["token"], raw_summary)
        self.assertIn("responder_user_token_hash_prefix", raw_summary)

    def test_current_user_pending_filters_by_token_and_response_status(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store = root / "tokens.json"
            outbox = root / "confirmation.jsonl"
            responses = root / "responses.jsonl"
            store = JsonUserTokenStore(token_store)
            issued = store.issue(user_id_hash="u_hash", metadata={"im_userid": "ww-user"})
            other = store.issue(user_id_hash="other_hash", metadata={"im_userid": "ww-other"})
            request = JsonlUserConfirmationOutbox(outbox).append(
                user_token=issued["token"],
                reason="problem_solved",
                message="请确认 token=secret",
                payload={"url": "https://kb.example/path?token=secret"},
            )
            JsonlUserConfirmationOutbox(outbox).append(
                user_token=other["token"],
                reason="human_review_required",
                message="其他用户确认",
                payload={},
            )

            pending = list_user_confirmations(outbox, responses_path=responses, user_token=issued["token"])
            detail = get_user_confirmation_detail(
                outbox,
                responses_path=responses,
                user_token=issued["token"],
                confirmation_id=request.confirmation_id,
            )
            JsonlUserConfirmationResponseStore(responses).record(
                outbox_path=outbox,
                user_token=issued["token"],
                confirmation_id=request.confirmation_id,
                decision="confirmed",
            )
            after_response = list_user_confirmations(outbox, responses_path=responses, user_token=issued["token"])
            with_response = list_user_confirmations(
                outbox,
                responses_path=responses,
                user_token=issued["token"],
                include_responded=True,
            )

        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["confirmation_id"], request.confirmation_id)
        self.assertNotIn("secret", json.dumps(pending, ensure_ascii=False))
        self.assertEqual(detail["confirmation_id"], request.confirmation_id)
        self.assertEqual(after_response, ())
        self.assertEqual(with_response[0]["status"], "responded")
        self.assertEqual(with_response[0]["response"]["decision"], "confirmed")

    def test_cli_user_confirmation_pending_and_detail(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store, outbox, issued = _write_confirmation_case(root, route_user="ww-user")
            request = json.loads(outbox.read_text(encoding="utf-8"))
            responses = root / "responses.jsonl"
            pending_stdout = io.StringIO()
            with contextlib.redirect_stdout(pending_stdout):
                pending_code = main(
                    [
                        "user-confirmation-pending",
                        "--outbox",
                        str(outbox),
                        "--responses",
                        str(responses),
                        "--token-store",
                        str(token_store),
                        "--auth-token",
                        issued["token"],
                        "--json",
                    ]
                )
            pending = json.loads(pending_stdout.getvalue())
            detail_stdout = io.StringIO()
            with contextlib.redirect_stdout(detail_stdout):
                detail_code = main(
                    [
                        "user-confirmation-detail",
                        "--outbox",
                        str(outbox),
                        "--responses",
                        str(responses),
                        "--token-store",
                        str(token_store),
                        "--auth-token",
                        issued["token"],
                        "--confirmation-id",
                        request["confirmation_id"],
                        "--json",
                    ]
                )
            detail = json.loads(detail_stdout.getvalue())

        self.assertEqual(pending_code, 0)
        self.assertEqual(detail_code, 0)
        self.assertEqual(pending["total"], 1)
        self.assertEqual(detail["confirmation_id"], request["confirmation_id"])
        self.assertNotIn(issued["token"], json.dumps(pending, ensure_ascii=False))


def _write_confirmation_case(root: Path, *, route_user: str):
    token_store = root / "tokens.json"
    outbox = root / "confirmation.jsonl"
    metadata = {"source": "test"}
    if route_user:
        metadata["im_userid"] = route_user
    issued = JsonUserTokenStore(token_store).issue(
        user_id_hash="u_hash",
        display_name="User",
        scopes=["diagnose"],
        metadata=metadata,
    )
    JsonlUserConfirmationOutbox(outbox).append(
        user_token=issued["token"],
        reason="problem_solved",
        message="请确认问题是否已解决",
        payload={"trace_id": "trace-1"},
    )
    return token_store, outbox, issued


def _request_from_payload(payload: dict):
    from codekb.user_confirmation_delivery import _request_from_dict

    return _request_from_dict(payload)


class _FakeIMClient:
    def __init__(self) -> None:
        self.calls = []

    def send_confirmation(self, *, to_user: str, request):
        self.calls.append({"to_user": to_user, "confirmation_id": request.confirmation_id})
        return {"errcode": 0, "errmsg": "ok", "msgid": "msg-1"}


class _FakeTransport:
    """Records (method, url, payload) calls and replays scripted responses."""

    def __init__(self, *, token_responses=None, send_responses=None) -> None:
        self.calls: list[dict] = []
        self._token_responses = list(
            token_responses or [{"errcode": 0, "access_token": "tok-1", "expires_in": 7200}]
        )
        self._send_responses = list(
            send_responses or [{"errcode": 0, "errmsg": "ok", "msgid": "msg-1"}]
        )

    @staticmethod
    def _next(queue):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def get_json(self, url: str) -> dict:
        self.calls.append({"method": "GET", "url": url, "payload": None})
        return dict(self._next(self._token_responses))

    def post_json(self, url: str, payload: dict) -> dict:
        self.calls.append({"method": "POST", "url": url, "payload": payload})
        return dict(self._next(self._send_responses))


class _MutableClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now


def _confirmation_request() -> UserConfirmationRequest:
    return UserConfirmationRequest(
        confirmation_id="conf-1",
        created_at="2026-06-22T00:00:00Z",
        channel="im",
        target_user_token_hash="hash-abcdef",
        reason="problem_solved",
        message="请确认问题是否已解决",
        payload={},
        status="pending_confirmation",
    )


def _im_client(transport, *, clock=None, **kwargs) -> IMAppMessageClient:
    return IMAppMessageClient(
        corp_id="corp-1",
        app_secret="secret-1",
        agent_id="1000001",
        confirmation_url_base="https://kb.example/confirm",
        transport=transport,
        clock=clock,
        **kwargs,
    )


class IMTransportContractTests(unittest.TestCase):
    def test_send_confirmation_call_order_and_payload(self):
        transport = _FakeTransport()
        client = _im_client(transport)

        response = client.send_confirmation(to_user="ww-user", request=_confirmation_request())

        self.assertEqual([call["method"] for call in transport.calls], ["GET", "POST"])
        self.assertIn("/gettoken?", transport.calls[0]["url"])
        self.assertIn("corpid=corp-1", transport.calls[0]["url"])
        self.assertIn("corpsecret=secret-1", transport.calls[0]["url"])
        self.assertIn("/message/send?access_token=tok-1", transport.calls[1]["url"])
        payload = transport.calls[1]["payload"]
        self.assertEqual(payload["msgtype"], "textcard")
        self.assertEqual(payload["touser"], "ww-user")
        self.assertEqual(payload["agentid"], 1000001)
        self.assertIn("confirmation_id=", payload["textcard"]["url"])
        self.assertEqual(response["msgid"], "msg-1")

    def test_send_confirmation_raises_on_message_errcode(self):
        transport = _FakeTransport(send_responses=[{"errcode": 40003, "errmsg": "invalid user"}])
        client = _im_client(transport)

        with self.assertRaises(RuntimeError):
            client.send_confirmation(to_user="ww-user", request=_confirmation_request())

    def test_gettoken_errcode_raises(self):
        transport = _FakeTransport(token_responses=[{"errcode": 40013, "errmsg": "invalid corpid"}])
        client = _im_client(transport)

        with self.assertRaises(RuntimeError):
            client.send_confirmation(to_user="ww-user", request=_confirmation_request())


class IMTokenExpiryTests(unittest.TestCase):
    def test_access_token_cached_until_expiry(self):
        transport = _FakeTransport(
            token_responses=[
                {"errcode": 0, "access_token": "tok-1", "expires_in": 7200},
                {"errcode": 0, "access_token": "tok-2", "expires_in": 7200},
            ],
            send_responses=[
                {"errcode": 0, "msgid": "m1"},
                {"errcode": 0, "msgid": "m2"},
                {"errcode": 0, "msgid": "m3"},
            ],
        )
        clock = _MutableClock(0.0)
        client = _im_client(transport, clock=clock, token_expiry_margin_seconds=60)

        client.send_confirmation(to_user="ww-user", request=_confirmation_request())
        gettoken_after_first = sum(1 for c in transport.calls if c["method"] == "GET")

        clock.now = 1000.0  # still well within ttl (7200 - 60 margin)
        client.send_confirmation(to_user="ww-user", request=_confirmation_request())
        gettoken_after_second = sum(1 for c in transport.calls if c["method"] == "GET")

        clock.now = 7200.0  # past expiry (>= 7140 effective)
        client.send_confirmation(to_user="ww-user", request=_confirmation_request())
        gettoken_after_third = sum(1 for c in transport.calls if c["method"] == "GET")

        self.assertEqual(gettoken_after_first, 1)
        self.assertEqual(gettoken_after_second, 1)  # cached: no new gettoken
        self.assertEqual(gettoken_after_third, 2)  # expired: refetched
        self.assertIn("access_token=tok-2", transport.calls[-1]["url"])

    def test_token_expired_errcode_forces_refresh_and_retries_once(self):
        transport = _FakeTransport(
            token_responses=[
                {"errcode": 0, "access_token": "tok-1", "expires_in": 7200},
                {"errcode": 0, "access_token": "tok-2", "expires_in": 7200},
            ],
            send_responses=[
                {"errcode": 42001, "errmsg": "access_token expired"},
                {"errcode": 0, "msgid": "m-ok"},
            ],
        )
        clock = _MutableClock(0.0)
        client = _im_client(transport, clock=clock)

        response = client.send_confirmation(to_user="ww-user", request=_confirmation_request())

        gettoken_calls = [c for c in transport.calls if c["method"] == "GET"]
        post_calls = [c for c in transport.calls if c["method"] == "POST"]
        self.assertEqual(len(gettoken_calls), 2)  # initial + forced refresh
        self.assertEqual(len(post_calls), 2)
        self.assertIn("access_token=tok-2", post_calls[1]["url"])
        self.assertEqual(response["msgid"], "m-ok")

    def test_token_expired_twice_raises(self):
        transport = _FakeTransport(
            token_responses=[
                {"errcode": 0, "access_token": "tok-1", "expires_in": 7200},
                {"errcode": 0, "access_token": "tok-2", "expires_in": 7200},
            ],
            send_responses=[
                {"errcode": 42001, "errmsg": "expired"},
                {"errcode": 42001, "errmsg": "still expired"},
            ],
        )
        client = _im_client(transport, clock=_MutableClock(0.0))

        with self.assertRaises(RuntimeError):
            client.send_confirmation(to_user="ww-user", request=_confirmation_request())

        self.assertEqual(sum(1 for c in transport.calls if c["method"] == "GET"), 2)
        self.assertEqual(sum(1 for c in transport.calls if c["method"] == "POST"), 2)


class _FlakyIMClient:
    """前 ``fail_times`` 次调用抛异常,之后成功。"""

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def send_confirmation(self, *, to_user: str, request):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"boom-{self.calls}")
        return {"errcode": 0, "errmsg": "ok", "msgid": "msg-ok"}


class WorkerRetryDeadLetterTests(unittest.TestCase):
    def test_worker_retries_then_succeeds_with_backoff(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store, outbox, _issued = _write_confirmation_case(root, route_user="ww-user")
            client = _FlakyIMClient(fail_times=2)
            sleeps: list[float] = []

            report = process_user_confirmation_outbox(
                outbox,
                token_store_path=token_store,
                execute=True,
                write_enabled=True,
                client=client,
                max_retries=2,
                backoff_base_seconds=0.5,
                sleeper=sleeps.append,
            )

        self.assertEqual(report.status, "executed")
        self.assertEqual(report.executed_operations, 1)
        self.assertEqual(report.dead_lettered, 0)
        self.assertEqual(client.calls, 3)
        self.assertEqual(sleeps, [0.5, 1.0])  # exponential backoff: base*2**0, base*2**1

    def test_worker_dead_letters_after_max_retries(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store, outbox, _issued = _write_confirmation_case(root, route_user="ww-user")
            dead_letter = root / "dead-letter.jsonl"
            client = _FlakyIMClient(fail_times=99)
            sleeps: list[float] = []

            report = process_user_confirmation_outbox(
                outbox,
                token_store_path=token_store,
                execute=True,
                write_enabled=True,
                client=client,
                max_retries=2,
                backoff_base_seconds=0.25,
                sleeper=sleeps.append,
                dead_letter_path=dead_letter,
            )
            raw_dead_letter = dead_letter.read_text(encoding="utf-8")
            records = [json.loads(line) for line in raw_dead_letter.splitlines() if line.strip()]

        self.assertEqual(report.status, "failed")
        self.assertEqual(report.dead_lettered, 1)
        self.assertEqual(report.results[0].status, "failed")
        self.assertEqual(report.dead_letter_path, str(dead_letter))
        self.assertEqual(client.calls, 3)  # 1 + 2 retries
        self.assertEqual(len(sleeps), 2)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["confirmation_id"], report.results[0].confirmation_id)
        self.assertEqual(records[0]["attempts"], 3)
        self.assertIn("boom-3", records[0]["last_error"])
        # 死信记录绝不写完整的 token 哈希,只留前缀。
        self.assertIn("target_user_token_hash_prefix", records[0])

    def test_worker_default_is_single_attempt_no_dead_letter(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store, outbox, _issued = _write_confirmation_case(root, route_user="ww-user")
            client = _FlakyIMClient(fail_times=99)
            sleeps: list[float] = []

            report = process_user_confirmation_outbox(
                outbox,
                token_store_path=token_store,
                execute=True,
                write_enabled=True,
                client=client,
                sleeper=sleeps.append,
            )

        # 行为零漂移:只尝试一次、状态 failed、无死信、无 sleep。
        self.assertEqual(report.status, "failed")
        self.assertEqual(report.dead_lettered, 0)
        self.assertEqual(client.calls, 1)
        self.assertEqual(sleeps, [])


if __name__ == "__main__":
    unittest.main()
