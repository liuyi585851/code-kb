[English](data-contracts.md) · **简体中文**

# P0 Data Contracts

更新时间:2026-06-11

本文件定义 P1 只读 MVP 所需的最小数据契约。字段以最小够用为原则进行设计,后续阶段再行扩展。

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

P3 人工沉淀入口先进入候选层,只有在通过审核后,内容才可发布到权威 KB 或 pending docs。

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

审核记录:

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

P3 出站发布必须先生成发布计划或 outbox,默认以 dry-run 方式运行,不直接写入 Wiki。

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

Wiki MCP 写操作的参数必须与工具 schema 对齐:

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

`index_docid`、`template_docid`、`target_parentid` 必须是正整数 Wiki docid。

执行报告:

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

执行 ledger:

```yaml
publish_id: uuid
candidate_id: uuid
mode: string
status: executed
operations: list
created_at: datetime
```

同一 `publish_id` 已记录为 `executed` 时,后续处理结果应返回 `skipped_already_executed`,不得再次调用写工具。

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

机器可读草案见 [kb-registry.draft.yaml](kb-registry.draft.yaml)。

最小字段:

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

## P1 建表建议

P1 最少需要以下表或集合:

| 名称 | 用途 |
|---|---|
| `knowledge_atoms` | Atom 主表 |
| `atom_versions` | 版本和 superseded 链 |
| `source_documents` | 文档 metadata、ACL、更新时间 |
| `retrieval_traces` | 检索链路审计 |
| `answer_logs` | 生成答案和引用 |
| `feedback_events` | 用户反馈和 AI 自评分 |
| `kb_registry` | 子 KB 配置快照 |
| `gap_candidates` | 缺口候选 |

## P1 约束

1. P1 生产问答默认只使用 `L2` 和 `L3`。
2. P1 可以将源文档切分为 atom,但属于 `L1` 的未审核 atom 不会进入生产回答。
3. 所有答案都必须保留 `trace_id`,以便回放与评估。
4. 所有引用都必须可追溯到具体的 source doc 与 anchor。
5. ACL 必须在检索前或结果返回前完成校验,不能仅在 UI 层过滤。
