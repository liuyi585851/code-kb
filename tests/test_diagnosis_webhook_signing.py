import hmac
import sys
import unittest
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.diagnosis_webhook_signing import (
    HmacSha256Verifier,
    verify_webhook_signature,
)


def _sign(secret: str, raw_body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw_body, sha256).hexdigest()


class HmacSha256VerifierTests(unittest.TestCase):
    def test_verify_accepts_matching_signature(self):
        verifier = HmacSha256Verifier()
        body = b'{"hello": "world"}'
        signature = _sign("s3cr3t", body)
        self.assertTrue(verifier.verify("s3cr3t", body, signature))

    def test_verify_tolerates_surrounding_whitespace(self):
        verifier = HmacSha256Verifier()
        body = b"payload"
        signature = _sign("s3cr3t", body)
        self.assertTrue(verifier.verify("s3cr3t", body, f"  {signature}  "))

    def test_verify_rejects_tampered_body(self):
        verifier = HmacSha256Verifier()
        signature = _sign("s3cr3t", b"original")
        self.assertFalse(verifier.verify("s3cr3t", b"tampered", signature))

    def test_verify_rejects_wrong_secret(self):
        verifier = HmacSha256Verifier()
        body = b"payload"
        self.assertFalse(verifier.verify("wrong", body, _sign("s3cr3t", body)))

    def test_header_and_prefix_defaults(self):
        verifier = HmacSha256Verifier()
        self.assertEqual(verifier.header, "x-hub-signature-256")
        self.assertEqual(verifier.prefix, "sha256=")


class VerifyWebhookSignatureTests(unittest.TestCase):
    def test_unconfigured_when_no_secret(self):
        body = b"payload"
        headers = {"x-hub-signature-256": _sign("s3cr3t", body)}
        self.assertEqual(
            verify_webhook_signature("code_review", body, headers, env={}),
            "unconfigured",
        )

    def test_unconfigured_when_no_signature_header(self):
        env = {"CODEKB_DIAGNOSE_WEBHOOK_SIGNING_SECRET": "s3cr3t"}
        self.assertEqual(
            verify_webhook_signature("code_review", b"payload", {}, env=env),
            "unconfigured",
        )

    def test_verified_with_shared_secret(self):
        body = b'{"event": "build_failed"}'
        env = {"CODEKB_DIAGNOSE_WEBHOOK_SIGNING_SECRET": "shared-secret"}
        headers = {"x-hub-signature-256": _sign("shared-secret", body)}
        self.assertEqual(
            verify_webhook_signature("code_review", body, headers, env=env),
            "verified",
        )

    def test_enforce_mode_rejects_missing_signature(self):
        env = {
            "CODEKB_DIAGNOSE_WEBHOOK_SIGNING_SECRET": "s3cr3t",
            "CODEKB_DIAGNOSE_WEBHOOK_SIGNING_ENFORCE": "1",
        }
        with self.assertRaises(PermissionError):
            verify_webhook_signature("code_review", b"payload", {}, env=env)

    def test_enforce_mode_is_noop_without_secret(self):
        env = {"CODEKB_DIAGNOSE_WEBHOOK_SIGNING_ENFORCE": "1"}
        self.assertEqual(
            verify_webhook_signature("code_review", b"payload", {}, env=env),
            "unconfigured",
        )

    def test_tampered_payload_raises_permission_error(self):
        env = {"CODEKB_DIAGNOSE_WEBHOOK_SIGNING_SECRET": "shared-secret"}
        headers = {"x-hub-signature-256": _sign("shared-secret", b"original")}
        with self.assertRaises(PermissionError):
            verify_webhook_signature("code_review", b"tampered", headers, env=env)

    def test_per_source_secret_overrides_shared(self):
        body = b"payload"
        env = {
            "CODEKB_DIAGNOSE_WEBHOOK_SIGNING_SECRET": "shared-secret",
            "CODEKB_DIAGNOSE_WEBHOOK_SIGNING_SECRET_CODE_BUDDY": "per-source",
        }
        # 用按来源配置的密钥签名,校验通过。
        good = {"x-hub-signature-256": _sign("per-source", body)}
        self.assertEqual(
            verify_webhook_signature("code-buddy", body, good, env=env),
            "verified",
        )
        # 只用共享密钥签名会被拒,因为按来源的密钥优先级更高。
        bad = {"x-hub-signature-256": _sign("shared-secret", body)}
        with self.assertRaises(PermissionError):
            verify_webhook_signature("code-buddy", body, bad, env=env)

    def test_header_lookup_is_case_insensitive(self):
        body = b"payload"
        env = {"CODEKB_DIAGNOSE_WEBHOOK_SIGNING_SECRET": "s3cr3t"}
        headers = {"X-Hub-Signature-256": _sign("s3cr3t", body)}
        self.assertEqual(
            verify_webhook_signature("code_review", body, headers, env=env),
            "verified",
        )

    def test_handles_none_headers(self):
        env = {"CODEKB_DIAGNOSE_WEBHOOK_SIGNING_SECRET": "s3cr3t"}
        self.assertEqual(
            verify_webhook_signature("code_review", b"payload", None, env=env),
            "unconfigured",
        )


if __name__ == "__main__":
    unittest.main()
