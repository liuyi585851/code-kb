<div align="center">

<h1>🧭 Code-KB</h1>

<p><strong>A retrieval and navigation layer over code and documentation for autonomous coding agents: precise location, enforced citations or refusal, always-current content, and full traceability.</strong></p>

<p>Code-KB splits source code and documentation into small, self-describing, locatable <em>atoms</em> and indexes them, returning precise locations relative to the repository root in a single call via hybrid retrieval. It follows a cite-or-die policy: every answer must be supported by a real, retrieved source range, otherwise the service declines to answer and records the gap. Code-KB is a retrieval and navigation layer responsible for quickly locating <code>file:line</code>; the actual reading is performed by the agent in its own local checkout, so it always surfaces the current content rather than a stale mirror, and the harder reasoning is left to a stronger client model, or to a human.</p>

<p>
  <a href="#-quickstart">Quickstart</a> ·
  <a href="#-code-kb-for-ai-agents">For AI Agents</a> ·
  <a href="#-architecture">Architecture</a> ·
  <a href="#-configuration">Configuration</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

<p>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-blue.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white">
  <img alt="MCP" src="https://img.shields.io/badge/MCP-native-7C3AED">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-service-009688?logo=fastapi&logoColor=white">
  <img alt="Qdrant" src="https://img.shields.io/badge/Qdrant-vectors-DC244C">
  <img alt="Core deps" src="https://img.shields.io/badge/core%20deps-PyYAML%20only-success">
  <img alt="PRs welcome" src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg">
</p>

<sub><strong>English</strong> · <a href="README.zh-CN.md">简体中文</a></sub>

</div>

---

> **TL;DR** — Code-KB is a retrieval and navigation layer over code and documentation for AI coding agents. It indexes the corpus into locatable atoms and uses hybrid retrieval to return precise locations relative to the repository root; the agent reads the current file in its own local checkout, so results are always up to date. It is an indexing layer whose job is fast, citable location, not a reasoning engine.

## 📚 Table of Contents

- [Why Code-KB](#-why-code-kb)
- [Highlights](#-highlights)
- [Code-KB for AI agents](#-code-kb-for-ai-agents)
- [Architecture](#-architecture)
- [Repository layout](#-repository-layout)
- [Quickstart](#-quickstart)
- [Tests](#-tests)
- [Configuration](#-configuration)
- [Integrations](#-integrations)
- [Contributing](#-contributing)
- [License](#-license)

## 🤔 Why Code-KB

Coding agents often make repeated attempts to locate code and, when grounding is missing, return speculative answers. Code-KB provides guarantees for both of these concerns.

- **Retrieve, do not guess**: Semantic and structured retrieval are combined to return precise locations relative to the repository root in a single call.
- **Cite or decline**: Answers must carry citations; when no supporting fragment is available, the service declines to answer and records the gap rather than producing fabricated content.
- **Always current**: It returns a location; the agent reads the local, current file, so it never reasons over a stale copy.
- **Lean dependencies, easy to run**: The offline retrieval/evaluation core and the full test suite run with PyYAML as the only dependency; FastAPI, Postgres, Qdrant, and the LLM SDK are all optional components.

## ✨ Highlights

| Capability | Description |
| --- | --- |
| 🔀 Hybrid retrieval | BM25-lite sparse retrieval combined with dense vector retrieval (Qdrant), merged via RRF (Reciprocal Rank Fusion), with an optional cross-encoder reranker. |
| 📌 Cite-or-die `/ask` | Extractive answers that must carry citations; an optional generative mode falls back to extractive automatically when no LLM is configured. |
| 🧭 Code navigation | MCP + HTTP tools for both semantic search and structured discovery: `codekb_search_code`, `codekb_get_symbol`, `codekb_find_files` (locate files by path substring), `codekb_list_dir` (list the immediate subdirectories and files under a given directory), `codekb_read_file_range`, `codekb_file_outline`. |
| 🔎 Query expansion | An optional LLM step that, before retrieval, rewrites a natural-language (or non-English) question into the identifiers actually used in the source, bridging the gap between natural language and source-code identifiers. |
| 🤖 Agent-oriented | MCP-native; ships a built-in `code-kb` skill (a "locate → read → verify" retrieval pattern), along with LangChain/LangGraph adapters and a runnable ReAct agent example. |
| 🚀 FastAPI service | Built on a Postgres atom store and a Qdrant vector store, with a hash-routed single-page console. |
| 🛡️ Governance and feedback | Golden-question evaluation, quality gates, candidate distillation, staleness/ownership governance reports, and a publish pipeline with redline (secrets/PII/internal-domain) filtering. |
| 📦 Standard-library-only core | The offline core and full test suite depend only on PyYAML; heavy dependencies are all optional. |

Every retrieved fragment carries a one-line, self-describing header comment that allows its origin to be traced.

```text
« <repo> · <path>:L<a>-<b> · <lang> · <symbol> »
```

## 🤖 Code-KB for AI agents

- **Agent infrastructure**: Code-KB is a fast, citable index over code and documentation built for autonomous coding agents, not a search box for manual lookup.
- **MCP-native**: Retrieval and code navigation are exposed through the Model Context Protocol; once the service is connected to any MCP client (such as Claude Code or various IDE agents), the tools are registered and available automatically.
- **Built-in agentic retrieval pattern**: The bundled `code-kb` skill (see [`skills/code-kb/SKILL.md`](skills/code-kb/SKILL.md)) encodes a "locate → browse → read → verify" loop, constrained by cite-or-die, so the agent lands directly on a precise `file:line`.
- **Locate fast, read in place**: Code-KB returns relative paths, and the agent reads the current file in its own local checkout. It is an indexing layer, not a copy that gradually goes stale.
- **Framework-agnostic**: Every tool is a plain JSON HTTP endpoint that can be wrapped as a LangChain tool, a LangGraph node, or a tool for any function-calling agent.

> **Tech stack**: RAG (hybrid retrieval + RRF + reranking) · tool calling (MCP) · prompt engineering (cite-or-die + query expansion) · context orchestration (budgeted, self-describing citations) · agent orchestration (LangGraph ReAct + multi-agent locate/read) · optional model serving (pluggable embedder / reranker / LLM).

### LangChain / LangGraph

Ready-to-use adapters are located in the [`examples/`](examples/) directory (`pip install -e '.[agents]'`).

```python
from langgraph.prebuilt import create_react_agent
from langchain.chat_models import init_chat_model
from examples.langchain_tools import CODEKB_TOOLS   # code_search, find_files, list_dir, get_symbol, read_file_range

agent = create_react_agent(init_chat_model("gpt-4o-mini"), tools=CODEKB_TOOLS)
agent.invoke({"messages": [("user", "where is third-party login handled?")]})
# the agent LOCATES via Code-KB, then reads the real files
```

Run the full example with `python examples/langgraph_agent.py "…"`.

> **Multi-agent scenarios**: In multi-agent orchestration, Code-KB serves as the shared code-indexing layer: a locating agent uses it to find modules, while a reading/implementing agent opens the current local file, combining fast location with fresh, authoritative content.

## 🏗️ Architecture

```text
            ┌──────────── ingest ────────────┐
 sources →  normalizer → chunker → atoms ─────┤
 (code +                                      ▼
  docs)                              ┌──────────────────┐
                                     │  Atom store      │  Postgres (prod)
                                     │  + vector store  │  Qdrant (vectors)
                                     └──────────────────┘
                                              │
 query → retrieval: BM25-lite ┐               │
                              ├─ RRF ─ rerank ─→ candidates → answer (cite-or-die)
         dense (Qdrant) ──────┘                                   │
                                                                  ▼
                                                      FastAPI /ask · /diagnose
                                                      MCP code-nav tools · SPA console
```

- **Atoms**: Small, self-describing units (body + context prefix + source anchor), so a retrieved fragment can always be traced back to its origin.
- **Sub-KBs**: Partitions of the corpus (for example `code`, `docs`, `release`, `incident`, `testing`); retrieval is scoped per sub-KB.
- **Trace**: Every answer writes its retrieval hits, citations, and decline reasons to a JSONL trace log.

## 📂 Repository layout

```text
src/codekb/          core library + service + connectors + CLI
  ├─ core            chunker, code_chunker, retrieval, store, candidate,
  │                  citation, answer, evaluator, service  (PyYAML-only)
  ├─ connectors      wiki*, ticket_client (Git), im_*, qdrant_*,
  │                  postgres, local_index   (optional, lazily imported)
  ├─ api             api.py (FastAPI app) + *_page.py server-rendered pages
  ├─ web/            single-page console (index.html, app.js, app.css)
  └─ cli / mcp       cli.py, __main__.py, mcp_server.py
tests/               unit tests
data/fixtures/       synthetic sample corpus + golden questions
examples/            small runnable examples
docs/                design notes and specs
deploy/              example run scripts
```

## 🚀 Quickstart

> **Requires Python ≥ 3.11.**

```bash
# 1. Configure
cp .env.example .env        # then edit values as needed

# 2. Install (editable). Add extras as needed:
pip install -e .                  # core only (PyYAML)
pip install -e '.[api,storage]'   # + FastAPI/uvicorn + Postgres driver
# pip install -e '.[llm]'         # + optional generative-answer SDK

# 3. Run the API + console
python -m uvicorn codekb.api:create_app --factory --host 0.0.0.0 --port 8000
```

By default, the service binds to a local Postgres / Qdrant (see [`.env.example`](.env.example)); the offline/extractive path runs without those components.

### Example

The `data/fixtures/` directory provides a synthetic sample corpus.

```bash
PYTHONPATH=src python examples/ask.py
```

The example loads `data/fixtures/sample_corpus.jsonl`, asks a question, and prints a cited answer. The `codekb` CLI (installed via `pip install -e .`) provides the same flow; see `codekb --help`. For the corpus structure and how to point Code-KB at your own data, see [`data/fixtures/README.md`](data/fixtures/README.md).

## 🧪 Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

The entire test suite requires no network access and completes with zero skips once PyYAML is installed.

## ⚙️ Configuration

All runtime configuration is provided through environment variables prefixed with `CODEKB_`; for the complete list, see [`.env.example`](.env.example) (host/port, Postgres DSN, Qdrant URL, retrieval mode, answer mode, LLM endpoint, auth token, and the various integration settings).

## 🔌 Integrations

Code-KB can connect to external systems as an ingest/publish target: Wiki (documentation), issue tracker (tickets), Git (code), and IM (authentication and notifications). These integrations are all optional and enabled through configuration prefixed with `CODEKB_`.

## 🤝 Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Please observe these three non-negotiables: the core must be importable with PyYAML as the only dependency; the test suite must require no network access and produce zero skips; and do not commit any real or third-party data to the repository.

## 📄 License

[MIT](LICENSE) © Code-KB contributors.
