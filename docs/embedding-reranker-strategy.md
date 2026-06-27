**English** · [简体中文](embedding-reranker-strategy.zh-CN.md)

# Embedding and Reranker Strategy

Last updated: 2026-06-11

## Conclusion

Given limited GPU resources that are only available in short windows:

1. **P1 does not hard-depend on embedding or a reranker.** Bring the read-only RAG MVP and the golden-set evaluation into working order first, using BM25, keywords, entity aliases, and metadata boosts.
2. **Embedding is needed in the medium term**, otherwise semantic questions, synonymous phrasings, and long incident-postmortem retrieval will be limited. Embedding can, however, be computed offline in batches and does not require a resident GPU.
3. **Self-hosting a resident-GPU reranker is not recommended yet.** For P1/P2, use RRF + BM25 + metadata/composite score first; attach a reranker via a short-window GPU or an internal service only once evaluation confirms that hit@4 or accuracy has plateaued.
4. **Prioritize requesting a CPU server rather than a long-term GPU.** Request a GPU only for short windows, for bulk vector building, model evaluation, or periodic rebuilds.

## Why embedding is still needed

Pure BM25 works well for:

- Parameter names, error codes, interface names, document titles
- Exact keywords like `DEVICE_SEQ`, `sourceFileName`, `Channel`
- Release SOPs, UDT parameter tables, explicit-title retrieval

However, pure BM25 is clearly at a disadvantage for:

- Cases where the user's wording differs from the document's wording, e.g. "stuck entering the match" vs. "frozen on loading"
- Postmortem-style questions, e.g. "how was a similar production issue handled before"
- Abstract knowledge formed after summarizing meeting notes and long documents
- Semantic-similarity recall across multiple documents and paragraphs

The strategy is therefore not to avoid embedding altogether, but rather:

> Do not let embedding block the P1 launch; begin introducing offline embedding in P2 to improve recall quality.

## Why the reranker can be deferred

The reranker's benefit lies mainly in the top-20 → top-4 fine-ranking stage, where it reduces the "recalled but ranked too low" problem. Its costs are:

- It must run on every online query, which imposes more direct latency and compute pressure.
- A resident GPU is not cost-effective.
- A CPU-based reranker is usable, but its p95 latency may be unstable.

P1 alternatives:

1. BM25 score.
2. source freshness/owner/status boost.
3. exact entity boost: parameter names, error codes, docids, gameplay names.
4. sub_kb route boost.
5. atom composite score.

Attach a reranker only when the golden set reveals the following problems:

- hit@20 passes but hit@4 does not.
- The same query frequently recalls multiple similar documents with unstable ordering.
- Incident-postmortem questions cite the wrong source.

## How to use the GPU

### Not recommended

```text
Resident-GPU online serving:
- embedding online inference
- reranker online inference
```

Reason: resources are limited and a resident GPU is costly; the P1/P2 query volume is not yet sufficient to justify it.

### Recommended

```text
Short-window GPU batch processing:
- initial bulk embedding
- major model/index rebuilds
- weekly or daily incremental embedding batches
- offline reranker A/B evaluation
```

Where the outputs are written:

- embedding vectors written to Qdrant
- atom metadata written to Postgres
- sparse text written to ES/OpenSearch
- no dependency on a resident online GPU

## Phase strategy

| Phase | Embedding | Reranker | GPU |
|---|---|---|---|
| P1 | Not a hard dependency; BM25-lite/ES BM25 first | Not attached | No long-term GPU requested |
| P1.5 | Short-window GPU to bulk-generate P1 document vectors | Not attached, or offline evaluation | Request a 2–4 hour GPU window |
| P2 | Incremental embedding enters the regular flow | Attach an internal service / small CPU model if hit@4 is insufficient | Daily/weekly short-window GPU, or use an internal embedding service |
| P3 | Embedding required | Reranker enabled depending on evaluation results | Not resident; internal service preferred |
| P4+ | Embedding required | Recommended to enable, but an internal model service is fine | Consider GPU HA only if self-hosting in production |

## P1 concrete execution

P1 begins with the following:

1. ES/OpenSearch BM25.
2. entity alias dictionary.
3. exact match boost.
4. doc freshness/owner/status boost.
5. golden hit@4 evaluator.

P1 pass criteria:

- If the BM25 baseline reaches hit@4 >= 0.75, continue with P1 without attaching embedding.
- If the BM25 baseline has hit@4 < 0.75 but a clearly higher hit@20, prioritize adding rerank and boost rules.
- If the BM25 baseline also has insufficient hit@20, then introduce embedding.

## Short-window GPU request suggestion

If a GPU is requested, do not request long-term occupancy; request a "batch-processing window" instead:

```text
GPU short-window task resources
- 16C CPU
- 64GB memory
- 500GB SSD
- 1x 16GB+ GPU, 24GB preferred
- usage: on-demand 2–4 hour windows
- purpose: bulk generate/rebuild embeddings, offline reranker evaluation
```

Expected P1/P2 usage frequency:

| Scenario | Frequency | Estimated duration |
|---|---:|---:|
| Initial P1 pilot vector build | 1 time | < 1 hour |
| P2 scale-up to 50k–200k atoms | 1–2 times | 2–6 hours |
| Weekly incremental rebuild | Weekly | 1–3 hours |
| Offline reranker evaluation | On demand | 1–2 hours |

## Recommended request framing

Resident server resources:

```text
Required: P1 all-in-one CPU server
- 16C / 64GB / 1TB SSD
- no GPU
```

GPU resources:

```text
Optional: short-window GPU batch resources
- not required to be resident
- 2–4 hours per use
- for embedding batch processing and offline reranker evaluation
```

## Final recommendation

For now, do not request a long-term GPU for P1. First request a CPU server and bring the BM25 baseline, data cleaning, trace, and golden-set evaluation into operation.

Once golden-set evaluation shows that pure BM25 cannot reach hit@4, request a short-window GPU to generate embeddings. Attach the reranker only when hit@20 passes but the top-4 ordering is unstable.
