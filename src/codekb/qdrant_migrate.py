"""Qdrant 向量维度蓝绿迁移 + 别名回滚(B7)。

换 embedding 维度就意味着要新建一个 Qdrant collection(维度不同的向量没法原地重建索引)。
本模块新建一个 ``{alias}_{model}_{dim}_{suffix}`` collection,把 points 灌进去
(复用 ``storage_sync.sync_qdrant_points``),再用一次 ``/collections/aliases`` 的
actions 批量请求,原子地把对外服务的别名切到新 collection。旧 collection 最多保留
``keep_old`` 个以便快速回滚;``rollback_alias`` 负责把别名切回去。

确定性:这里不用时钟、不用随机 —— ``suffix`` 由调用方传入(时间戳或 build id)。
``execute=False`` 只返回完整计划,不发起任何网络 I/O。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .storage_sync import sync_qdrant_points

__all__ = ["migrate_collection", "rollback_alias", "build_collection_name"]

_SANITIZE_RE = re.compile(r"[^A-Za-z0-9]+")

MIGRATION_STEPS = ("create_collection", "upsert_points", "alias_swap", "cleanup")


def build_collection_name(alias: str, model_id: str, vector_size: int, suffix: str) -> str:
    model = _SANITIZE_RE.sub("-", model_id.strip().lower()).strip("-") or "model"
    safe_suffix = _SANITIZE_RE.sub("-", str(suffix).strip().lower()).strip("-")
    return f"{alias}_{model}_{vector_size}_{safe_suffix}"


def migrate_collection(
    *,
    url: str,
    api_key: str = "",
    alias: str = "codekb_atoms",
    points_path: str | Path,
    vector_size: int,
    model_id: str,
    suffix: str,
    execute: bool = False,
    keep_old: int = 2,
    batch_size: int = 64,
    timeout_seconds: int = 20,
    opener: Any | None = None,
) -> dict[str, Any]:
    if not url:
        raise ValueError("Qdrant url is required")
    if not alias:
        raise ValueError("alias is required")
    if vector_size <= 0:
        raise ValueError("vector_size must be positive")
    if not suffix:
        raise ValueError("suffix is required (no clock/random is used)")
    if keep_old < 0:
        raise ValueError("keep_old must be >= 0")

    new_collection = build_collection_name(alias, model_id, vector_size, suffix)
    base_url = url.rstrip("/")
    headers = _headers(api_key)

    upsert_plan = sync_qdrant_points(
        points_path=points_path,
        url=url,
        collection=new_collection,
        vector_size=vector_size,
        api_key=api_key,
        execute=execute,
        batch_size=batch_size,
        timeout_seconds=timeout_seconds,
    )

    report: dict[str, Any] = {
        "status": "planned",
        "execute": execute,
        "alias": alias,
        "new_collection": new_collection,
        "old_collection": None,
        "vector_size": vector_size,
        "model_id": model_id,
        "suffix": suffix,
        "keep_old": keep_old,
        "steps": list(MIGRATION_STEPS),
        "upsert": upsert_plan,
        "alias_actions": _swap_actions(alias, new_collection),
        "deleted_collections": [],
    }

    if not execute:
        # 不走网络:整个计划无需联系 Qdrant 就能推导出来。
        return report

    if upsert_plan.get("status") != "synced":
        return {**report, "status": "failed", "error": "upsert_failed"}

    try:
        old_collection = _alias_target(base_url, alias, headers=headers, timeout_seconds=timeout_seconds, opener=opener)
        report["old_collection"] = old_collection
        _put_json(
            f"{base_url}/collections/aliases",
            {"actions": _swap_actions(alias, new_collection)},
            headers=headers,
            timeout_seconds=timeout_seconds,
            opener=opener,
        )
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {**report, "status": "failed", "error": exc.__class__.__name__}

    # 上面的别名切换已经提交,迁移其实已经落地。
    # 清理旧 collection 只是尽力而为:清理失败绝不能算作迁移失败
    #(否则可能触发对一个已经上线的 collection 的无谓回滚),只记成非致命的 cleanup_error。
    try:
        report["deleted_collections"] = _cleanup_old_collections(
            base_url,
            alias=alias,
            new_collection=new_collection,
            keep_old=keep_old,
            headers=headers,
            timeout_seconds=timeout_seconds,
            opener=opener,
        )
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        report["cleanup_error"] = exc.__class__.__name__

    return {**report, "status": "migrated"}


def rollback_alias(
    *,
    url: str,
    api_key: str = "",
    alias: str = "codekb_atoms",
    target_collection: str,
    execute: bool = False,
    timeout_seconds: int = 20,
    opener: Any | None = None,
) -> dict[str, Any]:
    if not url:
        raise ValueError("Qdrant url is required")
    if not alias:
        raise ValueError("alias is required")
    if not target_collection:
        raise ValueError("target_collection is required")

    actions = _swap_actions(alias, target_collection)
    report: dict[str, Any] = {
        "status": "planned",
        "execute": execute,
        "alias": alias,
        "target_collection": target_collection,
        "alias_actions": actions,
    }
    if not execute:
        return report

    base_url = url.rstrip("/")
    headers = _headers(api_key)
    try:
        _put_json(
            f"{base_url}/collections/aliases",
            {"actions": actions},
            headers=headers,
            timeout_seconds=timeout_seconds,
            opener=opener,
        )
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {**report, "status": "failed", "error": exc.__class__.__name__}
    return {**report, "status": "rolled_back"}


def _swap_actions(alias: str, new_collection: str) -> list[dict[str, Any]]:
    # 单次 /collections/aliases 请求里保证原子:在一个有序的 actions 批次里
    # 先解掉旧绑定,再把别名绑到新 collection 上。
    return [
        {"delete_alias": {"alias_name": alias}},
        {"create_alias": {"collection_name": new_collection, "alias_name": alias}},
    ]


def _alias_target(
    base_url: str,
    alias: str,
    *,
    headers: dict[str, str],
    timeout_seconds: int,
    opener: Any | None,
) -> str | None:
    data = _get_json(f"{base_url}/aliases", headers=headers, timeout_seconds=timeout_seconds, opener=opener)
    if not isinstance(data, dict):
        return None
    aliases = ((data.get("result") or {}).get("aliases"))
    if not isinstance(aliases, list):
        return None
    for entry in aliases:
        if isinstance(entry, dict) and entry.get("alias_name") == alias:
            collection = entry.get("collection_name")
            if isinstance(collection, str) and collection:
                return collection
    return None


def _cleanup_old_collections(
    base_url: str,
    *,
    alias: str,
    new_collection: str,
    keep_old: int,
    headers: dict[str, str],
    timeout_seconds: int,
    opener: Any | None,
) -> list[str]:
    data = _get_json(f"{base_url}/collections", headers=headers, timeout_seconds=timeout_seconds, opener=opener)
    names: list[str] = []
    if isinstance(data, dict):
        collections = ((data.get("result") or {}).get("collections"))
        if isinstance(collections, list):
            for entry in collections:
                if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                    names.append(entry["name"])

    prefix = f"{alias}_"
    candidates = [
        name for name in names if name.startswith(prefix) and name != new_collection
    ]
    # 按从新到旧排(suffix 是可排序的时间戳/build id),保留前 keep_old 个。
    candidates.sort(reverse=True)
    to_delete = candidates[keep_old:]
    deleted: list[str] = []
    for name in to_delete:
        _delete_collection(
            f"{base_url}/collections/{urllib.parse.quote(name)}",
            headers=headers,
            timeout_seconds=timeout_seconds,
            opener=opener,
        )
        deleted.append(name)
    return deleted


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["api-key"] = api_key
    return headers


def _open(request: urllib.request.Request, *, timeout_seconds: int, opener: Any | None):
    if opener is not None:
        return opener.open(request, timeout=timeout_seconds)
    return urllib.request.urlopen(request, timeout=timeout_seconds)


def _get_json(url: str, *, headers: dict[str, str], timeout_seconds: int, opener: Any | None) -> Any:
    request = urllib.request.Request(url, method="GET", headers={**headers, "Accept": "application/json"})
    try:
        response = _open(request, timeout_seconds=timeout_seconds, opener=opener)
    except urllib.error.HTTPError as exc:
        exc.close()
        return None
    with response:
        return json.loads(response.read().decode("utf-8"))


def _put_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout_seconds: int,
    opener: Any | None,
) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="PUT",
        headers={**headers, "Content-Type": "application/json", "Content-Length": str(len(body))},
    )
    with _open(request, timeout_seconds=timeout_seconds, opener=opener) as response:
        if response.status >= 400:
            raise urllib.error.HTTPError(url, response.status, "Qdrant request failed", response.headers, None)
        response.read()


def _delete_collection(url: str, *, headers: dict[str, str], timeout_seconds: int, opener: Any | None) -> None:
    request = urllib.request.Request(url, method="DELETE", headers={**headers, "Accept": "application/json"})
    with _open(request, timeout_seconds=timeout_seconds, opener=opener) as response:
        if response.status >= 400:
            raise urllib.error.HTTPError(url, response.status, "Qdrant delete failed", response.headers, None)
        response.read()
