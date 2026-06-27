**English** · [简体中文](interface-spec.zh-CN.md)

# P1 Interface Spec

Last updated: 2026-06-12

This document defines the module boundaries and interface drafts for the P1 read-only RAG MVP. The goal is to let the Hub, Indexer, Retriever, Generator, and Evaluator be implemented in parallel.

## Module boundaries

| Module | Responsibility | Not responsible for |
|---|---|---|
| Registry | Load sub_kb config, source docs, retrieval params | Judging query intent |
| Source Connector | Read Wiki metadata/document/tree | Cleaning bodies, chunking |
| Source Sync Runner | Schedule the source connector, produce incremental state and a sync report | Online retrieval, answer generation |
| Normalizer | Markdown/HTML/TXDOC text cleaning | Generating embeddings |
| Chunker | Chunk by section/table/semantic boundaries | Judging whether knowledge is authoritative |
| Atom Store | Store atoms, sources, versions | Generating answers |
| Indexer | Write to Postgres/Qdrant/ES | Online query routing |
| Retriever | Dense/Sparse/RRF/Rerank | Generating natural-language answers |
| Generator | cite-or-die answer generation | Modifying the KB |
| Trace Logger | Record query, hits, answer, latency | Evaluating correctness |
| Evaluator | Run the golden set, output hit@4/citation/refusal | Live traffic splitting |

## Core interfaces

### Registry

```python
class Registry:
    def load(self, path: str) -> "KbRegistry": ...
    def get_sub_kb(self, sub_kb_id: str) -> "SubKbConfig": ...
    def list_pilot_sub_kbs(self) -> list["SubKbConfig"]: ...
```

### Source Connector

```python
class WikiConnector:
    def get_metadata(self, docid: str) -> "SourceDocument": ...
    def get_document(self, docid: str) -> "RawDocument": ...
    def list_children(self, parentid: str) -> list["SourceDocument"]: ...
```

`RawDocument` minimal fields:

```python
@dataclass
class RawDocument:
    docid: str
    title: str
    content_type: str
    body: str
    url: str
    metadata: dict
```

### Source Sync Runner

```python
class SourceSyncRunner:
    def sync(self, bundle: "SourceBundle", state_path: str | None) -> "SyncReport": ...
```

`SyncReport` current JSON fields:

```text
source_path, started_at, finished_at, total, indexed, skipped, failed, atom_count, missing_docids, results
```

Per-document result:

```text
docid, title, sub_kb_id, status, reason, atom_count, body_sha256, metadata_sha256, source_modified_at
```

The P1 smoke CLI is already implemented:

```bash
PYTHONPATH=src python3 -m codekb sync --fixtures data/fixtures/sample_corpus.jsonl
```

Registry-driven real Wiki sync is implemented:

```python
bundle = build_registry_source_bundle(registry, WikiSourceConnector(client))
```

`enumerate_children` expands child documents; empty bodies or directory macros become metadata-only entries.

### Normalizer

```python
class DocumentNormalizer:
    def normalize(self, raw: RawDocument) -> "NormalizedDocument": ...
```

`NormalizedDocument` minimal fields:

```python
@dataclass
class NormalizedDocument:
    docid: str
    title: str
    sections: list["NormalizedSection"]
    warnings: list[str]
```

### Chunker

```python
class Chunker:
    def chunk(self, doc: NormalizedDocument, sub_kb_id: str) -> list["AtomDraft"]: ...
```

Chunking constraints:

- Target 200–800 Chinese characters.
- Preserve `docid/title/section_path/anchor`.
- Expand tables row by row, with header context inline in each row.
- Images keep only an attachment placeholder.

### Atom Store

```python
class AtomStore:
    def upsert_source_document(self, doc: SourceDocument) -> None: ...
    def upsert_atom(self, atom: KnowledgeAtom) -> None: ...
    def get_atom(self, atom_id: str) -> KnowledgeAtom: ...
    def list_atoms_by_source(self, docid: str) -> list[KnowledgeAtom]: ...
```

### Retriever

```python
class Retriever:
    def retrieve(self, request: RetrievalRequest) -> "RetrievalResult": ...
```

`RetrievalRequest`:

```python
@dataclass
class RetrievalRequest:
    query: str
    sub_kbs: list[str]
    user_acl: dict
    top_k_dense: int = 30
    top_k_sparse: int = 30
    top_k_rerank: int = 4
```

`RetrievalResult`:

```python
@dataclass
class RetrievalResult:
    query: str
    top_atoms: list["RetrievedAtom"]
    sparse_hits: list[str]
    dense_hits: list[str]
    rrf_top20: list[str]
    rerank_hits: list[str]
    retriever: str
```

P1 defaults to `bm25-lite`; the optional `hybrid-lite` uses a local deterministic dense + sparse + RRF + rerank trace, with no dependency on a GPU or an external model.

### Generator

```python
class Generator:
    def answer(self, query: str, citations: list["CitationPack"]) -> "AnswerResult": ...
```

Rules:

- Must refuse when `citations` is empty.
- May only cite facts present in the `CitationPack`.
- Answers default to at most 400 Chinese characters.

### Evaluator

```python
class GoldenEvaluator:
    def load_questions(self, path: str) -> list["GoldenQuestion"]: ...
    def run(self, questions: list["GoldenQuestion"]) -> "EvalReport": ...
```

## HTTP API

### `POST /ask`

Input:

```json
{
  "query": "DEVICE_SEQ 是什么？",
  "surface": "api",
  "user_id_hash": "u_xxx",
  "sub_kbs": ["testing"]
}
```

Output:

```json
{
  "answer_id": "uuid",
  "trace_id": "uuid",
  "answer": "DEVICE_SEQ 是 UDT 平台内置环境变量...",
  "citations": [
    {
      "docid": "1000000014",
      "title": "示例UDT自动化测试使用说明",
      "anchor": "3.1",
      "modified_at": "2024-11-29 21:17:08"
    }
  ],
  "refused": false,
  "confidence": 0.82
}
```

### `POST /diagnose`

The P5 diagnosis entry point reuses `/ask`'s retrieval and citation results, additionally returning diagnostic findings, suggested actions, a gap-candidate draft, and related governance items.

Input:

```json
{
  "query": "DEVICE_SEQ 是什么？",
  "context": {
    "surface": "code_review",
    "repo": "ym/app",
    "branch": "main",
    "commit": "abc123",
    "mr_id": "123",
    "build_id": "build-456",
    "job_name": "udt-ci",
    "error_code": "DEVICE_SEQ",
    "error_text": "DEVICE_SEQ 构建失败，需要排查 UDT 参数",
    "log_excerpt": "missing DEVICE_SEQ",
    "tags": ["ci", "udt"],
    "links": {"mr": "https://example.invalid/mr/123"}
  },
  "sub_kbs": ["testing"],
  "top_k": 4,
  "min_confidence": 0.35,
  "include_governance": true
}
```

`query` may be empty; when empty, the diagnostic query is derived from `context.error_code`, `context.error_text`, `context.log_excerpt`, and fields such as repo/build. If both `query` and the error context are empty, the endpoint returns 400.

Security handling:

- `query`, `context.error_text`, `context.log_excerpt`, and `context.links` are redacted before entering retrieval, before being returned, and before being written into gap-candidate metadata.
- Current redaction covers `password/token/access_token/refresh_token/client_secret/corpsecret/user_ticket/signature/api_key/access_key/authorization/cookie` and the like when they appear in assignments, JSON fields, CLI arguments, headers, and URL query parameters.
- Ordinary knowledge field names such as `ACCOUNT_TOKEN` are kept; only a `token=...` or `Authorization: ...` carrying an actual value is replaced with `[REDACTED]`.

Output:

```json
{
  "diagnosis_id": "uuid",
  "answer_id": "uuid",
  "trace_id": "uuid",
  "query": "DEVICE_SEQ 是什么？",
  "context": {
    "surface": "code_review",
    "repo": "ym/app",
    "branch": "main",
    "error_code": "DEVICE_SEQ",
    "error_text": "DEVICE_SEQ 构建失败，需要排查 UDT 参数",
    "tags": ["ci", "udt"],
    "links": {"mr": "https://example.invalid/mr/123"}
  },
  "sub_kbs": ["testing"],
  "answer": "根据当前可引用知识...",
  "refused": false,
  "confidence": 0.82,
  "citations": [],
  "findings": [],
  "related_governance_items": [],
  "suggested_actions": ["continue troubleshooting with the cited KB sections"],
  "gap_candidate": {}
}
```

The current P1 API already implements `answer_id`, `trace_id`, `refused`, `refusal_reason`, `citations`, and `confidence`.

### `POST /diagnose/webhook/{source}`

The P5 external callback entry point. This endpoint does not depend directly on a code review/CI/MR/issue tracker/Crash SDK; it only maps the platform payload onto the unified `/diagnose` input and then runs the diagnosis. It supports `source=code_review|ci|mr|issue_tracker|crash|generic`.

Example input:

```json
{
  "repository": {"path": "ym/app", "url": "https://example.invalid/ym/app"},
  "merge_request": {"iid": 123, "source_branch": "feature/udt", "url": "https://example.invalid/mr/123"},
  "pipeline": {"id": "build-456", "url": "https://example.invalid/build/456"},
  "job": {"name": "udt-ci"},
  "error": {"code": "DEVICE_SEQ", "message": "DEVICE_SEQ 构建失败"},
  "log_tail": "missing DEVICE_SEQ",
  "event": "build_failed",
  "sub_kbs": ["testing"],
  "include_governance": false,
  "auth_token": "<current-user-token>",
  "confirmation_policy": "needs_review",
  "confirmation_reason": "human_review_required"
}
```

Output:

```json
{
  "status": "diagnosed",
  "source": "code_review",
  "diagnosis": {"diagnosis_id": "uuid"},
  "normalized": {
    "query": "error_code=DEVICE_SEQ DEVICE_SEQ 构建失败 ...",
    "sub_kbs": ["testing"],
    "context": {"surface": "code_review", "repo": "ym/app"}
  }
}
```

Writing a gap candidate uses `POST /diagnose/webhook/{source}/gap-candidate`, whose response fields match `/diagnose/gap-candidate`. If the `CODEKB_DIAGNOSE_WEBHOOK_TOKEN` environment variable is configured, the request must carry `X-CodeKB-Token`. If the webhook diagnosis requires human review or problem-resolved confirmation, the payload must also carry the current user's bound `auth_token` and `confirmation_policy=needs_review|always`; the confirmation is pushed only to the current user bound to that token, and is not routed by repository, owner, or contact-person fields. The webhook audit event does not store the `auth_token` or the confirmation control fields.
The field mapping is read by default from `docs/diagnose-webhook-mapping.draft.yaml`, and can also point to another YAML via `CODEKB_DIAGNOSE_WEBHOOK_MAPPING`; source-specific paths take precedence over default and built-in compatibility paths.

### `GET /diagnose/webhook/{source}/mapping`

Read-only view of the webhook field mapping in effect for the current source, used to verify before integration whether a real platform payload will be extracted correctly.

Output:

```json
{
  "path": "docs/diagnose-webhook-mapping.draft.yaml",
  "exists": true,
  "sources": {
    "code_review": {
      "query_paths": ["query", "question", "diagnostic_query"],
      "context_paths": {"repo": ["code_review.repository.path", "repo", "repository.path"]},
      "link_paths": {"build": ["code_review.pipeline.url", "build_url", "pipeline.url"]},
      "tag_paths": ["code_review.event", "tags", "event"]
    }
  }
}
```

### `GET /diagnose/webhook/sample-suite`

Read-only run of the current webhook sample suite. The endpoint does not run a diagnosis, does not write a webhook audit, and does not return the raw sample payloads; it returns only the validation results, the redacted query/context, the field-extraction status, and an error summary.

The sample manifest is read by default from `CODEKB_DIAGNOSE_WEBHOOK_SAMPLES=docs/diagnose-webhook-samples.draft.yaml`, and the field mapping from `CODEKB_DIAGNOSE_WEBHOOK_MAPPING=docs/diagnose-webhook-mapping.draft.yaml`.

Response:

```json
{
  "status": "passed",
  "total": 6,
  "passed": 6,
  "failed": 0,
  "samples": [
    {"name": "code_review_build_failed", "source": "code_review", "status": "passed", "query_ready": true}
  ]
}
```

### `POST /diagnose/webhook/{source}/normalize`

Webhook payload pre-check entry point. This endpoint only performs field mapping, redaction, and diagnostic-query derivation; it does not run retrieval and does not write an audit log.

Output:

```json
{
  "status": "normalized",
  "source": "code_review",
  "query": "error_code=DEVICE_SEQ DEVICE_SEQ 构建失败 repo=ym/app",
  "sub_kbs": ["testing"],
  "context": {"surface": "code_review", "repo": "ym/app", "error_code": "DEVICE_SEQ"},
  "diagnostic_payload": {
    "query": "error_code=DEVICE_SEQ DEVICE_SEQ 构建失败 repo=ym/app",
    "context": {"surface": "code_review", "repo": "ym/app", "error_code": "DEVICE_SEQ"},
    "sub_kbs": ["testing"]
  },
  "options": {"top_k": "", "min_confidence": "", "include_governance": "", "allow_duplicate": ""}
}
```

### `POST /diagnose/webhook/{source}/validate`

Webhook payload validation entry point. This endpoint only performs field mapping, redaction, diagnostic-query derivation, and a field-completeness check; it does not run retrieval and does not write an audit log. Insufficient fields do not directly return 400; instead it returns `valid=false` together with `errors/warnings`.

Output:

```json
{
  "status": "validated",
  "source": "code_review",
  "valid": true,
  "query_ready": true,
  "errors": [],
  "warnings": [],
  "mapping": {"path": "docs/diagnose-webhook-mapping.draft.yaml", "exists": true},
  "extracted_fields": {
    "explicit_query": false,
    "query": true,
    "sub_kbs": true,
    "context": {"repo": true, "error_code": true, "error_text": true},
    "links": {"build": true}
  },
  "query": "error_code=DEVICE_SEQ DEVICE_SEQ 构建失败 repo=ym/app",
  "context": {"surface": "code_review", "repo": "ym/app", "error_code": "DEVICE_SEQ"},
  "sub_kbs": ["testing"],
  "diagnostic_payload": {"query": "error_code=DEVICE_SEQ DEVICE_SEQ 构建失败 repo=ym/app"}
}
```

### `GET /diagnose/webhook/events`

Webhook diagnosis-event audit summary, read-only.

Query parameters:

| Parameter | Default | Description |
|---|---:|---|
| `source` | empty | Filter by `code_review`, `ci`, `mr`, `issue_tracker`, `crash`, `generic` |
| `status` | empty | Filter by `diagnosed`, `accepted`, `duplicate`, `bad_request`, `unauthorized`, `error` |
| `action` | empty | Filter by `diagnose` or `gap_candidate` |
| `limit` | 20 | Number of recent events, range 0–200 |

Output:

```json
{
  "path": "/data/codekb/logs/diagnose-webhook.jsonl",
  "total": 12,
  "unfiltered_total": 20,
  "invalid_lines": 0,
  "filters": {"source": "code_review", "status": "bad_request", "action": "diagnose"},
  "by_source": {"code_review": 10, "ci": 2},
  "by_status": {"diagnosed": 11, "accepted": 1},
  "by_action": {"diagnose": 11, "gap_candidate": 1},
  "latest_created_at": "2026-06-11T12:00:00Z",
  "events": [
    {
      "event_id": "uuid",
      "source": "code_review",
      "action": "diagnose",
      "status": "diagnosed",
      "diagnosis_id": "uuid",
      "trace_id": "uuid",
      "context": {"surface": "code_review", "repo": "ym/app"},
      "citation_docids": ["1000000014"],
      "finding_types": []
    }
  ]
}
```

Successful events store only the normalized diagnostic summary; failed events store source/action/status, the error type, and a redacted error summary. The raw webhook payload is not stored; `query/context/links` have already gone through diagnostic-context redaction.

### P5 MCP and R&D entry artifacts

P5 provides a stdio MCP server and integration artifacts, so that IDE, code review, MR cards, and the IM entry point can reuse the same KB Hub. The integration artifacts can be obtained as a read-only JSON bundle under the current API base via `GET /diagnose/integrations`, or exported to files via the CLI.
The webhook sample suite can, via `diagnose-webhook-sample-suite`, batch-validate field extraction, query readiness, expected context, and non-leakage of forbidden secrets across code review/CI/MR/issue tracker/Crash/generic payloads; the default sample manifest is `docs/diagnose-webhook-samples.draft.yaml`, replaceable with real platform samples via `CODEKB_DIAGNOSE_WEBHOOK_SAMPLES`.

Once real platform payloads are available, first use `diagnose-webhook-sample-import` or `POST /diagnose/webhook/{source}/sample-import` to recursively redact the payload and import it into the real sample suite, then use `diagnose-webhook-sample-activate --apply --confirm-real-samples` to validate and write `CODEKB_DIAGNOSE_WEBHOOK_SAMPLES` into the server-only env file. HTTP import requires `X-CodeKB-Admin-Token` and writes by default to `CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES=/data/codekb/state/diagnose-webhook-samples.real.yaml`. The importer checks that the original sensitive values are not written into the sample YAML; the raw real payloads should not be committed to the repository.

P5 readiness can be viewed via `GET /diagnose/readiness` or `diagnose-readiness --json`. The returned items include the core diagnostic files, webhook mapping/sample suite, MCP tools/`auth_token` requirements, the current-user token store, the confirmation outbox, the IM OAuth/send config, the webhook shared token, and the real-platform sample source. The status is one of `ready`, `ready_with_warnings`, or `blocked`; when external config is not yet integrated it shows `deferred/warn` and `required_actions`, but never returns any secret in cleartext. The remaining external inputs can be viewed via `GET /diagnose/external-inputs`, `GET /diagnose/external-inputs.md`, `GET /diagnose/external-inputs/page`, or `diagnose-external-inputs --json`, returning the tasks, owner, required variable names, safe commands, and acceptance commands, without secret cleartext; this plan merges the evidence checks from `/diagnose/external-state`, surfaces state-only gaps such as `im_template` as tasks, and returns an `operator_handoff` containing the recommended execution order, grouping by owner, next actions, completion criteria, and the final gate command; the final gate command explicitly includes `diagnose-p5-external-state` and `/diagnose/external-state`, ensuring that external-state readiness has independent evidence. The Markdown/page entry points also show the current-user authorization policy: complete IM OAuth / web token binding before MCP calls, MCP uses `auth_token`, the confirmation target is the current authorized user, and no contact-person identification is used. When OAuth is temporarily unavailable, an admin can, after confirming the user's IM identity, use `GET /auth/im/token-bindings/page` to issue a current-user token under control; this fallback still requires `CODEKB_AUTH_ADMIN_TOKEN`. `GET /diagnose/final-verification` and `/diagnose/final-verification/page` reuse readiness, external-state, and the external-input plan to present the post-configuration acceptance stages by runtime state, IM OAuth, current-user authorization, IM delivery, real samples, and the final gate, and surface the same operator handoff, keeping the next action / completion criteria consistent between the final-acceptance page and the external-inputs page. `GET /diagnose/external-state` or `diagnose-p5-external-state --json` can further output boolean evidence for the IM template/env, token store, real samples, and send switch. `diagnose-p5-handoff-bundle --output-dir <dir>` writes the current plan into `external-inputs.json/md`, generates an `im-config.todo.env` safe template, and exports the `integrations/` artifacts and a README, making it easy to hand directly to the IM/platform integrator. Final acceptance uses `GET /diagnose/acceptance` or `diagnose-acceptance --json`; it returns `accepted=true` only when readiness is `ready`, and the CLI returns non-zero when not accepted. `diagnose-p5-final-verify --json --output <path>` further runs and aggregates the unit tests, quality gate, readiness, acceptance, external-inputs, external-state, external-input-plan-alignment, the sample suite, MCP auth-error fallback, MCP static-token default-deny, MCP token-store rejecting a shared static token, the handoff bundle, IM/current-user smoke, and HTTP readiness/external-state/webhook-token-guard/webhook-sample-import-smoke/setup-status/setup-page/token-binding-page/token-binding-fallback-smoke/im-configure-page/im-configure-guard/im-configure-plan/external-inputs-page/external-inputs-markdown/final-verification-json/final-verification-page/current-user-smoke/confirmation-request/acceptance and the confirmation-worker dry-run; only the current-user smoke/confirmation commands inherit `CODEKB_USER_AUTH_TOKEN`, while the other commands strip that environment variable. The report distinguishes `pending_required` from `failed_required`, returns a top-level `external_input_handoff` machine-readable summary containing status, pending_count, ordered_task_ids, next_action, completion_criteria, and secret markers, and writes the final evidence into a `0600` JSON file. Non-JSON output prints `HANDOFF`, `HANDOFF_SAFE`, and `HANDOFF_VERIFY` lines, using the same handoff data to expose the next-step safe command and verification command, so that configuration can be advanced from a terminal session without parsing JSON.

P5 security configuration can be generated via `diagnose-security-bootstrap` as a server-only env snippet, including by default `CODEKB_DIAGNOSE_WEBHOOK_TOKEN`, `CODEKB_IM_OAUTH_STATE_SECRET`, and `CODEKB_AUTH_ADMIN_TOKEN`. If written to a file, the permission is `0600`; generated values should go only into the server environment and should not be committed to the repository. The static `CODEKB_MCP_TOKEN` is generated only with an explicit `--include-static-mcp-token`, and is used only for local diagnostic smoke tests when no token store is configured.

MCP server:

```bash
PYTHONPATH=src python3 -m codekb diagnose-mcp-server \
  --token-store /data/codekb/state/user-tokens.json \
  --confirmation-outbox /data/codekb/outbox/user-confirmation.jsonl
```

Current tools:

| tool | Purpose |
|---|---|
| `codekb_diagnose` | Call the local KB diagnosis, returning citations, findings, gap candidate |
| `codekb_diagnose_webhook_validate` | Validate the extraction completeness of an external webhook payload |
| `codekb_diagnose_webhook_normalize` | Preview the webhook payload mapping result |
| `codekb_request_user_confirmation` | Request a problem-resolved or human-review confirmation from the current authorized user |

MCP calls require the current user to complete IM authorization or web-side token binding first. The production MCP server validates the bound token via `--token-store`; tool calls must pass the current user's token via `auth_token`. `CODEKB_MCP_TOKEN`/`--mcp-token` is not an MCP auth source by default; only with an explicit `--allow-static-mcp-token` and no configured token store is a local diagnostic smoke allowed, and this switch must not be enabled in production. After `--token-store` is configured, a shared static token is ignored and cannot initiate a confirmation. When no token store is configured, MCP tool calls are refused by default; the confirmation tool always requires a valid token store. When confirmation is needed, it is written to `CODEKB_USER_CONFIRMATION_OUTBOX`, default `/data/codekb/outbox/user-confirmation.jsonl`, storing only the token hash. `codekb_diagnose`, HTTP `POST /diagnose`, and webhook `POST /diagnose/webhook/{source}` all support `confirmation_policy=never|needs_review|always`, default `never`; `needs_review` writes a current-user confirmation outbox on refusal, low confidence, governance risk, or gap candidate, while `always` writes a confirmation on every diagnosis. The webhook entry point requires both the platform shared token `X-CodeKB-Token` and the current user's `auth_token`, which serve different purposes. A web/AI client may call `POST /auth/im/confirmations/request` to explicitly request a current-user confirmation when the problem is resolved, the interaction is complete, or for other post-hoc human review. P5 confirmation is not routed via complex contact-person identification; owner information is used only for governance reports, candidate assignment, and fallback context.

An MCP unauthorized JSON-RPC error keeps compatible text in `error.message`, and in `error.data` returns `authorization_required=true`, `reason`, `setup_url`, `im_oauth_login_url`, `auth_token_argument=auth_token`, `token_store_configured`, `static_token_configured`, `static_token_allowed`, and `remediation`. The client should present `setup_url` to let the current user complete authorization, and should not prompt the use of a shared static token.

Confirmation sending is handled by the `user-confirmation-outbox` worker, which consumes the outbox and looks up the current user's token binding by token hash; `deploy/codekb-confirmation-worker` can run in a long-lived loop. Real IM application-message sending requires the token binding metadata to carry `im_userid` and explicit settings of `CODEKB_ENABLE_IM_SEND=1`, `CODEKB_IM_CORP_ID`, `CODEKB_IM_AGENT_ID`, `CODEKB_IM_APP_SECRET`; the default dry-run only generates `CODEKB_USER_CONFIRMATION_REPORT`. A successful real send is written to `CODEKB_USER_CONFIRMATION_DELIVERY_LOG`, and subsequent loops skip already-delivered confirmations to avoid duplicate pushes.

Web-side token binding:

| Endpoint | Purpose |
|---|---|
| `GET /auth/im/oauth/login?next=...` | Initiate IM OAuth authorization; `next` only allows same-domain relative paths |
| `GET /auth/im/oauth/callback?code=...&state=...` | Validate state, exchange for the current IM user identity, and issue a current-user token |
| `GET /auth/im/mcp/setup` | Current-user MCP authorization status page; can initiate OAuth, check the browser token, run a self-test, and copy the MCP `auth_token` argument |
| `GET /auth/im/mcp/setup/status` | Current-user MCP authorization status JSON, returning OAuth missing items, callback URL, external-inputs JSON/Markdown/page URL, final-verification JSON/page URL, token-binding fallback page URL, IM configure page/API URL, confirmation page URL, current-user demo URL, current-user smoke URL, token-store count, and `mcp_auth_strategy`, without returning secrets |
| `GET /diagnose/external-inputs.md` | P5 remaining-external-inputs Markdown checklist, `text/markdown` + `Cache-Control: no-store`, reusing the `/diagnose/external-inputs` plan and showing the external-state status summary |
| `GET /diagnose/external-inputs/page` | P5 remaining-external-inputs web checklist, reusing the `/diagnose/external-inputs` plan, showing the current-user authorization policy, external-state summary, and final acceptance command |
| `GET /diagnose/final-verification` | P5 post-configuration acceptance JSON guide, reusing readiness, external-state, and the external-input plan, showing stages, URLs, commands, current status, and `operator_handoff`, without returning secrets |
| `GET /diagnose/final-verification/page` | P5 post-configuration acceptance web guide, `Cache-Control: no-store`, showing runtime state, IM OAuth, current-user authorization, delivery, real samples, the operator handoff, and the final gate |
| `POST /auth/im/current-user/status` | Current user self-checks whether the token is valid, returning only the public binding, not the raw token |
| `POST /auth/im/current-user/smoke` | Current-user self-service integration entry point; requires a valid token, creates a current-user confirmation and verifies the dry-run routing, without returning the raw token or the IM userid |
| `GET /demo/current-user` | Current-user end-to-end web demo, chaining token validation, diagnosis, an explicit confirmation request, the web-push inbox, and confirmation write-back into one deployable use case |
| `GET /demo/webhook` | Platform-webhook integration self-test web page, supporting normalize, validate, diagnose, gap candidate, and current-user confirmation for code review/CI/MR/issue tracker/Crash/generic payloads |
| `GET /auth/im/configure/page` | Admin web configuration tool; the form submits to `/auth/im/configure`, and the page does not store or echo secrets |
| `POST /auth/im/configure` | Admin-controlled write of the IM OAuth/send config into the API's current `CODEKB_ENV_FILE`; the response returns only key status and a hash prefix, not secrets |
| `GET /auth/im/token-bindings/page` | Admin-controlled token-binding fallback web tool; issues a current-user token when OAuth is temporarily unavailable and the user's IM identity has been confirmed, `Cache-Control: no-store` |
| `POST /auth/im/token-bindings` | Admin-controlled fallback issuance of a current-user token; the raw token is returned only once; when `user_id_hash` is not provided but `metadata.im_userid` is, the backend derives the hash |
| `GET /auth/im/token-bindings/summary` | Admin views the token-binding summary, without returning the raw token |
| `POST /auth/im/token-bindings/{token_id}/revoke` | Admin revokes a current-user token |
| `GET /auth/im/confirmations/page?confirmation_id=...` | Confirmation page opened from a IM textcard or the web |
| `POST /auth/im/confirmations/request` | Current user explicitly creates a confirmation request, for interaction-complete, problem-resolved, or other human-review moments; requires the current user's `auth_token` |
| `POST /auth/im/confirmations/pending` | Current user views pending confirmations; requires the current user's `auth_token` |
| `POST /auth/im/confirmations/{confirmation_id}/detail` | Current user views the detail of a single confirmation; requires the current user's `auth_token` |
| `POST /auth/im/confirmations/{confirmation_id}/response` | Current user writes back a confirmation result; requires the current user's `auth_token` |
| `GET /auth/im/confirmations/responses/summary` | View the confirmation-response summary, without returning the raw token |
| `GET /index/status` | View the current state of the SQLite local index and source/atom statistics |
| `POST /index/rebuild` | Admin-controlled rebuild of the SQLite local index, including pending docs by default, using atomic replacement; requires `X-CodeKB-Admin-Token` |
| `GET /audit/page` | Curator review console, chaining the candidate queue, detail, revision, review, and index status |
| `GET /ingest/candidates/{candidate_id}` | View candidate detail and audit history |
| `POST /ingest/candidates/{candidate_id}/revision` | Submit a revision for a `needs_revision` candidate; the candidate returns to `pending_review` |
| `GET /publish/plan` | Generate a publish-plan dry-run based on pending docs, without writing to the Wiki |
| `GET /publish/readiness` | Admin views the publish-target config, pending docs, outbox/report paths, and the real-write gate status |
| `POST /publish/configure` | Admin-controlled write of the publish-target config into the API's current `CODEKB_ENV_FILE`; after apply, sync the current-process env |
| `POST /publish/outbox/plan` | Admin-controlled write of the current publish plan into the Wiki publish outbox; requires `X-CodeKB-Admin-Token` |
| `POST /publish/outbox/process` | Admin-controlled validate/process of the publish outbox and write a report; real writes are blocked by default and require the write switch and a real client |

Token bindings are written by default to `CODEKB_USER_TOKEN_STORE=/data/codekb/state/user-tokens.json`. HTTP token management, the confirmation-response summary, and real-webhook-sample import must configure `CODEKB_AUTH_ADMIN_TOKEN` and carry `X-CodeKB-Admin-Token`; when not configured, the endpoints refuse the request. The main path for ordinary users is OAuth login/callback, which performs state signature validation per `CODEKB_IM_OAUTH_STATE_SECRET` and automatically issues a token. `diagnose-im-oauth-smoke` can validate the OAuth env, state, authorize URL, and token-store status without exposing secrets. The `im_userid` used for delivery is stored in the binding metadata, and the public summary, current-user status, and current-user smoke return only the hash / public confirmation info.

OAuth runtime config: `CODEKB_IM_CORP_ID`, `CODEKB_IM_AGENT_ID`, `CODEKB_IM_APP_SECRET`, `CODEKB_IM_OAUTH_REDIRECT_URI`, `CODEKB_IM_OAUTH_STATE_SECRET`. The callback success page shows the raw token only once and writes it to the same-domain browser `localStorage.codekb_user_token`; the store keeps only the token hash.

`diagnose-im-configure` and `POST /auth/im/configure` can safely write the above IM config into a server-only env file, with output containing only the key, status, and a hash prefix; the CLI `--template-output` can generate a `0600` to-fill template without copying existing secrets, and after filling it in you write the official env via `--from-template ... --apply`. The HTTP configure entry point must configure and validate `CODEKB_AUTH_ADMIN_TOKEN`, only writes the API's current `CODEKB_ENV_FILE`, and does not accept a request body overriding the env path; enabling real IM sending requires an explicit `--enable-send --confirm-real-send` or HTTP `enable_send=true` + `confirm_real_send=true`.

Confirmation responses are written by default to `CODEKB_USER_CONFIRMATION_RESPONSES=/data/codekb/state/user-confirmation-responses.jsonl`. pending/detail return only the confirmations for the current token, and already-responded items no longer appear in pending by default; `decision` is one of `confirmed`, `rejected`, or `needs_followup`; the server validates that the response token hash matches the outbox target user, and returns 401 for a wrong token.

Export the integration bundle:

```bash
curl -sS http://127.0.0.1:8080/diagnose/integrations

PYTHONPATH=src python3 -m codekb diagnose-integration-export \
  --output-dir /tmp/codekb-diagnose-integrations \
  --api-base-url http://127.0.0.1:8080
```

Output:

```text
mcp_tools.json
code_review_skill.md
mr_candidate_card.json
im_entry.md
current_user_auth.md
external_handoff.md
summary.json
```

`external_handoff.md` is the P5 production external-integration checklist, listing IM OAuth, IM message sending, real webhook samples, the webhook shared token, the webhook current-user confirmation policy, and the final acceptance commands; the final acceptance commands include `diagnose-acceptance` and `/diagnose/acceptance`.

### `POST /diagnose/gap-candidate`

Explicitly write the `gap_candidate` produced by a diagnosis into the P3 candidate store, entering the manual-review queue. This endpoint only writes the local candidate state; it does not write to the Wiki and does not create an external ticket.

Input is the same as `/diagnose`, additionally supporting:

```json
{
  "submitted_by_hash": "u_hash",
  "allow_duplicate": false
}
```

Output:

```json
{
  "status": "accepted",
  "diagnosis": {},
  "submission": {
    "duplicate": false,
    "existing_candidate_id": "",
    "candidate": {
      "candidate_id": "uuid",
      "sub_kb_id": "release",
      "source_type": "diagnose",
      "status": "pending_review"
    }
  }
}
```

Resubmitting the same query/sub_kb/finding returns `status=duplicate`. `diagnosis_id`, `answer_id`, `trace_id`, and `gap_fingerprint` are stored in the candidate metadata, and the candidate body stays stable to support P3 dedupe.

### `GET /diagnose/gaps/summary`

Read-only aggregation of P5 diagnostic gap candidates, for the curator to view the gap distribution and duplication trends.

Parameters:

```text
status: optional candidate status filter
limit: max clusters returned, default 20
```

Output:

```json
{
  "total_diagnostic_gaps": 3,
  "clusters_total": 2,
  "counts_by_status": {"pending_review": 3},
  "counts_by_sub_kb": {"testing": 2, "release": 1},
  "clusters": [
    {
      "cluster_id": "hash16",
      "sub_kb_id": "testing",
      "total_candidates": 2,
      "open_candidates": 2,
      "similarity_terms": ["device_seq"]
    }
  ]
}
```

### Trace JSONL

P1 currently uses a JSONL file as a lightweight substitute for the Postgres trace table:

```text
/data/codekb/logs/ask-trace.jsonl
```

Each line contains:

- `answer_id`
- `trace_id`
- `query`
- `sub_kbs`
- `top_k`
- `retriever`
- `sparse_hits`
- `dense_hits`
- `rrf_top20`
- `rerank_hits`
- `hits`: `rank/docid/title/anchor/section_path/score/matched_terms`
- `citations`
- `refused`
- `refusal_reason`
- `confidence`
- `created_at`

### Index Artifacts

P1 can currently export real-service integration artifacts:

```bash
PYTHONPATH=src python3 -m codekb export-index \
  --fixtures data/fixtures/sample_corpus.jsonl \
  --output-dir /tmp/codekb-index-artifacts
```

Output:

```text
source_documents.jsonl
knowledge_atoms.jsonl
postgres_upserts.jsonl
opensearch_documents.jsonl
qdrant_points.jsonl
summary.json
```

### `GET /kb/registry`

Returns the registry config currently in effect, including at least:

- sub_kb id/name/status
- source docs
- retrieval parameters
- production layer filter

### `GET /healthz`

Returns the health status of the API, Postgres, Qdrant, ES, and the model endpoint.

## Error codes

| code | Scenario |
|---|---|
| `NO_CITATION` | Retrieval has no reliable citation; must refuse |
| `ACL_DENIED` | The matched document is not visible to the user |
| `SOURCE_UNREADABLE` | Document read failed |
| `MODEL_UNAVAILABLE` | The generative model is unavailable |
| `INDEX_NOT_READY` | The sub-KB has not finished indexing |
