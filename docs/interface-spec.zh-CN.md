[English](interface-spec.md) · **简体中文**

# P1 Interface Spec

更新时间:2026-06-12

本文件定义 P1 只读 RAG MVP 的模块边界和接口草案。目标是让 Hub、Indexer、Retriever、Generator、Evaluator 可以并行实现。

## 模块边界

| 模块 | 职责 | 不负责 |
|---|---|---|
| Registry | 加载 sub_kb 配置、source docs、检索参数 | 判断 query 意图 |
| Source Connector | 读取 Wiki metadata/document/tree | 清洗正文、切片 |
| Source Sync Runner | 调度 source connector、生成增量状态和同步报告 | 在线检索、答案生成 |
| Normalizer | Markdown/HTML/TXDOC 文本清洗 | 生成 embedding |
| Chunker | 按章节/表格/语义边界切块 | 判断知识是否权威 |
| Atom Store | 存储 atoms、sources、versions | 生成答案 |
| Indexer | 写入 Postgres/Qdrant/ES | 在线 query 路由 |
| Retriever | Dense/Sparse/RRF/Rerank | 生成自然语言答案 |
| Generator | cite-or-die 答案生成 | 修改 KB |
| Trace Logger | 记录 query、hits、answer、latency | 评价正确性 |
| Evaluator | 跑黄金集,输出 hit@4/citation/refusal | 线上流量分流 |

## 核心接口

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

`RawDocument` 最小字段:

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

`SyncReport` 当前 JSON 字段:

```text
source_path, started_at, finished_at, total, indexed, skipped, failed, atom_count, missing_docids, results
```

单文档结果:

```text
docid, title, sub_kb_id, status, reason, atom_count, body_sha256, metadata_sha256, source_modified_at
```

P1 的 smoke CLI 已经实现:

```bash
PYTHONPATH=src python3 -m codekb sync --fixtures data/fixtures/sample_corpus.jsonl
```

由 Registry 驱动的真实 Wiki 同步已经实现:

```python
bundle = build_registry_source_bundle(registry, WikiSourceConnector(client))
```

`enumerate_children` 会展开子文档;空正文或目录宏会转为 metadata-only 入口。

### Normalizer

```python
class DocumentNormalizer:
    def normalize(self, raw: RawDocument) -> "NormalizedDocument": ...
```

`NormalizedDocument` 最小字段:

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

切片约束:

- 目标 200-800 中文字。
- 保留 `docid/title/section_path/anchor`。
- 表格按行展开,行内带表头上下文。
- 图片只保留附件占位。

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

P1 默认 `bm25-lite`;可选 `hybrid-lite` 使用本地 deterministic dense + sparse + RRF + rerank trace,不依赖 GPU 或外部模型。

### Generator

```python
class Generator:
    def answer(self, query: str, citations: list["CitationPack"]) -> "AnswerResult": ...
```

规则:

- `citations` 为空时必须拒答。
- 只允许引用 `CitationPack` 中的事实。
- 答案默认不超过 400 中文字。

### Evaluator

```python
class GoldenEvaluator:
    def load_questions(self, path: str) -> list["GoldenQuestion"]: ...
    def run(self, questions: list["GoldenQuestion"]) -> "EvalReport": ...
```

## HTTP API

### `POST /ask`

输入:

```json
{
  "query": "DEVICE_SEQ 是什么？",
  "surface": "api",
  "user_id_hash": "u_xxx",
  "sub_kbs": ["testing"]
}
```

输出:

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

P5 诊断入口复用 `/ask` 的检索和引用结果,额外返回诊断 finding、建议动作、gap candidate 草案和相关治理项。

输入:

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

`query` 可为空;为空时会从 `context.error_code`、`context.error_text`、`context.log_excerpt` 以及 repo/build 等字段派生诊断 query。若 `query` 和错误上下文都为空,接口返回 400。

安全处理:

- `query`、`context.error_text`、`context.log_excerpt` 和 `context.links` 在进入检索、返回结果、写入 gap candidate metadata 前会做脱敏。
- 当前的脱敏会处理 `password/token/access_token/refresh_token/client_secret/corpsecret/user_ticket/signature/api_key/access_key/authorization/cookie` 等键,覆盖它们出现在赋值语句、JSON 字段、CLI 参数、header 和 URL query 参数中的情况。
- 普通知识字段名如 `ACCOUNT_TOKEN` 会保留,只有携带实际值的 `token=...` 或 `Authorization: ...` 会被替换为 `[REDACTED]`。

输出:

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

当前 P1 API 已实现 `answer_id`、`trace_id`、`refused`、`refusal_reason`、`citations`、`confidence`。

### `POST /diagnose/webhook/{source}`

P5 外部回调入口。该接口不直接依赖 code review/CI/MR/issue tracker/Crash SDK,只把平台 payload 映射到统一 `/diagnose` 输入后执行诊断。支持 `source=code_review|ci|mr|issue_tracker|crash|generic`。

输入示例:

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

输出:

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

写入 gap candidate 使用 `POST /diagnose/webhook/{source}/gap-candidate`,返回字段与 `/diagnose/gap-candidate` 一致。若环境变量 `CODEKB_DIAGNOSE_WEBHOOK_TOKEN` 已配置,请求必须带 `X-CodeKB-Token`。如果 webhook 诊断需要人工审核或问题解决确认,payload 还必须携带当前用户绑定的 `auth_token` 和 `confirmation_policy=needs_review|always`;确认只推送给该 token 绑定的当前用户,不根据仓库、owner 或接口人字段做路由。webhook 审计事件不会保存 `auth_token` 或 confirmation 控制字段。
字段映射默认读取 `docs/diagnose-webhook-mapping.draft.yaml`,也可通过 `CODEKB_DIAGNOSE_WEBHOOK_MAPPING` 指向其他 YAML;source 专属路径优先于 default 和内置兼容路径。

### `GET /diagnose/webhook/{source}/mapping`

只读查看当前 source 生效的 webhook 字段映射,用于接入前核对真实平台 payload 是否会被正确抽取。

输出:

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

只读运行当前 webhook 样例套件。接口不执行诊断、不写 webhook 审计,也不返回原始样例 payload;只返回校验结果、已脱敏 query/context、字段抽取状态和错误摘要。

样例清单默认读取 `CODEKB_DIAGNOSE_WEBHOOK_SAMPLES=docs/diagnose-webhook-samples.draft.yaml`,字段映射读取 `CODEKB_DIAGNOSE_WEBHOOK_MAPPING=docs/diagnose-webhook-mapping.draft.yaml`。

响应:

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

Webhook payload 预检入口。该接口只执行字段映射、脱敏和诊断 query 派生,不运行检索、不写审计日志。

输出:

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

Webhook payload 校验入口。该接口只执行字段映射、脱敏、诊断 query 派生和字段完整度检查,不运行检索、不写审计日志。字段不足不会直接 400,而是返回 `valid=false` 和 `errors/warnings`。

输出:

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

Webhook 诊断事件审计摘要,只读。

查询参数:

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `source` | 空 | 按 `code_review`、`ci`、`mr`、`issue_tracker`、`crash`、`generic` 过滤 |
| `status` | 空 | 按 `diagnosed`、`accepted`、`duplicate`、`bad_request`、`unauthorized`、`error` 过滤 |
| `action` | 空 | 按 `diagnose` 或 `gap_candidate` 过滤 |
| `limit` | 20 | 最近事件数量,范围 0-200 |

输出:

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

成功事件只保存归一化后的诊断摘要;失败事件保存 source/action/status、错误类型和已脱敏错误摘要。不保存原始 webhook payload;`query/context/links` 已经过诊断上下文脱敏。

### P5 MCP 与研发入口 artifacts

P5 提供 stdio MCP server 和接入 artifacts,供 IDE、code review、MR 卡片和企微入口复用同一 KB Hub。接入 artifacts 可通过 `GET /diagnose/integrations` 获取当前 API base 下的只读 JSON 包,也可通过 CLI 导出为文件。
Webhook 样例套件可通过 `diagnose-webhook-sample-suite` 批量校验 code review/CI/MR/issue tracker/Crash/generic payload 的字段抽取、query readiness、期望上下文,以及禁止出现的 secret 未被泄漏;默认样例清单为 `docs/diagnose-webhook-samples.draft.yaml`,可用 `CODEKB_DIAGNOSE_WEBHOOK_SAMPLES` 替换为真实平台样例。

真实平台 payload 到位后,先用 `diagnose-webhook-sample-import` 或 `POST /diagnose/webhook/{source}/sample-import` 将 payload 递归脱敏并导入 real 样例套件,再用 `diagnose-webhook-sample-activate --apply --confirm-real-samples` 校验并把 `CODEKB_DIAGNOSE_WEBHOOK_SAMPLES` 写入 server-only env 文件。HTTP 导入要求 `X-CodeKB-Admin-Token`,默认写入 `CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES=/data/codekb/state/diagnose-webhook-samples.real.yaml`。导入器会检查原始敏感值未写入样例 YAML;原始真实 payload 不应提交到仓库。

P5 readiness 可通过 `GET /diagnose/readiness` 或 `diagnose-readiness --json` 查看。返回项包括核心诊断文件、webhook mapping/sample suite、MCP tools/auth_token 要求、当前用户 token store、确认 outbox、企微 OAuth/发送配置、webhook shared token 和真实平台样例来源。状态取值为 `ready`、`ready_with_warnings` 或 `blocked`;外部配置未接入时会显示 `deferred/warn` 和 `required_actions`,但不会返回任何 secret 原文。剩余外部输入可通过 `GET /diagnose/external-inputs`、`GET /diagnose/external-inputs.md`、`GET /diagnose/external-inputs/page` 或 `diagnose-external-inputs --json` 查看,返回任务、owner、所需变量名、安全命令和验收命令,不含 secret 原文;该计划会合并 `/diagnose/external-state` 的证据检查,并把 state-only 缺口如 `im_template` 展示为任务,同时返回 `operator_handoff`,包含推荐执行顺序、按 owner 分组、下一步动作、完成标准和最终 gate 命令;final gate 命令显式包含 `diagnose-p5-external-state` 和 `/diagnose/external-state`,确保外部状态 ready 有独立证据。Markdown/页面入口还展示当前用户授权策略:MCP 调用前先完成 IM OAuth / 网页 token 绑定,MCP 使用 `auth_token`,确认目标是当前授权用户,不使用接口人识别。OAuth 暂不可用时,管理员可在确认用户企微身份后使用 `GET /auth/im/token-bindings/page` 受控签发当前用户 token,该 fallback 仍要求 `CODEKB_AUTH_ADMIN_TOKEN`。`GET /diagnose/final-verification` 和 `/diagnose/final-verification/page` 复用 readiness、external-state 和 external-input plan,按 runtime state、IM OAuth、当前用户授权、企微投递、真实样例和最终 gate 展示配置后的验收阶段,并透出同一份 operator handoff,保证最终验收页和外部输入页的 next action/完成标准一致。`GET /diagnose/external-state` 或 `diagnose-p5-external-state --json` 可进一步输出企微模板/env、token store、真实样例和发送开关的布尔证据。`diagnose-p5-handoff-bundle --output-dir <dir>` 会把当前计划写成 `external-inputs.json/md`,生成 `im-config.todo.env` 安全模板,并导出 `integrations/` artifacts 和 README,便于直接交给企微/平台接入方。最终验收使用 `GET /diagnose/acceptance` 或 `diagnose-acceptance --json`;只有 readiness 为 `ready` 时才返回 `accepted=true`,CLI 未验收时返回非 0。`diagnose-p5-final-verify --json --output <path>` 会进一步执行并汇总单测、质量门禁、readiness、acceptance、external-inputs、external-state、external-input-plan-alignment、样例套件、MCP auth-error fallback、MCP 静态 token 默认拒绝、MCP token-store 拒绝共享 static token、handoff bundle、IM/current-user smoke、HTTP readiness/external-state/webhook-token-guard/webhook-sample-import-smoke/setup-status/setup-page/token-binding-page/token-binding-fallback-smoke/im-configure-page/im-configure-guard/im-configure-plan/external-inputs-page/external-inputs-markdown/final-verification-json/final-verification-page/current-user-smoke/confirmation-request/acceptance 和 confirmation worker dry-run;只有当前用户 smoke/confirmation 命令继承 `CODEKB_USER_AUTH_TOKEN`,其它命令会剥离该环境变量。报告区分 `pending_required` 与 `failed_required`,顶层返回 `external_input_handoff` 机器可读摘要,包含 status、pending_count、ordered_task_ids、next_action、completion_criteria 和 secret 标记,并把最终证据写入 `0600` JSON 文件。非 JSON 输出会打印 `HANDOFF`、`HANDOFF_SAFE` 和 `HANDOFF_VERIFY` 行,用同一份 handoff 数据暴露下一步安全命令和验证命令,便于终端值守时不解析 JSON 也能推进配置。

P5 安全配置可通过 `diagnose-security-bootstrap` 生成 server-only env 片段,默认包含 `CODEKB_DIAGNOSE_WEBHOOK_TOKEN`、`CODEKB_IM_OAUTH_STATE_SECRET` 和 `CODEKB_AUTH_ADMIN_TOKEN`。如写入文件,权限为 `0600`;生成值只应进入服务器环境,不应提交到仓库。静态 `CODEKB_MCP_TOKEN` 只在显式 `--include-static-mcp-token` 时生成,并仅用于未配置 token store 的本地诊断冒烟。

MCP server:

```bash
PYTHONPATH=src python3 -m codekb diagnose-mcp-server \
  --token-store /data/codekb/state/user-tokens.json \
  --confirmation-outbox /data/codekb/outbox/user-confirmation.jsonl
```

当前工具:

| tool | 用途 |
|---|---|
| `codekb_diagnose` | 调用本地 KB 诊断,返回引用、finding、gap candidate |
| `codekb_diagnose_webhook_validate` | 校验外部 webhook payload 抽取完整度 |
| `codekb_diagnose_webhook_normalize` | 预览 webhook payload 映射结果 |
| `codekb_request_user_confirmation` | 向当前授权用户请求问题解决或人审确认 |

MCP 调用前要求当前用户完成 IM 授权或网页端 token 绑定。生产 MCP server 通过 `--token-store` 校验绑定 token;工具调用必须通过 `auth_token` 传入当前用户 token。`CODEKB_MCP_TOKEN`/`--mcp-token` 默认不会作为 MCP 鉴权源;只有显式传 `--allow-static-mcp-token` 且未配置 token store 时,才允许本地诊断冒烟,生产禁止启用该开关。配置 `--token-store` 后共享静态 token 会被忽略,不能发起确认。未配置 token store 时,MCP 工具调用默认会拒绝执行;确认工具始终要求有效 token store。需要确认时写入 `CODEKB_USER_CONFIRMATION_OUTBOX`,默认 `/data/codekb/outbox/user-confirmation.jsonl`,只保存 token hash。`codekb_diagnose`、HTTP `POST /diagnose` 和 webhook `POST /diagnose/webhook/{source}` 均支持 `confirmation_policy=never|needs_review|always`,默认 `never`;`needs_review` 会在拒答、低置信、治理风险或 gap candidate 时写入当前用户确认 outbox,`always` 每次诊断都写入确认。webhook 入口同时要求平台共享 token `X-CodeKB-Token` 和当前用户 `auth_token`,二者用途不同。网页/AI 客户端在问题已解决、交互完成或其它后置人审时可调用 `POST /auth/im/confirmations/request` 显式请求当前用户确认。P5 确认不通过复杂的接口人识别来路由;owner 信息只用于治理报告、候选分派和兜底上下文。

MCP 未授权 JSON-RPC 错误在 `error.message` 中保留兼容文本,并在 `error.data` 中返回 `authorization_required=true`、`reason`、`setup_url`、`im_oauth_login_url`、`auth_token_argument=auth_token`、`token_store_configured`、`static_token_configured`、`static_token_allowed` 和 `remediation`。客户端应展示 `setup_url` 让当前用户完成授权,不应提示使用共享静态 token。

确认发送由 `user-confirmation-outbox` worker 负责,该 worker 消费 outbox,并通过 token hash 回查当前用户的 token binding;`deploy/codekb-confirmation-worker` 可长期循环运行。真实企微应用消息发送要求 token binding metadata 带 `im_userid`,且显式设置 `CODEKB_ENABLE_IM_SEND=1`、`CODEKB_IM_CORP_ID`、`CODEKB_IM_AGENT_ID`、`CODEKB_IM_APP_SECRET`;默认 dry-run 只生成 `CODEKB_USER_CONFIRMATION_REPORT`。真实发送成功会写入 `CODEKB_USER_CONFIRMATION_DELIVERY_LOG`,后续循环跳过已投递 confirmation,避免重复推送。

网页端 token binding:

| 接口 | 用途 |
|---|---|
| `GET /auth/im/oauth/login?next=...` | 发起IM OAuth 授权,next 只允许同域相对路径 |
| `GET /auth/im/oauth/callback?code=...&state=...` | 校验 state、换取当前企微用户身份并签发当前用户 token |
| `GET /auth/im/mcp/setup` | 当前用户 MCP 授权状态页,可发起 OAuth、检查浏览器 token、运行 self-test 并复制 MCP `auth_token` 参数 |
| `GET /auth/im/mcp/setup/status` | 当前用户 MCP 授权状态 JSON,返回 OAuth 缺失项、callback URL、external inputs JSON/Markdown/page URL、final verification JSON/page URL、token binding fallback page URL、IM configure page/API URL、确认页 URL、current-user demo URL、current-user smoke URL、token store 计数和 `mcp_auth_strategy`,不返回 secret |
| `GET /diagnose/external-inputs.md` | P5 剩余外部输入 Markdown 检查清单,`text/markdown` + `Cache-Control: no-store`,复用 `/diagnose/external-inputs` 计划,并显示 external-state 状态摘要 |
| `GET /diagnose/external-inputs/page` | P5 剩余外部输入网页检查清单,复用 `/diagnose/external-inputs` 计划,展示当前用户授权策略、external-state 摘要和最终验收命令 |
| `GET /diagnose/final-verification` | P5 配置后验收 JSON guide,复用 readiness、external-state 和 external-input plan,展示阶段、URL、命令、当前状态和 `operator_handoff`,不返回 secret |
| `GET /diagnose/final-verification/page` | P5 配置后验收网页 guide,`Cache-Control: no-store`,展示 runtime state、IM OAuth、当前用户授权、投递、真实样例、operator handoff 和最终 gate |
| `POST /auth/im/current-user/status` | 当前用户自查 token 是否有效,只返回 public binding,不返回原始 token |
| `POST /auth/im/current-user/smoke` | 当前用户自助联调入口,要求有效 token,创建当前用户确认并验证 dry-run 路由,不返回原始 token 或企微 userid |
| `GET /demo/current-user` | 当前用户端到端网页 demo,把 token 校验、诊断、显式确认请求、web push inbox 和确认回写串成一个可落地用例 |
| `GET /demo/webhook` | 平台 webhook 接入自测网页,支持 code review/CI/MR/issue tracker/Crash/generic payload 的 normalize、validate、diagnose、gap candidate 和当前用户确认 |
| `GET /auth/im/configure/page` | 管理员网页配置工具,表单提交到 `/auth/im/configure`,页面不保存、不回显 secret |
| `POST /auth/im/configure` | 管理员受控写入企微 OAuth/发送配置到 API 当前 `CODEKB_ENV_FILE`,响应只返回 key 状态和 hash 前缀,不返回 secret |
| `GET /auth/im/token-bindings/page` | 管理员受控 token binding fallback 网页工具,OAuth 暂不可用且已确认用户企微身份时签发当前用户 token,`Cache-Control: no-store` |
| `POST /auth/im/token-bindings` | 管理员受控兜底发放当前用户 token,原始 token 只返回一次;未提供 `user_id_hash` 但提供 `metadata.im_userid` 时后端派生 hash |
| `GET /auth/im/token-bindings/summary` | 管理员查看 token binding 摘要,不返回原始 token |
| `POST /auth/im/token-bindings/{token_id}/revoke` | 管理员撤销当前用户 token |
| `GET /auth/im/confirmations/page?confirmation_id=...` | 企微 textcard 或网页端打开的确认页 |
| `POST /auth/im/confirmations/request` | 当前用户显式创建确认请求,用于交互完成、问题解决或其它人审时机,需带当前用户 `auth_token` |
| `POST /auth/im/confirmations/pending` | 当前用户查看待确认事项,需带当前用户 `auth_token` |
| `POST /auth/im/confirmations/{confirmation_id}/detail` | 当前用户查看单个确认详情,需带当前用户 `auth_token` |
| `POST /auth/im/confirmations/{confirmation_id}/response` | 当前用户回写确认结果,需带当前用户 `auth_token` |
| `GET /auth/im/confirmations/responses/summary` | 查看确认响应摘要,不返回原始 token |
| `GET /index/status` | 查看 SQLite local index 当前状态和 source/atom 统计 |
| `POST /index/rebuild` | 管理员受控重建 SQLite local index,默认包含 pending docs,使用原子替换,需 `X-CodeKB-Admin-Token` |
| `GET /audit/page` | Curator 审核工作台,串联候选队列、详情、修订、审核和索引状态 |
| `GET /ingest/candidates/{candidate_id}` | 查看候选详情和审计历史 |
| `POST /ingest/candidates/{candidate_id}/revision` | 对 `needs_revision` 候选提交修订,候选回到 `pending_review` |
| `GET /publish/plan` | 基于 pending docs 生成发布计划 dry-run,不写 Wiki |
| `GET /publish/readiness` | 管理员查看发布目标配置、pending docs、outbox/report 路径和真实写入 gate 状态 |
| `POST /publish/configure` | 管理员受控写入发布目标配置到 API 当前 `CODEKB_ENV_FILE`,apply 后同步当前进程 env |
| `POST /publish/outbox/plan` | 管理员受控把当前发布计划写入 Wiki publish outbox,需 `X-CodeKB-Admin-Token` |
| `POST /publish/outbox/process` | 管理员受控校验/处理 publish outbox 并写报告;真实写入默认阻断,需写入开关和真实 client |

token binding 默认写入 `CODEKB_USER_TOKEN_STORE=/data/codekb/state/user-tokens.json`。HTTP token 管理、确认响应 summary 和真实 webhook 样例导入必须配置 `CODEKB_AUTH_ADMIN_TOKEN` 并携带 `X-CodeKB-Admin-Token`;未配置时接口拒绝请求。普通用户主路径是 OAuth 登录/回调,按 `CODEKB_IM_OAUTH_STATE_SECRET` 做 state 签名校验并自动签发 token。`diagnose-im-oauth-smoke` 可在不暴露 secret 的情况下校验 OAuth env、state、authorize URL 和 token store 状态。用于投递的 `im_userid` 保存在 binding metadata 中,public 摘要、current-user status 和 current-user smoke 只返回 hash/公开确认信息。

OAuth 运行配置:`CODEKB_IM_CORP_ID`、`CODEKB_IM_AGENT_ID`、`CODEKB_IM_APP_SECRET`、`CODEKB_IM_OAUTH_REDIRECT_URI`、`CODEKB_IM_OAUTH_STATE_SECRET`。回调成功页只展示原始 token 一次,并写入同域浏览器 `localStorage.codekb_user_token`;store 只保存 token hash。

`diagnose-im-configure` 和 `POST /auth/im/configure` 可安全地将上述企微配置写入 server-only env 文件,输出只包含 key、状态和 hash 前缀;CLI `--template-output` 可生成 `0600` 待填模板且不复制现有 secret,填好后用 `--from-template ... --apply` 写入正式 env。HTTP 配置入口必须配置并校验 `CODEKB_AUTH_ADMIN_TOKEN`,只写 API 当前 `CODEKB_ENV_FILE`,不接受请求体覆盖 env 路径;启用真实企微发送必须显式传 `--enable-send --confirm-real-send` 或 HTTP `enable_send=true` + `confirm_real_send=true`。

确认响应默认写入 `CODEKB_USER_CONFIRMATION_RESPONSES=/data/codekb/state/user-confirmation-responses.jsonl`。pending/detail 只返回当前 token 对应的确认,已响应事项默认不再出现在 pending;`decision` 取值为 `confirmed`、`rejected` 或 `needs_followup`;服务端会校验响应 token hash 与 outbox 目标用户一致,错误 token 返回 401。

导出接入包:

```bash
curl -sS http://127.0.0.1:8080/diagnose/integrations

PYTHONPATH=src python3 -m codekb diagnose-integration-export \
  --output-dir /tmp/codekb-diagnose-integrations \
  --api-base-url http://127.0.0.1:8080
```

输出:

```text
mcp_tools.json
code_review_skill.md
mr_candidate_card.json
im_entry.md
current_user_auth.md
external_handoff.md
summary.json
```

`external_handoff.md` 是 P5 生产外部接入清单,列出IM OAuth、企微消息发送、真实 webhook 样例、webhook shared token、webhook 当前用户确认策略和最终验收命令;最终验收命令包含 `diagnose-acceptance` 和 `/diagnose/acceptance`。

### `POST /diagnose/gap-candidate`

显式把诊断产生的 `gap_candidate` 写入 P3 candidate store,进入人工审核队列。该接口只写本地候选状态,不写 Wiki,不创建外部工单。

输入同 `/diagnose`,额外支持:

```json
{
  "submitted_by_hash": "u_hash",
  "allow_duplicate": false
}
```

输出:

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

重复提交同一 query/sub_kb/finding 时返回 `status=duplicate`。`diagnosis_id`、`answer_id`、`trace_id` 和 `gap_fingerprint` 保存在 candidate metadata 中,候选正文保持稳定以支持 P3 dedupe。

### `GET /diagnose/gaps/summary`

只读汇总 P5 诊断缺口候选,供 curator 查看缺口分布和重复趋势。

参数:

```text
status: optional candidate status filter
limit: max clusters returned, default 20
```

输出:

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

P1 当前以 JSONL 文件作为 Postgres trace 表的轻量替代:

```text
/data/codekb/logs/ask-trace.jsonl
```

每行包含:

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

P1 当前可导出真实服务接入 artifacts:

```bash
PYTHONPATH=src python3 -m codekb export-index \
  --fixtures data/fixtures/sample_corpus.jsonl \
  --output-dir /tmp/codekb-index-artifacts
```

输出:

```text
source_documents.jsonl
knowledge_atoms.jsonl
postgres_upserts.jsonl
opensearch_documents.jsonl
qdrant_points.jsonl
summary.json
```

### `GET /kb/registry`

返回当前 registry 生效配置,至少包含:

- sub_kb id/name/status
- source docs
- retrieval 参数
- production layer filter

### `GET /healthz`

返回 API、Postgres、Qdrant、ES、model endpoint 的健康状态。

## 错误码

| code | 场景 |
|---|---|
| `NO_CITATION` | 检索无可靠引用,必须拒答 |
| `ACL_DENIED` | 命中文档用户不可见 |
| `SOURCE_UNREADABLE` | 文档读取失败 |
| `MODEL_UNAVAILABLE` | 生成模型不可用 |
| `INDEX_NOT_READY` | 子 KB 尚未完成索引 |
