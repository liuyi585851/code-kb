"""诊断 webhook 的 HMAC 签名校验:可插拔,按来源(source)区分。

本模块刻意不碰网络、不持有凭据,只提供一个轻量的校验器抽象,新增签名方案时
按 ``source`` 注册即可,不用动 API 层。

接入约定(见 :func:`verify_webhook_signature`):

* ``'unconfigured'`` —— 没配 secret,或请求里没有签名头。这是默认状态,调用方
  当成空操作处理,签名校验因此能灰度上线,不影响现有 webhook 流量。
* ``'verified'`` —— 配了 secret,且请求带的签名头校验通过。
* ``PermissionError`` —— 配了 secret、请求也带了签名头,但对不上(说明有篡改
  或配置错误)。

secret 取值优先用按来源的覆盖配置,其次才用共享配置:

* 按来源:``CODEKB_DIAGNOSE_WEBHOOK_SIGNING_SECRET_<SOURCE>``
* 共享:  ``CODEKB_DIAGNOSE_WEBHOOK_SIGNING_SECRET``

其中 ``<SOURCE>`` 是把 source 转大写、非字母数字字符合并成下划线后的结果
(例如 ``code-buddy`` -> ``CODE_BUDDY``)。
"""

from __future__ import annotations

import hmac
import os
import re
from hashlib import sha256
from typing import Mapping, Optional, Protocol

SHARED_SECRET_ENV = "CODEKB_DIAGNOSE_WEBHOOK_SIGNING_SECRET"
PER_SOURCE_SECRET_ENV_PREFIX = "CODEKB_DIAGNOSE_WEBHOOK_SIGNING_SECRET_"
# 可选开关:配了 secret 但请求没带签名头时,直接拒绝而不是当空操作放过。
# 默认关闭,方便灰度上线。
ENFORCE_ENV = "CODEKB_DIAGNOSE_WEBHOOK_SIGNING_ENFORCE"

UNCONFIGURED = "unconfigured"
VERIFIED = "verified"


class WebhookSignatureVerifier(Protocol):
    """校验器协议:读取自己的签名头,再校验请求体。"""

    header: str

    def verify(self, secret: str, raw_body: bytes, signature: str) -> bool:  # pragma: no cover - 协议占位
        ...


class HmacSha256Verifier:
    """HMAC-SHA256 校验器,兼容常见的 ``sha256=<hex>`` 格式。"""

    header = "x-hub-signature-256"
    prefix = "sha256="

    def verify(self, secret: str, raw_body: bytes, signature: str) -> bool:
        expected = self.prefix + hmac.new(secret.encode(), raw_body, sha256).hexdigest()
        return hmac.compare_digest(expected, signature.strip())


# 默认校验器注册表,按 source 索引。``None`` 是兜底校验器,凡是没单独配置的
# source 都用它。
_DEFAULT_VERIFIER = HmacSha256Verifier()
_VERIFIERS: dict[Optional[str], WebhookSignatureVerifier] = {None: _DEFAULT_VERIFIER}


def _normalize_source(source: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(source or "").strip()).strip("_").upper()


def _verifier_for(source: str) -> WebhookSignatureVerifier:
    return _VERIFIERS.get(source) or _VERIFIERS.get(None) or _DEFAULT_VERIFIER


def _resolve_secret(source: str, env: Mapping[str, str]) -> str:
    normalized = _normalize_source(source)
    if normalized:
        per_source = env.get(PER_SOURCE_SECRET_ENV_PREFIX + normalized, "")
        if per_source and per_source.strip():
            return per_source.strip()
    shared = env.get(SHARED_SECRET_ENV, "")
    return shared.strip() if shared else ""


def _enforce_enabled(env: Mapping[str, str]) -> bool:
    return str(env.get(ENFORCE_ENV, "") or "").strip().lower() in {"1", "true", "yes"}


def _lookup_header(headers, name: str) -> str:
    if headers is None:
        return ""
    # Starlette 的 ``Headers`` 和类 dict 对象都支持 ``.get``;这里做大小写不敏感
    # 的查找,兼容各种 header 容器。
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name)
        if value:
            return str(value)
    lowered = name.lower()
    try:
        items = headers.items()
    except AttributeError:
        return ""
    for key, value in items:
        if str(key).lower() == lowered and value:
            return str(value)
    return ""


def verify_webhook_signature(source, raw_body: bytes, headers, *, env: Optional[Mapping[str, str]] = None) -> str:
    """校验 ``source`` 的 webhook 签名。

    返回 ``'verified'`` 或 ``'unconfigured'``;若配了 secret、请求也带了签名头但
    对不上,则抛出 ``PermissionError``。
    """

    env = os.environ if env is None else env
    verifier = _verifier_for(source)
    secret = _resolve_secret(source, env)
    signature = _lookup_header(headers, verifier.header)
    if not secret:
        # 没东西可校验,直接空操作。
        return UNCONFIGURED
    if not signature:
        # 配了 secret 但请求没签名:强制模式下拒绝,否则当空操作放过,方便签名灰度上线。
        if _enforce_enabled(env):
            raise PermissionError("diagnose webhook signature required but missing")
        return UNCONFIGURED
    body = raw_body if isinstance(raw_body, (bytes, bytearray)) else bytes(raw_body or b"")
    if not verifier.verify(secret, bytes(body), signature):
        raise PermissionError("invalid diagnose webhook signature")
    return VERIFIED
