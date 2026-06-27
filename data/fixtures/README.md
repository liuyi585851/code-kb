**English** · [简体中文](README.zh-CN.md)

# Sample fixtures

A tiny **synthetic** sample corpus, bundled so tests, examples and the CLI run
out of the box. It contains no real or third-party data — replace it with your
own corpus to use Code-KB on real data.

- `sample_corpus.jsonl` — a small JSONL corpus used by the in-memory atom store,
  the BM25-lite retriever, and the golden hit@4 evaluator.
- `golden_questions.md` — a small set of example golden questions over that
  corpus (markdown table format) for the evaluation feature.

## Corpus shape (`sample_corpus.jsonl`)

One JSON object per line:

```json
{"docid": "1000001", "sub_kb_id": "testing", "title": "Example doc", "content_type": "DOC", "url": "https://wiki.example.com/p/1000001", "metadata": {"system": "example", "owner": "alice"}, "body": "## Section\n\nExample body text used for retrieval."}
```

- `docid` — a unique id. Numeric ids (7+ digits) can be referenced by golden
  questions.
- `sub_kb_id` — one of the configured sub-KBs (e.g. `code`, `docs`, `release`,
  `incident`, `testing`); retrieval is scoped per sub-KB.
- `body` — markdown; headings become section anchors used in citations.

## Golden questions shape (`golden_questions.md`)

A markdown table, `ID | question | expected sources | focus`:

```
| TST-001 | What does DEVICE_SEQ mean? | `1000001` | parameter meaning |
```

## Pointing Code-KB at your own data

```bash
CODEKB_FIXTURES=path/to/your_corpus.jsonl   # or pass --fixtures on the CLI
```

A `.yaml`/`.yml` source path is loaded as a wiki-style manifest instead; any
other path is loaded as JSONL.
