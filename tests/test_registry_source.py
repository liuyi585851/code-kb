import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.models import (
    KbRegistry,
    RawDocument,
    RetrievalDefaults,
    SourceDocConfig,
    SubKbConfig,
)
from codekb.registry_source import build_registry_source_bundle


class FakeConnector:
    def get_document(self, docid: str) -> RawDocument:
        bodies = {
            "root": "",
            "child-a": "## 复盘\n\n这是子文档 A。",
            "deep": "## 指南\n\n这是深抓文档。",
        }
        return RawDocument(
            docid=docid,
            title=f"title-{docid}",
            content_type="DOC",
            body=bodies[docid],
            url=f"https://wiki.example.com/p/{docid}",
            metadata={"owner": "owner", "parent_path": "root", "last_modified": "2026-06-11"},
        )

    def list_children(self, parentid: str):
        if parentid != "root":
            raise AssertionError(parentid)
        return [{"docid": "child-a", "title": "child A"}]


class RegistrySourceTests(unittest.TestCase):
    def test_build_bundle_expands_children_and_metadata_only_parent(self):
        registry = _registry()

        bundle = build_registry_source_bundle(registry, FakeConnector())

        self.assertEqual(tuple(doc.docid for doc in bundle.documents), ("root", "child-a", "deep"))
        self.assertEqual(bundle.sub_kbs, {"root": "incident", "child-a": "incident", "deep": "release"})
        self.assertIn("metadata/目录入口", bundle.documents[0].body)
        self.assertIn("这是子文档 A", bundle.documents[1].body)


def _registry() -> KbRegistry:
    defaults = RetrievalDefaults(
        dense_top_k=30,
        sparse_top_k=30,
        rrf_top_k=20,
        rerank_top_k=4,
        max_atoms=4,
        max_atom_tokens=800,
        contextual_prefix_tokens=200,
        citation_required=True,
        refuse_without_citation=True,
        layers_for_production_answer=("L2", "L3"),
    )
    return KbRegistry(
        version="test",
        updated_at="2026-06-11",
        status="draft",
        defaults=defaults,
        sub_kbs=(
            SubKbConfig(
                id="incident",
                name="复盘",
                owner_group="sre",
                status="pilot",
                description="",
                source_docs=(
                    SourceDocConfig(
                        system="wiki",
                        docid="root",
                        title="root",
                        mode="enumerate_children",
                        priority="P0",
                    ),
                ),
            ),
            SubKbConfig(
                id="release",
                name="发布",
                owner_group="release",
                status="pilot",
                description="",
                source_docs=(
                    SourceDocConfig(
                        system="wiki",
                        docid="deep",
                        title="deep",
                        mode="deep",
                        priority="P0",
                    ),
                ),
            ),
        ),
    )


if __name__ == "__main__":
    unittest.main()
