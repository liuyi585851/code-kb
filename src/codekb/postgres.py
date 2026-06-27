from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .models import AtomDraft, AtomRecord, AtomStatus, Layer, RawDocument


@dataclass(frozen=True)
class SqlStatement:
    sql: str
    params: tuple


class PostgresAtomStore:
    def __init__(self, dsn: str, *, connect: Any | None = None) -> None:
        self.dsn = dsn
        self._connect = connect or _default_connect

    def list_atoms(
        self,
        *,
        sub_kbs: set[str] | None = None,
        source_docids: set[str] | None = None,
    ) -> list[AtomRecord]:
        where: list[str] = []
        params: list[Any] = []
        if sub_kbs:
            where.append("a.sub_kb_id = ANY(%s)")
            params.append(sorted(sub_kbs))
        if source_docids:
            where.append("a.source_docid = ANY(%s)")
            params.append(sorted(source_docids))
        sql = _atom_select_sql(where=where, order_by="a.atom_id")
        with self._connection() as conn:
            with _dict_cursor(conn) as cur:
                cur.execute(sql, tuple(params))
                return [_record_from_postgres_row(row) for row in cur.fetchall()]

    def get(self, atom_id: str) -> AtomRecord:
        sql = _atom_select_sql(where=["a.atom_id = %s"])
        with self._connection() as conn:
            with _dict_cursor(conn) as cur:
                cur.execute(sql, (atom_id,))
                row = cur.fetchone()
        if row is None:
            raise KeyError(atom_id)
        return _record_from_postgres_row(row)

    def __len__(self) -> int:
        with self._connection() as conn:
            with _dict_cursor(conn) as cur:
                cur.execute("SELECT COUNT(*) AS count FROM knowledge_atoms")
                row = cur.fetchone()
        return int(row["count"] if isinstance(row, dict) else row[0])

    def counts_by_sub_kb(self) -> list[dict[str, Any]]:
        sql = "SELECT sub_kb_id, COUNT(*) AS count FROM knowledge_atoms GROUP BY sub_kb_id ORDER BY count DESC"
        with self._connection() as conn:
            with _dict_cursor(conn) as cur:
                cur.execute(sql)
                rows = cur.fetchall()
        return [{"sub_kb_id": str(_row_value(r, "sub_kb_id")), "count": int(_row_value(r, "count"))} for r in rows]

    def all_source_docids(self, *, sub_kbs: set[str] | None = None) -> list[str]:
        """返回去重后的 source_docid(文件路径/文档 id),供结构化导航使用。"""
        sql = "SELECT DISTINCT source_docid FROM knowledge_atoms"
        params: tuple = ()
        if sub_kbs:
            sql += " WHERE sub_kb_id = ANY(%s)"
            params = (sorted(sub_kbs),)
        sql += " ORDER BY source_docid"
        with self._connection() as conn:
            with _dict_cursor(conn) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return [str(_row_value(r, "source_docid")) for r in rows]

    def _connection(self):
        connection = self._connect(self.dsn)
        if hasattr(connection, "__enter__"):
            return connection
        return closing(connection)


def source_document_upsert(raw: RawDocument) -> SqlStatement:
    sql = """
INSERT INTO source_documents (
    docid, system, url, title, content_type, parent_path, owner, author,
    can_edit, source_acl_hash, last_modified, raw_metadata
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
)
ON CONFLICT (docid) DO UPDATE SET
    url = EXCLUDED.url,
    title = EXCLUDED.title,
    content_type = EXCLUDED.content_type,
    parent_path = EXCLUDED.parent_path,
    owner = EXCLUDED.owner,
    author = EXCLUDED.author,
    can_edit = EXCLUDED.can_edit,
    source_acl_hash = EXCLUDED.source_acl_hash,
    raw_metadata = EXCLUDED.raw_metadata,
    updated_at = now()
""".strip()
    metadata = raw.metadata
    params = (
        raw.docid,
        metadata.get("system", "doc"),
        raw.url,
        raw.title,
        raw.content_type,
        metadata.get("parent_path"),
        metadata.get("owner"),
        metadata.get("author"),
        bool(metadata.get("can_edit", False)),
        metadata.get("source_acl_hash"),
        metadata.get("last_modified") or metadata.get("page_updated_at"),
        _json(metadata),
    )
    return SqlStatement(sql=sql, params=params)


def atom_upsert(record: AtomRecord) -> SqlStatement:
    draft = record.draft
    sql = """
INSERT INTO knowledge_atoms (
    atom_id, sub_kb_id, layer, card_type, atom_type, status, text,
    contextual_prefix, source_docid, source_anchor, section_path_json,
    owner, sensitivity, branch, version, atom_version
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, 1
)
ON CONFLICT (atom_id) DO UPDATE SET
    text = EXCLUDED.text,
    contextual_prefix = EXCLUDED.contextual_prefix,
    section_path_json = EXCLUDED.section_path_json,
    status = EXCLUDED.status,
    updated_at = now()
""".strip()
    params = (
        record.atom_id,
        draft.sub_kb_id,
        draft.layer.value,
        _infer_card_type(draft.sub_kb_id),
        _infer_atom_type(draft.sub_kb_id),
        draft.status.value,
        draft.text,
        draft.contextual_prefix,
        draft.source_docid,
        draft.source_anchor,
        _json(list(draft.section_path)),
        None,
        None,
        None,
        None,
    )
    return SqlStatement(sql=sql, params=params)


def governance_item_upsert(item: Any, *, first_seen_at: str = "", last_seen_at: str = "") -> SqlStatement:
    sql = """
INSERT INTO governance_items (
    item_id, item_type, severity, sub_kb_id, title, summary, suggested_owner,
    source_ref, evidence, status, first_seen_at, last_seen_at
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s
)
ON CONFLICT (item_id) DO UPDATE SET
    item_type = EXCLUDED.item_type,
    severity = EXCLUDED.severity,
    sub_kb_id = EXCLUDED.sub_kb_id,
    title = EXCLUDED.title,
    summary = EXCLUDED.summary,
    suggested_owner = EXCLUDED.suggested_owner,
    source_ref = EXCLUDED.source_ref,
    evidence = EXCLUDED.evidence,
    last_seen_at = EXCLUDED.last_seen_at,
    updated_at = now()
""".strip()
    params = (
        item.item_id,
        item.item_type,
        item.severity,
        item.sub_kb_id or None,
        item.title,
        item.summary,
        item.suggested_owner or None,
        item.source_ref or None,
        _json(item.evidence),
        item.status,
        first_seen_at or None,
        last_seen_at or None,
    )
    return SqlStatement(sql=sql, params=params)


def governance_ticket_plan_upsert(plan: Any) -> SqlStatement:
    sql = """
INSERT INTO governance_ticket_plans (
    ticket_id, item_id, item_type, severity, sub_kb_id, title, target, assignee,
    status, description, operations, planned_at
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s
)
ON CONFLICT (ticket_id) DO UPDATE SET
    status = EXCLUDED.status,
    assignee = EXCLUDED.assignee,
    description = EXCLUDED.description,
    operations = EXCLUDED.operations,
    updated_at = now()
""".strip()
    params = (
        plan.ticket_id,
        plan.item_id,
        plan.item_type,
        plan.severity,
        plan.sub_kb_id or None,
        plan.title,
        plan.target,
        plan.assignee or None,
        plan.status,
        plan.description,
        _json([operation.to_dict() for operation in plan.operations]),
        plan.created_at or None,
    )
    return SqlStatement(sql=sql, params=params)


def _infer_card_type(sub_kb_id: str) -> str:
    return {
        "release": "SOP",
        "testing": "TestGuide",
        "incident": "Incident",
        "owner": "Owner",
    }.get(sub_kb_id, "FAQ")


def _infer_atom_type(sub_kb_id: str) -> str:
    return {
        "release": "rule",
        "testing": "rule",
        "incident": "incident_lesson",
        "owner": "owner_record",
    }.get(sub_kb_id, "fact")


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _default_connect(dsn: str):
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        raise RuntimeError("psycopg is required for PostgresAtomStore") from exc

    return psycopg.connect(dsn)


def _dict_cursor(conn: Any):
    try:
        from psycopg.rows import dict_row
    except ModuleNotFoundError:
        return conn.cursor()

    try:
        return conn.cursor(row_factory=dict_row)
    except TypeError:
        return conn.cursor()


def _atom_select_sql(*, where: list[str], order_by: str = "") -> str:
    sql = """
SELECT
    a.atom_id::text AS atom_id,
    a.sub_kb_id,
    a.source_docid,
    COALESCE(d.title, '') AS source_title,
    COALESCE(a.source_anchor, '') AS source_anchor,
    COALESCE(a.section_path_json, '[]'::jsonb) AS section_path_json,
    a.text,
    COALESCE(a.contextual_prefix, '') AS contextual_prefix,
    a.layer,
    a.status,
    a.created_at,
    a.updated_at
FROM knowledge_atoms a
LEFT JOIN source_documents d ON d.docid = a.source_docid
""".strip()
    if where:
        sql += "\nWHERE " + " AND ".join(where)
    if order_by:
        sql += f"\nORDER BY {order_by}"
    return sql


def _record_from_postgres_row(row: Any) -> AtomRecord:
    draft = AtomDraft(
        sub_kb_id=str(_row_value(row, "sub_kb_id")),
        source_docid=str(_row_value(row, "source_docid")),
        source_title=str(_row_value(row, "source_title") or ""),
        source_anchor=str(_row_value(row, "source_anchor") or ""),
        section_path=_parse_section_path(_row_value(row, "section_path_json", default=[])),
        text=str(_row_value(row, "text")),
        contextual_prefix=str(_row_value(row, "contextual_prefix") or ""),
        layer=Layer(str(_row_value(row, "layer"))),
        status=AtomStatus(str(_row_value(row, "status"))),
    )
    return AtomRecord(
        atom_id=str(_row_value(row, "atom_id")),
        draft=draft,
        created_at=_parse_dt(_row_value(row, "created_at")),
        updated_at=_parse_dt(_row_value(row, "updated_at")),
    )


def _row_value(row: Any, key: str, *, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _parse_section_path(value: Any) -> tuple[str, ...]:
    import json

    if value in (None, ""):
        return ()
    if isinstance(value, str):
        parsed = json.loads(value)
    else:
        parsed = value
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item) for item in parsed)


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
