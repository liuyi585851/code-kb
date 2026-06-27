**English** · [简体中文](p0-dependencies.zh-CN.md)

# P0 Dependencies

Last updated: 2026-06-11

This document records the model, storage, service, and permission dependencies of the P1 read-only RAG MVP. Before P0 ends, the availability of each item must be confirmed, or an alternative must be provided.

## Model dependencies

| Capability | Preferred | Purpose | P0 confirmation items | Fallback |
|---|---|---|---|---|
| Query rewrite | Low-cost rewrite model (OpenAI-compatible) | Generate rewritten queries and extract entities | API endpoint, auth, rate limit, cost | Disable query rewrite first and use only the original query |
| Embedding | BGE-large-zh-v1.5 | Chinese semantic vectors | Whether an intranet service exists; vector dimensions; batch QPS | bge-m3 or an existing embedding service |
| Reranker | BGE Reranker v2-m3 | Rerank top-20 to top-4 | Whether an intranet service exists; p95 latency | Use the RRF score + BM25 boost first |
| Generator | OpenAI-compatible model / team model proxy | Generate cited answers | Model proxy, service token, max context, audit requirements | Output a citation pack first, without a long answer |
| AI self eval | Shared with the Generator | P2 self-scoring | Not a hard dependency for P1 | Integrate in P2 |

## Storage dependencies

| Component | Initial purpose | Required for P1 | P0 confirmation items | Degradation plan |
|---|---|---|---|---|
| Postgres | Atom metadata, source_documents, trace | Yes | Instance, database name, account, migration approach | SQLite for local PoC only; it cannot be used for P1 acceptance |
| Qdrant | Dense vector index | Yes | Deployment address, collection naming, payload filter | Local Qdrant container, for the dev environment only |
| Elasticsearch | Sparse BM25 | Yes | Index permissions, IK tokenizer, field mapping | OpenSearch or Postgres FTS as a temporary substitute |
| Redis | L0 cache | No | Can be deferred in P1 | No cache; implement it in P2/P6 |
| S3/object storage | Archiving raw attachments and images | No | P1 keeps only attachment URLs | Do not archive attachments for now |
| Neo4j | KG 1-hop | No | Deferred in P1 | entity_alias + BM25 boost |

## Data-source dependencies

| Data source | P1 purpose | Current status | P0 to confirm |
|---|---|---|---|
| Wiki MCP `getDocument` | Deep-crawl pilot document bodies | DOC/MD verified as readable; TXDOC readable but noisy | Bulk-read rate limiting, TXDOC cleaning strategy |
| Wiki MCP `metadata` | Document owner, modified time, ACL metadata | Verified | Field stability, cross-space ACL expression |
| Wiki MCP `getSpacePageTree` | Enumerate postmortem directories | Verified `1000000004` | Large-directory pagination/depth limits |
| Wiki write APIs | P3 outbound sync | Confirmed comment, append, save, copy, and move; no create or label APIs found | Document-create and label capabilities, or a template-copy degradation plan |
| Git | P4/P5 owner and MR candidate cards | Not a P1 dependency | Project ID, webhook, commit/MR API permissions |
| issue tracker | P4 gap ticket / P5 diagnosis | Not a P1 dependency | workspace, bug/story creation permissions |
| IM | P3 Catcher Bot | Not a P1 dependency | Bot callback, card buttons, proactive-message permissions |

## Runtime-environment dependencies

| Item | P1 requirement |
|---|---|
| Python | 3.11+ |
| API framework | FastAPI + Uvicorn |
| Task queue | A lightweight worker/cron for the first version; add Celery/Arq after P2 |
| Config | YAML registry + env vars |
| Deployment | Start with a single intranet instance; the index worker and the API can run as separate processes |
| Observability | structured log + trace_id; add a dashboard in P2 |

## Permissions and security

1. `/ask` may only be called with a service-account token.
2. ACL filtering must happen before retrieval or before returning results; it cannot rely on UI hiding alone.
3. The `user_id` in traces is stored as a hash; no plaintext personal sensitive information is recorded.
4. A source doc's `can_edit` is only a reference for write capability; it does not mean the content may be freely rewritten.
5. P1 does not automatically write to the Wiki, which avoids permission and knowledge-pollution risks.

## P0 decisions

| Decision | Conclusion |
|---|---|
| Whether to include Redis in P1 | No; P1 prioritizes accuracy and traceability |
| Whether to include Neo4j in P1 | No; use metadata/entity_alias as a fallback |
| Whether to include TXDOC in the P1 deep crawl | Not a P1 acceptance item for now; prioritize metadata |
| Whether to include OCR in P1 | No; screenshots keep only an attachment placeholder and source link |
| Whether cross-space documents may enter Q&A | Yes, but ACL and source URL must be retained |
| Whether work can proceed without a model service | Yes; ingest/retrieval/eval can be completed first, with the generator integrated later |
