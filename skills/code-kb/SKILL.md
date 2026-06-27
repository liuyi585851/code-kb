---
name: code-kb
description: Use when you must locate or understand code/docs in the monorepo. Code-KB is a fast INDEX over the repo (paths, symbols, structure) — use it to LOCATE, then read the REAL files in the local monorepo checkout. Invoke for "where is the X module / which directory / how is Y implemented / what does error code Z mean".
---

# Code-KB: locate fast, read local

Code-KB is an **index and navigator over the monorepo, not the source of truth.** Its purpose is
to direct you to the relevant location *quickly* (repo-relative paths, symbols, structure) without
requiring a local copy of the repository. The snippets stored in the KB are a **snapshot taken at
the last ingest and may be out of date.**

The governing principle is therefore: **use the KB to LOCATE, then read the actual code in the
LOCAL monorepo checkout** (the development machine holds it, and it is current). Fall back to the
KB's stored content only when the repository is not available locally. **When a KB snippet and the
local file disagree, trust the local file.**

## Tools (MCP, read-only) — these return LOCATIONS to open locally
- `codekb_search_code(query, sub_kbs?, top_k?)` — concept search returning hits as `repo/file:line`.
- `codekb_find_files(pattern)` — files whose PATH contains a substring; use it to discover a module by name.
- `codekb_list_dir(prefix?)` — the immediate sub-directories and files under a path, for browsing the tree.
- `codekb_get_symbol(name)` — the file:line that defines or contains a symbol.
- `codekb_read_file_range(path, a, b)` / `codekb_file_outline(path)` — **fallback content read** for use when the repository is NOT available locally (a snapshot that may be stale).

(MCP tool names may be prefixed depending on the deployment.)

## Workflow
1. **LOCATE via the KB (fast, no repository required).** Expand the request into likely English
   identifiers and call `search_code`; if the results look generic (directory-convention documents,
   templates), switch to **structural discovery** — use `find_files("<keyword>")` to list files whose
   path carries the concept, `list_dir("<prefix>")` to browse the module tree, and `get_symbol` for a
   known name. The goal is the repo-relative path or paths of the central module.
2. **READ LOCAL.** Open those paths in the local monorepo checkout and read the **real, current**
   code: follow `Build.cs` and imports to trace the dependency graph, inspect the actual data
   structures, and rule out false matches by opening them. This is where thorough, up-to-date
   verification takes place.
3. **FALLBACK (no local repo).** If the repository is not present on the machine, use `read_file_range`/`file_outline`
   to read the KB's indexed snapshot, bearing in mind that it may lag behind the current code.
4. **Answer** with repo-relative paths and the key `file:line` references, drawn from the local files you read.

## Why this split
The KB excels at **speed** and works **without the repo**; the local files excel at **freshness and
completeness**. Locating via the KB and reading locally gives you both. Do not use the KB as a
substitute for reading the local code when it is available: the KB points, and the repository is the truth.

## Anti-patterns
- Treating KB snippets as authoritative or current; they are only a snapshot.
- Issuing a single vague `/ask` and accepting the first summary.
- Answering "where is X" from a "目录规范 / how-to-organize-code" document instead of locating the module itself.

## Worked example — "负责第三方登录的模块代码在哪个目录"
1. `search_code("third party login OAuth SDK")` returns generic or secondary hits, so switch to
   `find_files("login")`, which surfaces `.../SDKWrapper/AuthSDK/Source/Login/LoginManager.cpp`.
2. `list_dir(".../SDKWrapper")` confirms that the module sits among the SDK wrappers.
3. **Open the local files** `LoginManager.cpp` + `Login.Build.cs` to verify the real,
   current dependency graph (which channel SDKs it links) and rule out false matches.
4. Answer with the path and what the local source actually shows.
