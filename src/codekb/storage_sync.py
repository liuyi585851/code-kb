from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .storage_integrations import load_env_file


def sync_qdrant_points(
    *,
    points_path: str | Path,
    url: str,
    collection: str = "codekb_atoms",
    vector_size: int = 64,
    api_key: str = "",
    execute: bool = False,
    batch_size: int = 64,
    timeout_seconds: int = 20,
    recreate: bool = False,
) -> dict[str, Any]:
    points = _load_jsonl(points_path)
    if vector_size <= 0:
        raise ValueError("vector_size must be positive")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if not collection:
        raise ValueError("collection is required")
    if not url:
        raise ValueError("Qdrant url is required")

    report: dict[str, Any] = {
        "status": "planned",
        "execute": execute,
        "points": len(points),
        "collection": collection,
        "url": _redact_url(url),
        "vector_size": vector_size,
        "existing_vector_size": None,
        "recreate": recreate,
        "batches": (len(points) + batch_size - 1) // batch_size if points else 0,
        "secret_values_written": False,
    }
    if not execute:
        return report

    base_url = url.rstrip("/")
    headers = _headers(api_key)
    collection_url = f"{base_url}/collections/{urllib.parse.quote(collection)}"

    existing_vector_size = _get_collection_vector_size(
        collection_url, headers=headers, timeout_seconds=timeout_seconds
    )
    report["existing_vector_size"] = existing_vector_size

    # B6:向量维度和目标维度不一致的 collection 一律拒绝写入。不加 recreate 就完全
    # 不动数据(避免静默截断 / 污染向量);加了 recreate 则先删后重建。
    dimension_mismatch = existing_vector_size is not None and existing_vector_size != vector_size
    if dimension_mismatch and not recreate:
        return {**report, "status": "dimension_mismatch"}

    try:
        if dimension_mismatch and recreate:
            _delete_collection(collection_url, headers=headers, timeout_seconds=timeout_seconds)
        _put_json(
            collection_url,
            {
                "vectors": {
                    "size": vector_size,
                    "distance": "Cosine",
                }
            },
            headers=headers,
            timeout_seconds=timeout_seconds,
        )
        for batch in _batches(points, batch_size):
            _put_json(
                f"{collection_url}/points?wait=true",
                {"points": batch},
                headers=headers,
                timeout_seconds=timeout_seconds,
            )
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {
            **report,
            "status": "failed",
            "error": exc.__class__.__name__,
        }
    return {**report, "status": "synced"}


def sync_qdrant_points_from_env(
    *,
    points_path: str | Path,
    env_file: str | Path | None = None,
    env: dict[str, str] | None = None,
    collection: str = "codekb_atoms",
    vector_size: int = 64,
    execute: bool = False,
    batch_size: int = 64,
    timeout_seconds: int = 20,
    recreate: bool = False,
) -> dict[str, Any]:
    import os

    effective = dict(os.environ if env is None else env)
    if env_file:
        effective.update(load_env_file(env_file))
    return sync_qdrant_points(
        points_path=points_path,
        url=(effective.get("QDRANT_URL") or effective.get("CODEKB_QDRANT_URL") or "").strip(),
        collection=collection,
        vector_size=vector_size,
        api_key=(effective.get("QDRANT_API_KEY") or effective.get("CODEKB_QDRANT_API_KEY") or "").strip(),
        execute=execute,
        batch_size=batch_size,
        timeout_seconds=timeout_seconds,
        recreate=recreate,
    )


def sync_opensearch_documents(
    *,
    documents_path: str | Path,
    url: str,
    index: str | None = None,
    api_key: str = "",
    execute: bool = False,
    batch_size: int = 100,
    timeout_seconds: int = 20,
    transport: Any | None = None,
) -> dict[str, Any]:
    documents = _load_jsonl(documents_path)
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if not url:
        raise ValueError("OpenSearch url is required")
    for line_no, doc in enumerate(documents, start=1):
        if not doc.get("_id"):
            raise ValueError(f"document {line_no} is missing _id")
        if "_source" not in doc:
            raise ValueError(f"document {line_no} is missing _source")

    report: dict[str, Any] = {
        "status": "planned",
        "execute": execute,
        "documents": len(documents),
        "index": index or _default_opensearch_index(documents),
        "url": _redact_url(url),
        "batches": (len(documents) + batch_size - 1) // batch_size if documents else 0,
        "secret_values_written": False,
    }
    if not execute:
        return report

    base_url = url.rstrip("/")
    headers = _opensearch_headers(api_key)
    sender = transport if transport is not None else _UrllibOpenSearchTransport()

    synced = 0
    failed = 0
    try:
        for batch in _batches(documents, batch_size):
            body = _bulk_body(batch, index)
            response = sender.bulk(
                url=f"{base_url}/_bulk",
                body=body,
                headers=headers,
                timeout_seconds=timeout_seconds,
            )
            ok, bad = _count_bulk_items(response, len(batch))
            synced += ok
            failed += bad
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {
            **report,
            "status": "failed",
            "error": exc.__class__.__name__,
            "synced": synced,
            "failed": len(documents) - synced,
        }

    status = "synced" if failed == 0 else ("partial" if synced > 0 else "failed")
    return {**report, "status": status, "synced": synced, "failed": failed}


def sync_opensearch_documents_from_env(
    *,
    documents_path: str | Path,
    env_file: str | Path | None = None,
    env: dict[str, str] | None = None,
    index: str | None = None,
    execute: bool = False,
    batch_size: int = 100,
    timeout_seconds: int = 20,
    transport: Any | None = None,
) -> dict[str, Any]:
    import os

    effective = dict(os.environ if env is None else env)
    if env_file:
        effective.update(load_env_file(env_file))
    url = (effective.get("OPENSEARCH_URL") or effective.get("CODEKB_OPENSEARCH_URL") or "").strip()
    if not url:
        # OpenSearch 是可选的:没配端点就干净跳过,而不是让整个外部同步失败
        # (零行为偏差)。
        return {
            "status": "skipped",
            "reason": "opensearch_url_not_configured",
            "execute": execute,
            "documents": 0,
            "secret_values_written": False,
        }
    return sync_opensearch_documents(
        documents_path=documents_path,
        url=url,
        index=index,
        api_key=(effective.get("OPENSEARCH_API_KEY") or effective.get("CODEKB_OPENSEARCH_API_KEY") or "").strip(),
        execute=execute,
        batch_size=batch_size,
        timeout_seconds=timeout_seconds,
        transport=transport,
    )


def sync_postgres_upserts(
    *,
    upserts_path: str | Path,
    dsn: str,
    execute: bool = False,
    connect: Any | None = None,
) -> dict[str, Any]:
    upserts = _load_jsonl(upserts_path)
    if not dsn:
        raise ValueError("Postgres DSN is required")

    targets: dict[str, int] = {}
    for row in upserts:
        target = str(row.get("target", "") or "")
        targets[target] = targets.get(target, 0) + 1
        if not row.get("sql"):
            raise ValueError("upsert row sql is required")
        params = row.get("params", [])
        if not isinstance(params, list):
            raise ValueError("upsert row params must be a list")

    report: dict[str, Any] = {
        "status": "planned",
        "execute": execute,
        "upserts": len(upserts),
        "targets": targets,
        "dsn": _redact_url(dsn),
        "secret_values_written": False,
    }
    if not execute:
        return report

    connector = connect or _default_postgres_connect
    try:
        with _postgres_connection(connector, dsn) as conn:
            with conn.cursor() as cur:
                for row in upserts:
                    cur.execute(str(row["sql"]), tuple(row.get("params", [])))
            conn.commit()
    except Exception as exc:
        return {
            **report,
            "status": "failed",
            "error": exc.__class__.__name__,
        }
    return {**report, "status": "synced"}


def sync_postgres_upserts_from_env(
    *,
    upserts_path: str | Path,
    env_file: str | Path | None = None,
    env: dict[str, str] | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    import os

    effective = dict(os.environ if env is None else env)
    if env_file:
        effective.update(load_env_file(env_file))
    return sync_postgres_upserts(
        upserts_path=upserts_path,
        dsn=(effective.get("POSTGRES_DSN") or effective.get("DATABASE_URL") or "").strip(),
        execute=execute,
    )


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    for line_no, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"line {line_no} is not an object")
        rows.append(row)
    return rows


def _default_postgres_connect(dsn: str):
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        raise RuntimeError("psycopg is required for Postgres sync") from exc

    return psycopg.connect(dsn)


def _postgres_connection(connect: Any, dsn: str):
    from contextlib import closing

    connection = connect(dsn)
    if hasattr(connection, "__enter__"):
        return connection
    return closing(connection)


def _put_json(url: str, payload: dict[str, Any], *, headers: dict[str, str], timeout_seconds: int) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="PUT",
        headers={**headers, "Content-Type": "application/json", "Content-Length": str(len(body))},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        if response.status >= 400:
            raise urllib.error.HTTPError(url, response.status, "Qdrant request failed", response.headers, None)
        response.read()


def _get_collection_vector_size(
    collection_url: str, *, headers: dict[str, str], timeout_seconds: int
) -> int | None:
    """返回 Qdrant collection 现有默认向量的维度。

    collection 不存在、维度无法确定(命名向量 / 非预期 schema)或读取失败时返回
    ``None``。调用方把 ``None`` 当作"没有现成 collection",继续走创建流程。
    """

    request = urllib.request.Request(
        collection_url,
        method="GET",
        headers={**headers, "Accept": "application/json"},
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout_seconds)
    except urllib.error.HTTPError as exc:
        # 比如 404(collection 不存在)或 501,都当作"没有现成维度"。
        exc.close()
        return None
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    try:
        with response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        return None
    vectors = (((result.get("config") or {}).get("params") or {}).get("vectors"))
    if isinstance(vectors, dict) and isinstance(vectors.get("size"), int):
        return int(vectors["size"])
    return None


def _delete_collection(collection_url: str, *, headers: dict[str, str], timeout_seconds: int) -> None:
    request = urllib.request.Request(
        collection_url,
        method="DELETE",
        headers={**headers, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        if response.status >= 400:
            raise urllib.error.HTTPError(
                collection_url, response.status, "Qdrant delete failed", response.headers, None
            )
        response.read()


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["api-key"] = api_key
    return headers


def _opensearch_headers(api_key: str) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"ApiKey {api_key}"
    return headers


def _default_opensearch_index(documents: list[dict[str, Any]]) -> str:
    for doc in documents:
        index = doc.get("_index")
        if isinstance(index, str) and index:
            return index
    return "codekb_atoms"


def _bulk_body(batch: list[dict[str, Any]], index_override: str | None) -> str:
    lines: list[str] = []
    for doc in batch:
        index = index_override or doc.get("_index") or "codekb_atoms"
        action = {"index": {"_index": index, "_id": doc["_id"]}}
        lines.append(json.dumps(action, ensure_ascii=False))
        lines.append(json.dumps(doc.get("_source", {}), ensure_ascii=False, sort_keys=True))
    return "\n".join(lines) + "\n"


def _count_bulk_items(response: Any, batch_len: int) -> tuple[int, int]:
    if not isinstance(response, dict):
        return 0, batch_len
    items = response.get("items")
    if not isinstance(items, list):
        # 没有逐条明细,就以顶层的 errors 标志为准。
        if response.get("errors") is False:
            return batch_len, 0
        return 0, batch_len
    ok = 0
    bad = 0
    for item in items:
        op = next(iter(item.values()), {}) if isinstance(item, dict) and item else {}
        status = op.get("status") if isinstance(op, dict) else None
        if isinstance(status, int) and 200 <= status < 300:
            ok += 1
        else:
            bad += 1
    return ok, bad


class _UrllibOpenSearchTransport:
    def bulk(self, *, url: str, body: str, headers: dict[str, str], timeout_seconds: int) -> Any:
        data = body.encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                **headers,
                "Content-Type": "application/x-ndjson",
                "Content-Length": str(len(data)),
            },
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            if response.status >= 400:
                raise urllib.error.HTTPError(
                    url, response.status, "OpenSearch bulk failed", response.headers, None
                )
            return json.loads(response.read().decode("utf-8"))


def _batches(points: list[dict[str, Any]], batch_size: int):
    for offset in range(0, len(points), batch_size):
        yield points[offset : offset + batch_size]


def _redact_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return value
    netloc = parsed.netloc
    # 只要 userinfo 里带任何凭据,就把整段抹掉。
    # 像 https://<TOKEN>@host 这种只有 token 的 URL,username 有值而 password
    # 为 None,只看 password 会漏掉 token。
    if parsed.username is not None or parsed.password is not None:
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port is not None else ""
        netloc = f"***@{host}{port}"
    redacted = parsed._replace(query="", fragment="")
    return urllib.parse.urlunsplit(redacted._replace(netloc=netloc))
