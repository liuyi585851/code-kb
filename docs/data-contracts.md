**English** · [简体中文](data-contracts.zh-CN.md)

# P0 Data Contracts

Last updated: 2026-06-11

This document defines the minimal data contracts required for the P1 read-only MVP. The field set is kept minimal at first and extended in later phases.

## Knowledge Atom

```yaml
atom_id: uuid
sub_kb_id: string
layer: L0 | L1 | L2 | L3
card_type: SOP | FAQ | Owner | Checklist | Incident | Decision | ConfigRule | ReleaseParam | TestGuide
type: fact | rule | faq | snippet | incident_lesson | owner_record
status: draft | pending_review | curated | stale | archived | superseded
text: string
contextual_prefix: string
source:
  system: wiki | git | issue_tracker | im | crash | manual
  docid: string
  url: string
  title: string
  anchor: string
  parent_path: string
metadata:
  owner: string
  author: string
  last_modified: datetime
  source_acl_hash: string
  sensitivity: public_internal | team_internal | restricted
  branch: string
  version: string
score:
  confidence: float
  freshness: float
  usefulness: float
  ai_self_eval: float
  accuracy: float
  composite: float
version:
  atom_version: integer
  superseded_by: uuid | null
created_at: datetime
updated_at: datetime
```

## Retrieval Trace

```yaml
trace_id: uuid
query_id: uuid
user_id_hash: string
surface: code_review | claude_cli | im | mr_bot | api
query_text: string
route:
  selected_sub_kbs: [string]
  reason: string
rewrite:
  rewritten_queries: [string]
  entities: [string]
retrieval:
  dense_hits: [atom_id]
  sparse_hits: [atom_id]
  rrf_top20: [atom_id]
  reranker_top4:
    - atom_id: uuid
      score: float
answer:
  answer_id: uuid
  cited_atoms: [atom_id]
  cited_sources: [string]
  refused: boolean
  refusal_reason: string
latency_ms:
  total: integer
  retrieval: integer
  generation: integer
created_at: datetime
```

## Feedback Event

```yaml
feedback_id: uuid
answer_id: uuid
trace_id: uuid
user_id_hash: string
surface: code_review | claude_cli | im | mr_bot | api
signal: thumbs_up | thumbs_down | wrong | incomplete | supplement | ai_self_eval
comment: string
ai_self_eval:
  sufficiency: float
  faithfulness: float
  coverage: float
  per_atom_utility:
    - atom_id: uuid
      utility: float
created_at: datetime
```

## Knowledge Candidate

The P3 manual-curation entry point first lands in the candidate layer; only after passing review may the content be published to the authoritative KB or to pending docs.

```yaml
candidate_id: uuid
sub_kb_id: string
title: string
content: string
source_type: manual | im | issue_tracker | git | wiki
source_ref: string
submitted_by_hash: string
status: pending_review | approved | rejected | needs_revision
dedupe_key: sha256
conflict_candidate_id: uuid | ""
metadata: object
approved_doc_path: string
created_at: datetime
updated_at: datetime
```

Audit record:

```yaml
audit_id: uuid
candidate_id: uuid
action: approve | reject | request_revision
reviewer_hash: string
comment: string
previous_status: string
new_status: string
output_path: string
created_at: datetime
```

## Publish Plan

P3 outbound publishing must first generate a plan or an outbox; it is dry-run by default and does not write to the Wiki directly.

```yaml
publish_id: uuid
candidate_id: uuid
sub_kb_id: string
title: string
source_path: string
mode: manual | index_page | template_copy
status: planned | validated | blocked | executed
rendered_body: string
operations:
  - tool: manual_publish | saveDocumentParts | copyDocument | saveDocument
    params: object
    risk: string
created_at: datetime
```

Wiki MCP write-operation parameters must align with the tool schema:

```yaml
saveDocumentParts:
  id: integer
  title: string
  after: string
copyDocument:
  docid: integer
  new_parentid: integer
  is_single: 1
  language: zh_CN
saveDocument:
  docid: integer | "<copied_docid>"
  title: string
  body: string
```

`index_docid`, `template_docid`, and `target_parentid` must be positive-integer Wiki docids.

Execution report:

```yaml
outbox_path: string
execute: boolean
write_enabled: boolean
total: integer
processed: integer
invalid_lines: integer
executed_operations: integer
blocked_operations: integer
skipped_operations: integer
status: validated | manual_required | blocked | partial | executed | skipped
results:
  - publish_id: uuid
    candidate_id: uuid
    mode: string
    status: string
    operations:
      - tool: string
        status: validated | manual_required | blocked_write_disabled | blocked_missing_client | executed | skipped_already_executed
        detail: string
created_at: datetime
```

Execution ledger:

```yaml
publish_id: uuid
candidate_id: uuid
mode: string
status: executed
operations: list
created_at: datetime
```

When the same `publish_id` has already been recorded as `executed`, subsequent processing results should return `skipped_already_executed` and must not call the write tool again.

## KB Gap Candidate

```yaml
gap_id: uuid
source_event: ask_refusal | low_sufficiency | repeated_query | thumbs_down | manual
query_cluster_id: string
sub_kb_id: string
summary: string
example_queries: [string]
suggested_owner: string
priority: P0 | P1 | P2
status: open | triaged | assigned | resolved | wontfix
links:
  issue_tracker: string
  wiki: string
created_at: datetime
updated_at: datetime
```

## Sub KB Registry

For the machine-readable draft, see [kb-registry.draft.yaml](kb-registry.draft.yaml).

Minimal fields:

```yaml
sub_kbs:
  - id: release
    name: 发布与版本
    owner_group: release
    status: pilot
    default_layer_filter: [L2, L3]
    source_docs:
      - system: wiki
        docid: "1000000013"
        mode: deep
    retrieval:
      dense_top_k: 30
      sparse_top_k: 30
      rerank_top_k: 4
```

## P1 table suggestions

At minimum P1 needs the following tables or collections:

| Name | Purpose |
|---|---|
| `knowledge_atoms` | Atom main table |
| `atom_versions` | Versions and the superseded chain |
| `source_documents` | Document metadata, ACL, modified time |
| `retrieval_traces` | Retrieval-pipeline audit |
| `answer_logs` | Generated answers and citations |
| `feedback_events` | User feedback and AI self-eval scores |
| `kb_registry` | Sub-KB config snapshot |
| `gap_candidates` | Gap candidates |

## P1 constraints

1. P1 production Q&A uses only `L2` and `L3` by default.
2. P1 may chunk source documents into atoms, but an unreviewed atom belonging to `L1` does not enter production answers.
3. Every answer must retain a `trace_id` for replay and evaluation.
4. Every citation must trace back to a specific source doc and anchor.
5. ACL must be checked before retrieval or before results are returned; it cannot be filtered only at the UI layer.
