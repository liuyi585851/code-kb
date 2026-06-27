from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping


def load_env_file(path: str | Path) -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.exists():
        return values
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = _parse_env_value(value)
    return values


def build_storage_readiness(
    *,
    env: Mapping[str, str] | None = None,
    env_file: str | Path | None = None,
    timeout_seconds: int = 3,
) -> dict[str, Any]:
    effective_env = dict(os.environ if env is None else env)
    if env_file:
        effective_env.update(load_env_file(env_file))

    checks = [
        _postgres_check(effective_env, timeout_seconds=timeout_seconds),
        _qdrant_check(effective_env, timeout_seconds=timeout_seconds),
    ]
    status = _overall_status(check["status"] for check in checks)
    return {
        "status": status,
        "checks": checks,
        "summary": {
            "ok": sum(1 for check in checks if check["status"] == "ok"),
            "deferred": sum(1 for check in checks if check["status"] == "deferred"),
            "pending": sum(1 for check in checks if check["status"].startswith("pending")),
            "error": sum(1 for check in checks if check["status"] == "error"),
        },
        "secret_values_written": False,
    }


def build_qdrant_status(
    *,
    env: Mapping[str, str] | None = None,
    env_file: str | Path | None = None,
    collection: str = "codekb_atoms",
    timeout_seconds: int = 3,
) -> dict[str, Any]:
    effective_env = dict(os.environ if env is None else env)
    if env_file:
        effective_env.update(load_env_file(env_file))
    base_url = (effective_env.get("QDRANT_URL") or effective_env.get("CODEKB_QDRANT_URL") or "").strip().rstrip("/")
    if not base_url:
        return {
            "status": "deferred",
            "message": "Qdrant is not configured.",
            "version": "",
            "collections": [],
            "collection": {"name": collection, "exists": False},
            "secret_values_written": False,
        }
    try:
        root = _get_json(base_url, effective_env, timeout_seconds=timeout_seconds)
        collections_payload = _get_json(f"{base_url}/collections", effective_env, timeout_seconds=timeout_seconds)
        collections = _qdrant_collection_names(collections_payload)
        if collection not in collections:
            return {
                "status": "missing_collection",
                "message": f"Qdrant collection {collection} does not exist.",
                "version": str(root.get("version", "")),
                "collections": collections,
                "collection": {"name": collection, "exists": False},
                "secret_values_written": False,
            }
        detail = _get_json(
            f"{base_url}/collections/{urllib.parse.quote(collection)}",
            effective_env,
            timeout_seconds=timeout_seconds,
        )
        return {
            "status": "ok",
            "message": "Qdrant collection is ready.",
            "version": str(root.get("version", "")),
            "collections": collections,
            "collection": _qdrant_collection_summary(collection, detail),
            "secret_values_written": False,
        }
    except (urllib.error.URLError, TimeoutError, socket.timeout, ValueError) as exc:
        return {
            "status": "error",
            "message": f"Qdrant status failed: {exc.__class__.__name__}",
            "version": "",
            "collections": [],
            "collection": {"name": collection, "exists": False},
            "secret_values_written": False,
        }


def _postgres_check(env: Mapping[str, str], *, timeout_seconds: int) -> dict[str, Any]:
    dsn = _postgres_dsn(env)
    if not dsn:
        return {
            "id": "postgres",
            "status": "deferred",
            "message": "Postgres is not configured.",
            "details": {"configured": False, "dsn": ""},
        }

    details = {"configured": True, "dsn": _redact_url(dsn)}
    try:
        import psycopg  # type: ignore
    except ModuleNotFoundError:
        return {
            "id": "postgres",
            "status": "pending_driver",
            "message": "Install psycopg or provide a runtime image with the Postgres driver.",
            "details": details,
        }

    try:
        with psycopg.connect(dsn, connect_timeout=timeout_seconds) as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cursor:
                cursor.execute("select 1")
                cursor.fetchone()
        return {"id": "postgres", "status": "ok", "message": "Postgres connection is ready.", "details": details}
    except Exception as exc:
        return {
            "id": "postgres",
            "status": "error",
            "message": f"Postgres connection failed: {exc.__class__.__name__}",
            "details": details,
        }


def _qdrant_check(env: Mapping[str, str], *, timeout_seconds: int) -> dict[str, Any]:
    base_url = (env.get("QDRANT_URL") or env.get("CODEKB_QDRANT_URL") or "").strip().rstrip("/")
    if not base_url:
        return {
            "id": "qdrant",
            "status": "deferred",
            "message": "Qdrant is not configured.",
            "details": {"configured": False, "url": ""},
        }

    details: dict[str, Any] = {"configured": True, "url": _redact_url(base_url), "collections": []}
    request = urllib.request.Request(
        f"{base_url}/collections",
        headers=_qdrant_headers(env),
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8", "replace")
            payload = json.loads(raw) if raw.strip() else {}
        details["collections"] = _qdrant_collection_names(payload)
        return {"id": "qdrant", "status": "ok", "message": "Qdrant collections endpoint is reachable.", "details": details}
    except (urllib.error.URLError, TimeoutError, socket.timeout, ValueError) as exc:
        return {
            "id": "qdrant",
            "status": "error",
            "message": f"Qdrant connection failed: {exc.__class__.__name__}",
            "details": details,
        }


def _postgres_dsn(env: Mapping[str, str]) -> str:
    direct = (env.get("POSTGRES_DSN") or env.get("DATABASE_URL") or "").strip()
    if direct:
        return direct
    host = (env.get("POSTGRES_HOST") or "").strip()
    if not host:
        return ""
    port = (env.get("POSTGRES_PORT") or "5432").strip()
    db = (env.get("POSTGRES_DB") or env.get("POSTGRES_DATABASE") or "codekb").strip()
    user = urllib.parse.quote((env.get("POSTGRES_USER") or "").strip())
    password = urllib.parse.quote((env.get("POSTGRES_PASSWORD") or "").strip())
    auth = user
    if password:
        auth = f"{user}:{password}"
    if auth:
        auth += "@"
    return f"postgresql://{auth}{host}:{port}/{db}"


def _qdrant_headers(env: Mapping[str, str]) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    api_key = (env.get("QDRANT_API_KEY") or env.get("CODEKB_QDRANT_API_KEY") or "").strip()
    if api_key:
        headers["api-key"] = api_key
    return headers


def _qdrant_collection_names(payload: Mapping[str, Any]) -> list[str]:
    result = payload.get("result") if isinstance(payload, Mapping) else {}
    collections = result.get("collections", []) if isinstance(result, Mapping) else []
    names = []
    for item in collections:
        if isinstance(item, Mapping) and item.get("name"):
            names.append(str(item["name"]))
    return names


def _get_json(url: str, env: Mapping[str, str], *, timeout_seconds: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=_qdrant_headers(env), method="GET")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read().decode("utf-8", "replace")
    payload = json.loads(raw) if raw.strip() else {}
    if not isinstance(payload, dict):
        raise ValueError("Qdrant response is not an object")
    return payload


def _qdrant_collection_summary(collection: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    result = payload.get("result") if isinstance(payload, Mapping) else {}
    if not isinstance(result, Mapping):
        result = {}
    config = result.get("config") if isinstance(result.get("config"), Mapping) else {}
    params = config.get("params") if isinstance(config.get("params"), Mapping) else {}
    vectors = params.get("vectors") if isinstance(params.get("vectors"), Mapping) else {}
    payload_schema = result.get("payload_schema") if isinstance(result.get("payload_schema"), Mapping) else {}
    return {
        "name": collection,
        "exists": True,
        "status": str(result.get("status", "")),
        "optimizer_status": str(result.get("optimizer_status", "")),
        "points_count": int(result.get("points_count") or 0),
        "indexed_vectors_count": int(result.get("indexed_vectors_count") or 0),
        "segments_count": int(result.get("segments_count") or 0),
        "vector_size": int(vectors.get("size") or 0),
        "distance": str(vectors.get("distance", "")),
        "on_disk_payload": bool(params.get("on_disk_payload", False)),
        "payload_fields": sorted(str(key) for key in payload_schema.keys()),
    }


def _overall_status(statuses) -> str:
    status_list = list(statuses)
    if any(status == "error" for status in status_list):
        return "degraded"
    if all(status == "deferred" for status in status_list):
        return "deferred"
    if any(status.startswith("pending") for status in status_list):
        return "pending_external_inputs"
    return "ready"


def _parse_env_value(value: str) -> str:
    parsed = value.strip()
    if len(parsed) >= 2 and parsed[0] == parsed[-1] and parsed[0] in ('"', "'"):
        return parsed[1:-1]
    return parsed


def _redact_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return value
    try:
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        return f"{parsed.scheme}://***"
    username = urllib.parse.quote(parsed.username or "")
    auth = ""
    if username:
        auth = f"{username}:***@"
    redacted = parsed._replace(netloc=f"{auth}{host}{port}", query="", fragment="")
    return urllib.parse.urlunsplit(redacted)


def qdrant_admin_overview(env: Mapping[str, str], *, timeout_seconds: int = 4) -> dict[str, Any]:
    """列出 Qdrant 的所有 collection,并给出每个的概览(点数/向量维度/状态)。"""
    effective = dict(env)
    base_url = (effective.get("QDRANT_URL") or effective.get("CODEKB_QDRANT_URL") or "").strip().rstrip("/")
    if not base_url:
        return {"configured": False, "collections": []}
    try:
        names = _qdrant_collection_names(_get_json(f"{base_url}/collections", effective, timeout_seconds=timeout_seconds))
    except (urllib.error.URLError, TimeoutError, socket.timeout, ValueError) as exc:
        return {"configured": True, "url": _redact_url(base_url), "error": str(exc), "collections": []}
    cols: list[dict[str, Any]] = []
    for name in names:
        try:
            payload = _get_json(
                f"{base_url}/collections/{urllib.parse.quote(name)}", effective, timeout_seconds=timeout_seconds
            )
            summary = _qdrant_collection_summary(name, payload)
            result = payload.get("result") if isinstance(payload, Mapping) else {}
            result = result if isinstance(result, Mapping) else {}
            opt = result.get("optimizer_status")
            summary.update(
                {
                    "indexed": result.get("indexed_vectors_count"),
                    "segments": result.get("segments_count"),
                    "optimizer": opt if isinstance(opt, str) else (opt.get("status") if isinstance(opt, Mapping) else None),
                }
            )
            cols.append(summary)
        except (urllib.error.URLError, TimeoutError, socket.timeout, ValueError):
            cols.append({"name": name, "error": "collection info failed"})
    telemetry: dict[str, Any] = {}
    try:
        tele = _get_json(f"{base_url}/telemetry", effective, timeout_seconds=timeout_seconds).get("result", {})
        app = tele.get("app") if isinstance(tele.get("app"), Mapping) else {}
        telemetry = {"version": app.get("version"), "collections": len(names)}
    except (urllib.error.URLError, TimeoutError, socket.timeout, ValueError):
        telemetry = {}
    return {"configured": True, "url": _redact_url(base_url), "collections": cols, "telemetry": telemetry}


def qdrant_sample(
    collection: str, *, env: Mapping[str, str], limit: int = 10, sub_kb: str = "", timeout_seconds: int = 5
) -> dict[str, Any]:
    """翻取若干个点(只取 payload,不取向量),供管理端预览内容。"""
    effective = dict(env)
    base_url = (effective.get("QDRANT_URL") or effective.get("CODEKB_QDRANT_URL") or "").strip().rstrip("/")
    if not base_url:
        return {"collection": collection, "points": [], "configured": False}
    body: dict[str, Any] = {"limit": max(1, min(int(limit), 50)), "with_payload": True, "with_vector": False}
    if sub_kb:
        body["filter"] = {"must": [{"key": "sub_kb_id", "match": {"value": sub_kb}}]}
    request = urllib.request.Request(
        f"{base_url}/collections/{urllib.parse.quote(collection)}/points/scroll",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={**_qdrant_headers(effective), "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, socket.timeout, ValueError) as exc:
        return {"collection": collection, "points": [], "error": str(exc)}
    points = ((data.get("result") or {}).get("points")) or []
    return {
        "collection": collection,
        "points": [{"id": p.get("id"), "payload": p.get("payload")} for p in points],
    }
