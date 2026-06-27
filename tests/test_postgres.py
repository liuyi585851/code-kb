import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.models import RawDocument
from codekb.pipeline import ingest_raw_document
from codekb.postgres import (
    PostgresAtomStore,
    atom_upsert,
    governance_item_upsert,
    governance_ticket_plan_upsert,
    source_document_upsert,
)
from codekb.store import InMemoryAtomStore


class PostgresMappingTests(unittest.TestCase):
    def test_source_document_upsert(self):
        raw = RawDocument(
            docid="1000000014",
            title="UDT",
            content_type="DOC",
            body="body",
            url="https://wiki.example.com/p/1000000014",
            metadata={"owner": "user2", "can_edit": True},
        )

        stmt = source_document_upsert(raw)

        self.assertIn("INSERT INTO source_documents", stmt.sql)
        self.assertEqual(stmt.params[0], "1000000014")
        self.assertEqual(stmt.params[6], "user2")
        self.assertTrue(stmt.params[8])

    def test_atom_upsert(self):
        store = InMemoryAtomStore()
        raw = RawDocument(
            docid="1000000014",
            title="UDT",
            content_type="DOC",
            body="## 参数\n\nDEVICE_SEQ 表示设备序号。" * 10,
        )
        record = ingest_raw_document(raw, sub_kb_id="testing", store=store)[0]

        stmt = atom_upsert(record)

        self.assertIn("INSERT INTO knowledge_atoms", stmt.sql)
        self.assertIn("section_path_json", stmt.sql)
        self.assertEqual(stmt.params[1], "testing")
        self.assertEqual(stmt.params[3], "TestGuide")
        self.assertEqual(stmt.params[4], "rule")
        self.assertTrue(any("参数" in str(param) for param in stmt.params))

    def test_governance_item_upsert(self):
        item = _Obj(
            item_id="item-1",
            item_type="stale_source",
            severity="P1",
            sub_kb_id="release",
            title="Old doc",
            summary="source document is stale",
            suggested_owner="release",
            source_ref="wiki://1",
            evidence={"age_days": 200},
            status="open",
        )

        stmt = governance_item_upsert(item, first_seen_at="2026-06-11T00:00:00Z", last_seen_at="2026-06-11T00:00:00Z")

        self.assertIn("INSERT INTO governance_items", stmt.sql)
        self.assertEqual(stmt.params[0], "item-1")
        self.assertEqual(stmt.params[1], "stale_source")
        self.assertIn("age_days", stmt.params[8])

    def test_governance_ticket_plan_upsert(self):
        operation = _Obj(to_dict=lambda: {"tool": "issue_tracker_create_ticket"})
        plan = _Obj(
            ticket_id="00000000-0000-0000-0000-000000000001",
            item_id="item-1",
            item_type="stale_source",
            severity="P1",
            sub_kb_id="release",
            title="[P1] Old doc",
            target="issue_tracker",
            assignee="release",
            status="planned",
            description="desc",
            operations=(operation,),
            created_at="2026-06-11T00:00:00Z",
        )

        stmt = governance_ticket_plan_upsert(plan)

        self.assertIn("INSERT INTO governance_ticket_plans", stmt.sql)
        self.assertEqual(stmt.params[0], "00000000-0000-0000-0000-000000000001")
        self.assertEqual(stmt.params[6], "issue_tracker")
        self.assertIn("issue_tracker_create_ticket", stmt.params[10])

    def test_postgres_atom_store_lists_atoms_with_filters(self):
        row = {
            "atom_id": "00000000-0000-0000-0000-000000000001",
            "sub_kb_id": "testing",
            "source_docid": "1000000014",
            "source_title": "UDT相关预设参数",
            "source_anchor": "p-1",
            "text": "DEVICE_SEQ 表示设备序号。",
            "contextual_prefix": "UDT > 参数",
            "layer": "L2",
            "status": "curated",
            "section_path_json": '["UDT相关预设参数", "参数"]',
            "created_at": "2026-06-17T01:02:03+00:00",
            "updated_at": "2026-06-17T01:02:04+00:00",
        }
        fake = _FakePostgres(rows=[row])
        store = PostgresAtomStore("postgresql://example", connect=fake.connect)

        records = store.list_atoms(sub_kbs={"testing"}, source_docids={"1000000014"})

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].atom_id, "00000000-0000-0000-0000-000000000001")
        self.assertEqual(records[0].draft.source_title, "UDT相关预设参数")
        self.assertEqual(records[0].draft.section_path, ("UDT相关预设参数", "参数"))
        sql, params = fake.cursor.executions[0]
        self.assertIn("FROM knowledge_atoms a", sql)
        self.assertIn("a.section_path_json", sql)
        self.assertIn("a.sub_kb_id = ANY(%s)", sql)
        self.assertIn("a.source_docid = ANY(%s)", sql)
        self.assertEqual(params, (["testing"], ["1000000014"]))

    def test_postgres_atom_store_gets_atom_by_id(self):
        row = {
            "atom_id": "00000000-0000-0000-0000-000000000002",
            "sub_kb_id": "release",
            "source_docid": "1000000022",
            "source_title": "Code-KB",
            "source_anchor": "root",
            "text": "发布流程需要先确认影响范围。",
            "contextual_prefix": "",
            "layer": "L2",
            "status": "curated",
            "created_at": "2026-06-17T01:02:03+00:00",
            "updated_at": "2026-06-17T01:02:04+00:00",
        }
        fake = _FakePostgres(rows=[row])
        store = PostgresAtomStore("postgresql://example", connect=fake.connect)

        record = store.get("00000000-0000-0000-0000-000000000002")

        self.assertEqual(record.draft.sub_kb_id, "release")
        self.assertEqual(record.draft.source_docid, "1000000022")
        sql, params = fake.cursor.executions[0]
        self.assertIn("a.atom_id = %s", sql)
        self.assertEqual(params, ("00000000-0000-0000-0000-000000000002",))

    def test_postgres_atom_store_get_raises_key_error_for_missing_atom(self):
        fake = _FakePostgres(rows=[])
        store = PostgresAtomStore("postgresql://example", connect=fake.connect)

        with self.assertRaises(KeyError):
            store.get("missing")


class _Obj:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakePostgres:
    def __init__(self, *, rows):
        self.cursor = _FakeCursor(rows)

    def connect(self, _dsn):
        return _FakeConnection(self.cursor)


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self, *args, **kwargs):
        return self._cursor


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=()):
        self.executions.append((sql, params))
        return self

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


if __name__ == "__main__":
    unittest.main()
