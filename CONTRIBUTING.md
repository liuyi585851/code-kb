**English** · [简体中文](CONTRIBUTING.zh-CN.md)

# Contributing to Code-KB

Thanks for your interest in contributing! This guide covers the basics.

## Development setup

Requires Python ≥ 3.11.

```bash
pip install -e '.[api,storage]'
```

The core library depends only on `PyYAML`; `fastapi`/`uvicorn` (`[api]`),
`psycopg` (`[storage]`) and `anthropic` (`[llm]`) are optional extras.

## Running the tests

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

A synthetic corpus is bundled under `data/fixtures/` so tests and examples
run out of the box. Do not add real or third-party data to the repository.

## Project layout

```
src/codekb/      core library, connectors, FastAPI app, CLI, MCP server
src/codekb/web/  single-page console (static assets)
tests/           unit tests (one file per module/area)
data/fixtures/   synthetic sample corpus + golden questions
examples/        small runnable examples
docs/            design notes and architecture docs
deploy/          example service/run scripts
```

See the "Architecture" section of `README.md` for the core / connectors / api /
web / cli layering.

## Guidelines

- Keep the core importable with only `PyYAML`; put heavy or optional
  dependencies behind extras and import them lazily.
- Add or update tests for any behavior change; keep the suite at zero skips and
  network-free.
- Prefer small, self-describing, locatable retrieval units (atoms) — the project
  is a retrieval/navigation layer, not a reasoning engine.
- Run the test suite before opening a pull request.

## Reporting issues

Please include the Python version, the command you ran, and the full output.
For security-sensitive reports, contact the maintainers privately rather than
opening a public issue.
