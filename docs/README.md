**English** · [简体中文](README.zh-CN.md)

# Design notes & reference

Background on how Code-KB is built. Start with the project [README](../README.md)
for an overview and quickstart.

## Design & reference

| Doc | What it covers |
|-----|----------------|
| [code-retrieval-design.md](code-retrieval-design.md) | Structure-aware code chunking, the self-describing in-text code header, and the code-navigation primitives (search / symbol / find_files / list_dir / read) |
| [embedding-reranker-strategy.md](embedding-reranker-strategy.md) | Embedding model + cross-encoder reranker strategy and trade-offs |
| [data-contracts.md](data-contracts.md) | Core data structures: Atom, citation/answer, feedback, trace, sub-KB registry |
| [interface-spec.md](interface-spec.md) | Module boundaries, Python interfaces, and the HTTP API surface |
| [schema.sql](schema.sql) | Postgres schema for the atom store and supporting tables |
| [p0-dependencies.md](p0-dependencies.md) | Runtime dependencies and what each optional extra pulls in |
| [integration-credentials-guide.md](integration-credentials-guide.md) | Configuring the optional connectors (Wiki / issue tracker / Git / IM) |

## Config files (loaded at runtime)

| File | Used by |
|------|---------|
| [kb-registry.draft.yaml](kb-registry.draft.yaml) | Sub-KB registry (`CODEKB_REGISTRY`) — declares the `code` / `docs` / … sub-KBs |
| [governance-policy.draft.yaml](governance-policy.draft.yaml) | Governance thresholds (staleness, ownership, gap policy) |
| [diagnose-webhook-mapping.draft.yaml](diagnose-webhook-mapping.draft.yaml) | Maps external webhook payloads (CI / MR / issue-tracker / crash / generic) → diagnostic requests |
| [diagnose-webhook-samples.draft.yaml](diagnose-webhook-samples.draft.yaml) | Sample webhook payloads for the mapping above |

`*.draft.yaml` are working defaults — copy and adapt them for your deployment.
