**English** · [简体中文](README.zh-CN.md)

# Examples

Small, self-contained examples that run against the bundled synthetic corpus
(`data/fixtures/sample_corpus.jsonl`).

## `ask.py`

Loads the sample corpus and asks a question, printing the cite-or-die answer and
its citations.

```bash
PYTHONPATH=src python examples/ask.py
# or, after `pip install -e .`:
python examples/ask.py
```

## `langchain_tools.py` — LangChain tool adapters

Wraps the Code-KB HTTP API as LangChain `@tool`s (`code_search`, `find_files`,
`list_dir`, `get_symbol`, `read_file_range`); `CODEKB_TOOLS` can be passed
directly to any LangChain/LangGraph agent. Point it at a running server with
`CODEKB_URL`.

## `langgraph_agent.py` — a LangGraph ReAct agent

A ReAct agent that drives Code-KB in the intended way: **locate** code with the
KB (expand concept → identifiers → search/find_files/list_dir), then read the
actual file. Requires the `[agents]` extra and a running Code-KB server.

```bash
pip install -e '.[agents]'
export CODEKB_URL=http://localhost:8000
export OPENAI_API_KEY=...          # or set CODEKB_AGENT_MODEL to any chat model
python examples/langgraph_agent.py "where is third-party login handled?"
```

> These agent examples are illustrative and require network access and an LLM,
> so they are **not** part of the (network-free, zero-skip) test suite.
