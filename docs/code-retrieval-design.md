**English** · [简体中文](code-retrieval-design.zh-CN.md)

# Code retrieval design (Code-KB)

> Scope clarification (essential): **this project builds only the knowledge-base side together with the client-side harness, skill, and MCP. The party that actually analyzes and solves problems is the strong client model (for example, an LLM agent).** The knowledge base is therefore **not a code-comprehension engine but a retrieval and navigation service**. Success is measured against only two criteria:
> 1. Return the **correct** references (documents, knowledge atoms, or code snippets) — that is, recall and precision;
> 2. The returned code snippets must be **directly understandable by the client AI** — self-describing, non-fragmented, locatable, and continuable (the client can fetch more).
>
> Cross-file understanding and multi-hop call-graph reasoning are **the responsibility of the client** (which will call our MCP tools again to perform its own multi-hop traversal) and fall outside the scope of this service.

## 1. Architecture: a retrieval/navigation service (not a comprehension engine)

- **Docs (.md / explanatory text) → the existing vector RAG** (prose, which RAG handles well), sub-KB `docs`.
- **Code → structure-aware, self-describing code atoms** + **navigation MCP tools**, sub-KB `code`.
- Once the client holds snippets that are correct, understandable, and continuable, it reasons on its own and follows the references.

## 2. Code-atom design (shipped, zero schema migration)

Each code atom is **one complete semantic unit** (a whole function or whole class; only oversized functions are given overlapping splits) and carries its own provenance:

- **In-text header note** (written into `text`, travels with the snippet to the client):
  `« 代码大仓 · <repo> · <path>:L<start>-<end> · <language> · <symbol> »`
- **Location is carried in existing fields** (no new columns):
  - `source_docid = <repo>/<repo-relative-path>` (e.g. `AIKnowledge/Source/Weapon/Weapon.lua`)
  - `source_anchor = <source_docid>#L<start>-<end>`
  - `section_path = (repo, dirs…, symbol)`
- `code_location.parse_code_location()` reconstructs `repo/file_path/start_line/end_line/language/qualified_symbol` from these fields; `CitationPack` adds them as **optional fields**, and on a code hit the `/ask` citation JSON emits `file_path/start_line/end_line/language/repo_id/symbol`, so that the client can locate, jump to, or fetch more. Doc atoms are unaffected (with no `#L`, parsing returns None).

Implementation files:
- `src/codekb/code_location.py` — parser (CodeLocation)
- `src/codekb/code_chunker.py` — structure-aware chunking `chunk_code()` + source-ingestion traversal `walk_repo()`
- `src/codekb/models.py` — `CitationPack` gains optional code fields
- `src/codekb/citation.py`, `api.py` — populate and expose the code location

Chunking strategy (`code_chunker`): cut on definition boundaries (py: def/class; lua: function; md: headings; c-family: type declarations and function signatures, handled conservatively to avoid mis-cutting inside a function); pack small pieces up to a ~110-line budget; split oversized functions into ~110-line windows with 12-line overlap, backfilling the owning symbol into each piece; during traversal, prune vendored, build, and cache directories (but do not prune `Content`; assets are filtered by extension and binary-skip), and skip binary, compressed, or oversized files.

## 3. Retrieval flow (Chinese NL questions)

1. (Optional) query rewrite: deepseek produces candidate English symbols, **taken as a union with the original rather than as a replacement**.
2. Hybrid retrieval: dense (semantic) + **identifier-aware BM25** (for exact symbols, paths, and error codes) → RRF.
3. reranker fine-ranking → top-k.
4. Return **self-describing snippets + file:line citations** to the client; the client fetches more via the navigation tools as needed.

## 4. Navigation MCP tools (planned for P3, handing multi-hop back to the client)

- `search_code(query, sub_kbs?)` → matched snippets (with header note + file:line)
- `get_symbol(name)` → the definition snippet of a symbol
- `read_file_range(path, L<start>-<end>)` / `get_file_outline(path)` → fill in context on demand
- (Optional) `find_references(symbol)`

This tool set is lightweight (a symbol and file index is enough to support it); it delegates cross-file association to the strong client best suited for it, and avoids spending our effort on call-graph analysis or PageRank.

## 5. Key choices (rationale in the code-retrieval research workflow)

- **Reranker**: the gateway `qwen3-reranker-8b` (MTEB-Code 41→81, GPU-hosted, no local CPU cost, no rebuild needed) — the single highest-ROI precision gain; **it improves precision only and does not add recall**.
- **Sparse**: identifier-aware BM25 (splitting camelCase, snake_case, and paths); on exact symbols it demonstrably outperforms any embedder, at negligible CPU cost.
- **Embedding**: deferred to the last step and treated as an **evidence gate**; first attempt to falsify BGE-large-zh (close to the same modality once header-note enrichment is applied), and before migrating to qwen3, first fix `embedding_remote.py` (its instruction and dimensions handling); **falling back across vector spaces is not permitted**.
- **Chunking**: structure-aware with header-note enrichment; naive line-cutting and pure function-cutting are rejected.

## 6. Deployment realities

- **The repo is not on the server**: a two-plane design separating offline indexing from online serving — chunking and embedding run where the repo is visible, and at query time only a short question is available; alternatively, when the client is inside the repo, the service mainly returns file:line pointers + cross-repo and curated atoms.
- **CPU-only**: embedding and reranking are moved up to the gateway; the CPU only performs ANN and RRF.
- **Freshness**: incremental indexing (keyed on a content hash, re-chunking and re-embedding only the files that have changed).
- **Qdrant**: a shadow collection + an atomic alias switch; never recreate the production collection in place.

## 7. Phasing

- **P0** (to do): RepoCrawler (exclusion rules) + incremental indexing + evaluation (Chinese NL → the correct snippet hits gold).
- **P0.5 ✅ shipped**: code location carried with the atom (zero schema migration) + CitationPack code fields + `/ask` exposes file:line.
- **P1 ✅ shipped**: structure-aware + header-note chunking pipeline (`code_chunker`).
- **P2**: identifier BM25 + gateway reranker (getting the ranking right — the most cost-effective quality improvement).
- **P3**: navigation MCP tools wired into the existing MCP server + skill.
- **P4 (evidence gate, optional)**: an embedder head-to-head comparison (BGE-zh vs qwen3).

## 8. Explicit non-goals (these are the client's job or low ROI)

On our side the service **does not do** the following: cross-file understanding or call-graph reasoning, symbol-graph PageRank centrality, GraphExpander pre-materialized neighbors, per-chunk LLM-generated Chinese summaries, Neo4j, SCIP (there is no Lua indexer), a CPU self-hosted code embedder, or naive line-cutting.
