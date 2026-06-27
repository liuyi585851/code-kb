import sys
import unittest
import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.candidate import JsonCandidateStore
from codekb.cli import main
from codekb.diagnosis import build_diagnostic_result, submit_diagnostic_gap
from codekb.diagnosis_context import DiagnosticContext
from codekb.governance import GovernanceItem
from codekb.models import AnswerResult, CitationPack


ROOT = Path(__file__).resolve().parents[1]


class DiagnosisTests(unittest.TestCase):
    def test_refused_answer_builds_gap_candidate(self):
        answer = AnswerResult(
            query="流水线失败但 KB 没有说明怎么办？",
            answer="我现在没有足够可引用的知识来源来回答这个问题。",
            citations=(),
            refused=True,
            refusal_reason="NO_CITATION",
            answer_id="answer-1",
            trace_id="trace-1",
        )

        result = build_diagnostic_result(answer, sub_kbs={"release"}, owner_groups={"release": "release_owner"})

        self.assertTrue(result.refused)
        self.assertEqual(result.findings[0].finding_type, "no_citation")
        self.assertEqual(result.gap_candidate["source_event"], "ask_refusal")
        self.assertEqual(result.gap_candidate["suggested_owner"], "release_owner")
        self.assertIn("create a KB gap candidate", result.suggested_actions[0])

    def test_related_governance_items_are_linked_by_cited_docid(self):
        answer = AnswerResult(
            query="DEVICE_SEQ 是什么？",
            answer="根据当前可引用知识，先给出可追溯摘要。",
            citations=(
                CitationPack(
                    atom_id="atom-1",
                    docid="1000000014",
                    title="示例UDT自动化测试使用说明",
                    anchor="3-udt相关预设参数",
                    section_path=("3. UDT相关预设参数",),
                    quote="DEVICE_SEQ 是平台内置环境变量。",
                    score=12.5,
                ),
            ),
            refused=False,
            answer_id="answer-1",
            trace_id="trace-1",
            confidence=0.8,
        )
        item = GovernanceItem(
            item_id="gov-1",
            item_type="stale_source",
            severity="P1",
            sub_kb_id="testing",
            title="Old source",
            summary="source is stale",
            suggested_owner="qa_testing",
            source_ref="wiki://1000000014",
            evidence={"docid": "1000000014"},
        )

        result = build_diagnostic_result(answer, sub_kbs={"testing"}, governance_items=(item,))

        self.assertFalse(result.refused)
        self.assertEqual(len(result.related_governance_items), 1)
        self.assertTrue(any(finding.finding_type == "related_governance_risk" for finding in result.findings))
        self.assertEqual(result.gap_candidate, {})

    def test_submit_diagnostic_gap_writes_candidate(self):
        with TemporaryDirectory() as tmp:
            answer = AnswerResult(
                query="流水线失败但 KB 没有说明怎么办？",
                answer="我现在没有足够可引用的知识来源来回答这个问题。",
                citations=(),
                refused=True,
                refusal_reason="NO_CITATION",
                answer_id="answer-1",
                trace_id="trace-1",
            )
            diagnosis = build_diagnostic_result(
                answer,
                sub_kbs={"release"},
                owner_groups={"release": "release_owner"},
            )
            store = JsonCandidateStore(Path(tmp) / "candidates.json", pending_docs_dir=Path(tmp) / "pending")

            first = submit_diagnostic_gap(diagnosis, store, submitted_by_hash="u_1")
            duplicate = submit_diagnostic_gap(diagnosis, store, submitted_by_hash="u_1")

            self.assertFalse(first.duplicate)
            self.assertTrue(duplicate.duplicate)
            self.assertEqual(first.candidate.sub_kb_id, "release")
            self.assertEqual(first.candidate.source_type, "diagnose")
            self.assertEqual(first.candidate.source_ref, "trace-1")
            self.assertEqual(first.candidate.metadata["diagnosis_id"], diagnosis.diagnosis_id)
            self.assertIn("KB Gap", first.candidate.content)
            self.assertIn("流水线失败", first.candidate.content)
            self.assertNotIn("trace-1", first.candidate.content)
            self.assertEqual(len(first.candidate.metadata["gap_fingerprint"]), 64)

    def test_submit_diagnostic_gap_keeps_external_context(self):
        with TemporaryDirectory() as tmp:
            answer = AnswerResult(
                query="DEVICE_SEQ 构建失败 repo=ym/app",
                answer="我现在没有足够可引用的知识来源来回答这个问题。",
                citations=(),
                refused=True,
                refusal_reason="NO_CITATION",
                answer_id="answer-1",
                trace_id="trace-1",
            )
            diagnosis = build_diagnostic_result(
                answer,
                sub_kbs={"testing"},
                owner_groups={"testing": "qa_testing"},
                context=DiagnosticContext(
                    surface="code_review",
                    repo="ym/app",
                    branch="main",
                    error_code="DEVICE_SEQ",
                    error_text="DEVICE_SEQ 构建失败",
                    links={"mr": "https://example.invalid/mr/1"},
                    tags=("ci", "udt"),
                ),
            )
            store = JsonCandidateStore(Path(tmp) / "candidates.json", pending_docs_dir=Path(tmp) / "pending")

            submission = submit_diagnostic_gap(diagnosis, store)

            self.assertEqual(submission.candidate.metadata["context"]["repo"], "ym/app")
            self.assertEqual(submission.candidate.metadata["context"]["error_code"], "DEVICE_SEQ")
            self.assertIn("## Context", submission.candidate.content)
            self.assertIn("repo: ym/app", submission.candidate.content)
            self.assertIn("error_code: DEVICE_SEQ", submission.candidate.content)

    def test_submit_diagnostic_gap_dedupes_across_runs(self):
        with TemporaryDirectory() as tmp:
            first_answer = AnswerResult(
                query="流水线失败但 KB 没有说明怎么办？",
                answer="我现在没有足够可引用的知识来源来回答这个问题。",
                citations=(),
                refused=True,
                refusal_reason="NO_CITATION",
                answer_id="answer-1",
                trace_id="trace-1",
            )
            second_answer = AnswerResult(
                query="流水线失败但 KB 没有说明怎么办？",
                answer="我现在没有足够可引用的知识来源来回答这个问题。",
                citations=(),
                refused=True,
                refusal_reason="NO_CITATION",
                answer_id="answer-2",
                trace_id="trace-2",
            )
            first_diagnosis = build_diagnostic_result(
                first_answer,
                sub_kbs={"release"},
                owner_groups={"release": "release_owner"},
            )
            second_diagnosis = build_diagnostic_result(
                second_answer,
                sub_kbs={"release"},
                owner_groups={"release": "release_owner"},
            )
            store = JsonCandidateStore(Path(tmp) / "candidates.json", pending_docs_dir=Path(tmp) / "pending")

            first = submit_diagnostic_gap(first_diagnosis, store)
            duplicate = submit_diagnostic_gap(second_diagnosis, store)

            self.assertFalse(first.duplicate)
            self.assertTrue(duplicate.duplicate)
            self.assertEqual(duplicate.existing_candidate_id, first.candidate.candidate_id)
            self.assertEqual(len(store.list()), 1)

    def test_cli_diagnose_can_submit_gap_candidate(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store_path = root / "candidates.json"

            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "diagnose",
                        "DEVICE_SEQ 是什么？",
                        "--fixtures",
                        str(ROOT / "data" / "fixtures" / "sample_corpus.jsonl"),
                        "--registry",
                        str(ROOT / "docs" / "kb-registry.draft.yaml"),
                        "--aliases",
                        str(ROOT / "data" / "entity_aliases.yaml"),
                        "--sub-kb",
                        "release",
                        "--no-governance",
                        "--submit-gap",
                        "--candidate-store",
                        str(store_path),
                        "--pending-docs-dir",
                        str(root / "pending"),
                        "--json",
                    ]
                )

            self.assertEqual(code, 0)
            payload = json.loads(store_path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["candidates"]), 1)
            self.assertEqual(payload["candidates"][0]["source_type"], "diagnose")
            self.assertEqual(payload["candidates"][0]["sub_kb_id"], "release")

    def test_cli_diagnose_can_derive_query_from_context(self):
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            code = main(
                [
                    "diagnose",
                    "--fixtures",
                    str(ROOT / "data" / "fixtures" / "sample_corpus.jsonl"),
                    "--registry",
                    str(ROOT / "docs" / "kb-registry.draft.yaml"),
                    "--aliases",
                    str(ROOT / "data" / "entity_aliases.yaml"),
                    "--sub-kb",
                    "testing",
                    "--no-governance",
                    "--surface",
                    "code_review",
                    "--repo",
                    "ym/app",
                    "--branch",
                    "main",
                    "--error-code",
                    "DEVICE_SEQ",
                    "--error-text",
                    "DEVICE_SEQ 构建失败，需要排查 UDT 参数",
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertIn("DEVICE_SEQ 构建失败", payload["query"])
        self.assertEqual(payload["context"]["surface"], "code_review")
        self.assertEqual(payload["context"]["repo"], "ym/app")


if __name__ == "__main__":
    unittest.main()
