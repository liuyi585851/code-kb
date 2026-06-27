from __future__ import annotations

import base64
import hmac
import json
import secrets
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4


DEFAULT_TOKEN_TTL_DAYS = 30
SENSITIVE_TOKEN_METADATA_KEYS = {
    "im_userid",
    "im_user_id",
    "userid",
    "user_id",
    "open_userid",
    "im_robot_key",
    "im_robot_webhook",
    "im_message_target",
    "contact_route",
    "route_value",
}
DEFAULT_IM_API_BASE = "https://im-api.example.com/cgi-bin"
DEFAULT_IM_OAUTH_AUTHORIZE_BASE = "https://im-oauth.example.com/connect/oauth2/authorize"
DEFAULT_IM_OAUTH_SCOPE = "snsapi_base"
DEFAULT_IM_OAUTH_STATE_MAX_AGE_SECONDS = 900


@dataclass(frozen=True)
class UserTokenBinding:
    token_id: str
    created_at: str
    expires_at: str
    revoked_at: str
    user_id_hash: str
    token_hash: str
    display_name: str
    scopes: tuple[str, ...]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
            "user_id_hash": self.user_id_hash,
            "token_hash": self.token_hash,
            "display_name": self.display_name,
            "scopes": list(self.scopes),
            "metadata": dict(self.metadata),
        }


class JsonUserTokenStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def issue(
        self,
        *,
        user_id_hash: str,
        display_name: str = "",
        scopes: tuple[str, ...] | list[str] | None = None,
        ttl_days: int = DEFAULT_TOKEN_TTL_DAYS,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_user = str(user_id_hash or "").strip()
        if not normalized_user:
            raise ValueError("user_id_hash is required")
        if ttl_days < 1 or ttl_days > 366:
            raise ValueError("ttl_days must be between 1 and 366")
        token = "lkb_" + secrets.token_urlsafe(32)
        now = _now()
        binding = UserTokenBinding(
            token_id=str(uuid4()),
            created_at=now,
            expires_at=_after_days(ttl_days),
            revoked_at="",
            user_id_hash=normalized_user,
            token_hash=_token_hash(token),
            display_name=str(display_name or "").strip(),
            scopes=_scopes_tuple(scopes),
            metadata=dict(metadata or {}),
        )
        bindings = [*self._load(), binding]
        self._write(bindings)
        return {
            "token": token,
            "binding": binding.to_dict(),
        }

    def validate(self, token: str) -> UserTokenBinding | None:
        token_hash = _token_hash(str(token or "").strip())
        if not token_hash:
            return None
        now = _parse_time(_now())
        for binding in self._load():
            if binding.token_hash != token_hash:
                continue
            if binding.revoked_at:
                return None
            if binding.expires_at and _parse_time(binding.expires_at) < now:
                return None
            return binding
        return None

    def revoke(self, token_id: str) -> UserTokenBinding:
        normalized_id = str(token_id or "").strip()
        if not normalized_id:
            raise ValueError("token_id is required")
        bindings = list(self._load())
        updated: list[UserTokenBinding] = []
        revoked: UserTokenBinding | None = None
        for binding in bindings:
            if binding.token_id == normalized_id and not binding.revoked_at:
                revoked = UserTokenBinding(**{**binding.to_dict(), "revoked_at": _now(), "scopes": tuple(binding.scopes)})
                updated.append(revoked)
            else:
                updated.append(binding)
        if revoked is None:
            raise ValueError("active token binding not found")
        self._write(updated)
        return revoked

    def list(self) -> tuple[UserTokenBinding, ...]:
        return self._load()

    def summary(self) -> dict[str, Any]:
        bindings = self._load()
        active = tuple(binding for binding in bindings if self.validate_hash(binding.token_hash))
        return {
            "path": str(self.path),
            "total": len(bindings),
            "active": len(active),
            "revoked": sum(1 for binding in bindings if binding.revoked_at),
            "expired": sum(1 for binding in bindings if binding.expires_at and _parse_time(binding.expires_at) < _parse_time(_now())),
            "bindings": [_public_binding(binding) for binding in bindings],
        }

    def validate_hash(self, token_hash: str) -> bool:
        now = _parse_time(_now())
        for binding in self._load():
            if binding.token_hash != token_hash:
                continue
            return not binding.revoked_at and (not binding.expires_at or _parse_time(binding.expires_at) >= now)
        return False

    def _load(self) -> tuple[UserTokenBinding, ...]:
        if not self.path.exists():
            return ()
        data = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        if not isinstance(data, list):
            raise ValueError("user token store must contain a JSON array")
        bindings: list[UserTokenBinding] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            bindings.append(
                UserTokenBinding(
                    token_id=str(item.get("token_id", "")).strip(),
                    created_at=str(item.get("created_at", "")).strip(),
                    expires_at=str(item.get("expires_at", "")).strip(),
                    revoked_at=str(item.get("revoked_at", "")).strip(),
                    user_id_hash=str(item.get("user_id_hash", "")).strip(),
                    token_hash=str(item.get("token_hash", "")).strip(),
                    display_name=str(item.get("display_name", "")).strip(),
                    scopes=_scopes_tuple(item.get("scopes")),
                    metadata=dict(item.get("metadata") or {}),
                )
            )
        return tuple(bindings)

    def _write(self, bindings: list[UserTokenBinding]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([binding.to_dict() for binding in bindings], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


@dataclass(frozen=True)
class IMOAuthProfile:
    user_id: str
    open_id: str
    device_id: str
    user_ticket: str
    raw: dict[str, Any]

    @property
    def route_user(self) -> str:
        return self.user_id or self.open_id

    @property
    def user_id_hash(self) -> str:
        route_user = self.route_user
        if not route_user:
            return ""
        return sha256(route_user.encode("utf-8")).hexdigest()

    def token_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {"source": "im_oauth"}
        if self.user_id:
            metadata["im_userid"] = self.user_id
        if self.open_id:
            metadata["open_userid"] = self.open_id
        if self.device_id:
            metadata["device_id_hash"] = sha256(self.device_id.encode("utf-8")).hexdigest()
        if self.user_ticket:
            metadata["has_user_ticket"] = True
        return metadata


class IMOAuthClient:
    def __init__(
        self,
        *,
        corp_id: str,
        app_secret: str,
        agent_id: str,
        api_base: str = DEFAULT_IM_API_BASE,
        authorize_base: str = DEFAULT_IM_OAUTH_AUTHORIZE_BASE,
        get_json: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.corp_id = str(corp_id or "").strip()
        self.app_secret = str(app_secret or "").strip()
        self.agent_id = str(agent_id or "").strip()
        self.api_base = str(api_base or DEFAULT_IM_API_BASE).rstrip("/")
        self.authorize_base = str(authorize_base or DEFAULT_IM_OAUTH_AUTHORIZE_BASE).rstrip("/")
        self._get_json = get_json or _get_json
        self._access_token = ""

    def configured(self) -> bool:
        return bool(self.corp_id and self.app_secret and self.agent_id)

    def authorize_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        scope: str = DEFAULT_IM_OAUTH_SCOPE,
    ) -> str:
        if not self.corp_id:
            raise ValueError("CODEKB_IM_CORP_ID is required")
        if not self.agent_id:
            raise ValueError("CODEKB_IM_AGENT_ID is required")
        normalized_redirect = str(redirect_uri or "").strip()
        if not normalized_redirect:
            raise ValueError("IM OAuth redirect_uri is required")
        query = urllib.parse.urlencode(
            {
                "appid": self.corp_id,
                "redirect_uri": normalized_redirect,
                "response_type": "code",
                "scope": str(scope or DEFAULT_IM_OAUTH_SCOPE).strip() or DEFAULT_IM_OAUTH_SCOPE,
                "state": str(state or "").strip(),
                "agentid": self.agent_id,
            }
        )
        return f"{self.authorize_base}?{query}#im_redirect"

    def exchange_code(self, code: str) -> IMOAuthProfile:
        normalized_code = str(code or "").strip()
        if not normalized_code:
            raise ValueError("IM OAuth code is required")
        token = self._get_access_token()
        query = urllib.parse.urlencode({"access_token": token, "code": normalized_code})
        response = self._get_json(f"{self.api_base}/auth/getuserinfo?{query}")
        if int(response.get("errcode", 0) or 0) != 0:
            raise RuntimeError(f"IM auth/getuserinfo failed: {response.get('errmsg', response)}")
        profile = IMOAuthProfile(
            user_id=str(response.get("UserId", "") or response.get("userid", "") or "").strip(),
            open_id=str(response.get("OpenId", "") or response.get("openid", "") or "").strip(),
            device_id=str(response.get("DeviceId", "") or response.get("deviceid", "") or "").strip(),
            user_ticket=str(response.get("user_ticket", "") or "").strip(),
            raw=dict(response),
        )
        if not profile.route_user:
            raise RuntimeError("IM auth/getuserinfo response missing UserId/OpenId")
        return profile

    def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token
        if not self.configured():
            raise ValueError("IM OAuth client is not configured")
        query = urllib.parse.urlencode({"corpid": self.corp_id, "corpsecret": self.app_secret})
        response = self._get_json(f"{self.api_base}/gettoken?{query}")
        if int(response.get("errcode", 0) or 0) != 0:
            raise RuntimeError(f"IM gettoken failed: {response.get('errmsg', response)}")
        token = str(response.get("access_token", "") or "").strip()
        if not token:
            raise RuntimeError("IM gettoken response missing access_token")
        self._access_token = token
        return token


def issue_im_oauth_token(
    store: JsonUserTokenStore,
    profile: IMOAuthProfile,
    *,
    scopes: tuple[str, ...] | list[str] | None = None,
    ttl_days: int = DEFAULT_TOKEN_TTL_DAYS,
    display_name: str = "",
) -> dict[str, Any]:
    if not profile.user_id_hash:
        raise ValueError("IM OAuth profile missing user id")
    issued = store.issue(
        user_id_hash=profile.user_id_hash,
        display_name=display_name or "IM user",
        scopes=scopes or ["diagnose"],
        ttl_days=ttl_days,
        metadata=profile.token_metadata(),
    )
    return issued


def make_im_oauth_state(
    secret: str,
    *,
    next_url: str = "",
    now: int | None = None,
) -> str:
    normalized_secret = str(secret or "")
    if not normalized_secret:
        raise ValueError("IM OAuth state secret is required")
    payload = {
        "created_at": int(time.time() if now is None else now),
        "nonce": secrets.token_urlsafe(12),
        "next": safe_relative_url(next_url),
    }
    encoded = _b64url(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    signature = _state_signature(encoded, normalized_secret)
    return f"{encoded}.{signature}"


def verify_im_oauth_state(
    state: str,
    secret: str,
    *,
    max_age_seconds: int = DEFAULT_IM_OAUTH_STATE_MAX_AGE_SECONDS,
    now: int | None = None,
) -> dict[str, Any]:
    normalized_secret = str(secret or "")
    if not normalized_secret:
        raise ValueError("IM OAuth state secret is required")
    try:
        encoded, signature = str(state or "").split(".", 1)
    except ValueError as exc:
        raise ValueError("invalid IM OAuth state") from exc
    expected = _state_signature(encoded, normalized_secret)
    if not hmac.compare_digest(signature, expected):
        raise ValueError("invalid IM OAuth state signature")
    try:
        payload = json.loads(_b64url_decode(encoded).decode("utf-8"))
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid IM OAuth state payload") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid IM OAuth state payload")
    created_at = int(payload.get("created_at") or 0)
    current = int(time.time() if now is None else now)
    if created_at <= 0 or created_at - current > 60 or current - created_at > max_age_seconds:
        raise ValueError("expired IM OAuth state")
    return {"created_at": created_at, "next": safe_relative_url(str(payload.get("next") or ""))}


def safe_relative_url(value: str) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return ""
    if not candidate.startswith("/") or candidate.startswith("//"):
        return ""
    return candidate


def _public_binding(binding: UserTokenBinding) -> dict[str, Any]:
    return {
        "token_id": binding.token_id,
        "created_at": binding.created_at,
        "expires_at": binding.expires_at,
        "revoked_at": binding.revoked_at,
        "user_id_hash": binding.user_id_hash,
        "token_hash_prefix": binding.token_hash[:12],
        "display_name": binding.display_name,
        "scopes": list(binding.scopes),
        "metadata": public_token_metadata(binding.metadata),
    }


def public_token_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    public: dict[str, Any] = {}
    for key, value in dict(metadata or {}).items():
        normalized_key = str(key)
        if normalized_key.lower() in SENSITIVE_TOKEN_METADATA_KEYS and value:
            public[f"{normalized_key}_hash"] = sha256(str(value).encode("utf-8")).hexdigest()
        else:
            public[normalized_key] = value
    return public


def _token_hash(token: str) -> str:
    if not token:
        return ""
    return sha256(token.encode("utf-8")).hexdigest()


def _scopes_tuple(value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    if value in (None, "", []):
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("scopes must be a list")
    return tuple(str(item).strip() for item in value if str(item).strip())


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _after_days(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _get_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "codekb/0.1"})
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("JSON response must be an object")
    return payload


def _state_signature(encoded_payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), encoded_payload.encode("utf-8"), sha256).digest()
    return _b64url(digest)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))
