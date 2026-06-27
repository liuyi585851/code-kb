<div align="center">

<h1>🧭 Code-KB</h1>

<p><strong>面向自主编码 agent 的「代码 + 文档」检索与导航层:精确定位、强制引用或拒答、内容始终最新、可追溯。</strong></p>

<p>Code-KB 将源码与文档切分并索引为小而自描述、可定位的「原子(atom)」,通过混合检索在单次调用中返回相对仓库根的精确位置。它遵循 cite-or-die(无引用则拒答)策略:每个答案都必须由检索到的真实源码区间支撑,否则拒绝回答并记录该缺口。它是检索与导航层,负责快速定位到 <code>file:line</code>,实际读取由 agent 在自身本地工作副本中完成,因此返回的始终是最新内容,而非过时镜像;更复杂的推理则交给更强的客户端模型或人。</p>

<p>
  <a href="#-快速上手">快速上手</a> ·
  <a href="#-面向-ai-agent">面向 AI agent</a> ·
  <a href="#-架构">架构</a> ·
  <a href="#-配置">配置</a> ·
  <a href="CONTRIBUTING.zh-CN.md">参与贡献</a>
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

<sub><a href="README.md">English</a> · <strong>简体中文</strong></sub>

</div>

---

> **TL;DR** — Code-KB 是面向 AI 编码 agent 的代码与文档检索、导航层。它将语料索引为可定位的「原子」,通过混合检索返回相对仓库根的精确位置;agent 在自身本地工作副本中读取当前文件,因此结果始终最新。它是索引层,职责是快速、可引用的定位,而非推理引擎。

## 📚 目录

- [为什么用 Code-KB](#-为什么用-code-kb)
- [亮点](#-亮点)
- [面向 AI agent](#-面向-ai-agent)
- [架构](#-架构)
- [仓库结构](#-仓库结构)
- [快速上手](#-快速上手)
- [测试](#-测试)
- [配置](#-配置)
- [集成](#-集成)
- [参与贡献](#-参与贡献)
- [许可证](#-许可证)

## 🤔 为什么用 Code-KB

编码 agent 常在定位代码上反复试探,并在缺乏依据时给出臆测的答案。Code-KB 针对这两个环节分别提供保障。

- **检索而非猜测**:语义检索与结构化检索结合,单次调用即返回精确的、相对仓库根的位置。
- **引用或拒答**:答案强制携带引用;在缺少支撑片段时,服务拒绝作答并记录缺口,而不产生臆造内容。
- **内容始终最新**:返回的是位置,agent 读取的是本地当前文件,不会基于过时副本进行推理。
- **依赖精简、易于运行**:离线的检索/评估内核与完整测试套件仅依赖 PyYAML 即可运行;FastAPI、Postgres、Qdrant 与 LLM SDK 均为可选组件。

## ✨ 亮点

| 能力 | 说明 |
| --- | --- |
| 🔀 混合检索 | BM25-lite 稀疏检索与稠密向量检索(Qdrant)结合,经 RRF(倒数排名融合)合并,并可选接入 cross-encoder 重排器。 |
| 📌 cite-or-die 的 `/ask` | 强制携带引用的抽取式答案;可选的生成式模式在未配置 LLM 时自动回退到抽取式。 |
| 🧭 代码导航 | 兼具语义搜索与结构化发现的 MCP + HTTP 工具:`codekb_search_code`、`codekb_get_symbol`、`codekb_find_files`(按路径子串查找文件)、`codekb_list_dir`(列出指定目录下一层的子目录与文件)、`codekb_read_file_range`、`codekb_file_outline`。 |
| 🔎 查询展开 | 可选的 LLM 步骤,在检索前将自然语言(或非英文)提问改写为源码实际使用的标识符,弥合自然语言与源码标识符之间的鸿沟。 |
| 🤖 面向 agent | MCP 原生;内置 `code-kb` skill(一套「定位 → 读取 → 校验」的检索范式);并提供 LangChain/LangGraph 适配器与可直接运行的 ReAct agent 示例。 |
| 🚀 FastAPI 服务 | 构建于 Postgres 原子存储与 Qdrant 向量存储之上,并附带按 hash 路由的单页控制台。 |
| 🛡️ 治理与反馈 | 黄金问题评估、质量门禁、候选蒸馏、过期/属主治理报告,以及带红线(密钥/PII/内部域名)过滤的发布通道。 |
| 📦 纯标准库内核 | 离线内核与完整测试套件仅依赖 PyYAML;重型依赖均为可选。 |

每个检索片段均带有一行自描述的头部注释,可据此追溯来源。

```text
« <repo> · <path>:L<a>-<b> · <lang> · <symbol> »
```

## 🤖 面向 AI agent

- **agent 基础设施**:Code-KB 是一个快速、可引用的「代码 + 文档」索引,面向自主编码 agent,而非面向人工检索的搜索框。
- **MCP 原生**:检索与代码导航均通过 Model Context Protocol 暴露;将服务接入任意 MCP 客户端(如 Claude Code、各类 IDE agent)后,工具即自动注册并可用。
- **内置 agentic 检索范式**:内置 `code-kb` skill(参见 [`skills/code-kb/SKILL.md`](skills/code-kb/SKILL.md)),固化「定位 → 浏览 → 读取 → 校验」闭环,并以 cite-or-die 作为约束,使 agent 直接命中精确的 `file:line`。
- **快速定位、就地读取**:Code-KB 返回相对路径,agent 在自身本地工作副本中读取当前文件。它是索引层,而非会逐渐过时的副本。
- **框架无关**:每个工具都是普通的 JSON HTTP 端点,可封装为 LangChain 工具、LangGraph 节点,或任意 function-calling agent 的工具。

> **技术栈**:RAG(混合检索 + RRF + 重排)· 工具调用(MCP)· prompt 工程(cite-or-die + 查询展开)· 上下文编排(预算化、自描述引用)· agent 编排(LangGraph ReAct + 多 agent 定位/读取)· 可选的模型服务化(可插拔的 embedder / reranker / LLM)。

### LangChain / LangGraph

开箱即用的适配器位于 [`examples/`](examples/) 目录(`pip install -e '.[agents]'`)。

```python
from langgraph.prebuilt import create_react_agent
from langchain.chat_models import init_chat_model
from examples.langchain_tools import CODEKB_TOOLS   # code_search, find_files, list_dir, get_symbol, read_file_range

agent = create_react_agent(init_chat_model("gpt-4o-mini"), tools=CODEKB_TOOLS)
agent.invoke({"messages": [("user", "where is third-party login handled?")]})
# agent 先用 Code-KB 定位,再读取真实文件
```

运行完整示例:`python examples/langgraph_agent.py "…"`。

> **多 agent 场景**:在多 agent 编排中,Code-KB 作为共享的「代码索引层」:定位 agent 经由它找到模块,读取/实现 agent 打开本地的当前文件,从而兼顾快速定位与内容的实时性与权威性。

## 🏗️ 架构

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

- **原子(Atoms)**:小而自描述的单元(正文 + 上下文前缀 + 来源锚点),因此检索到的片段总能追溯至其来源。
- **子知识库(Sub-KBs)**:对语料进行分区(例如 `code`、`docs`、`release`、`incident`、`testing`);检索按子知识库分别限定范围。
- **Trace(链路追踪)**:每个答案都会将检索命中、引用与拒答原因写入一份 JSONL 链路日志。

## 📂 仓库结构

```text
src/codekb/          核心库 + 服务 + 连接器 + CLI
  ├─ core            chunker、code_chunker、retrieval、store、candidate、
  │                  citation、answer、evaluator、service(仅依赖 PyYAML)
  ├─ connectors      wiki*、ticket_client(Git)、im_*、qdrant_*、
  │                  postgres、local_index(可选,按需懒加载)
  ├─ api             api.py(FastAPI 应用)+ *_page.py 服务端渲染页面
  ├─ web/            单页控制台(index.html、app.js、app.css)
  └─ cli / mcp       cli.py、__main__.py、mcp_server.py
tests/               单元测试
data/fixtures/       合成样例语料 + 黄金问题
examples/            可运行的小示例
docs/                设计说明与规范
deploy/              示例运行脚本
```

## 🚀 快速上手

> **需要 Python ≥ 3.11。**

```bash
# 1. 配置环境变量
cp .env.example .env        # 根据需要修改 .env 文件

# 2. 根据需要安装相应组件:
pip install -e .                  # 仅安装核心服务 (PyYAML)
pip install -e '.[api,storage]'   # + FastAPI/uvicorn + Postgres driver
# pip install -e '.[llm]'         # + 可选的生成式答案 SDK

# 3. 运行 API 服务和控制台
python -m uvicorn codekb.api:create_app --factory --host 0.0.0.0 --port 8000
```

默认情况下,服务绑定到本地的 Postgres / Qdrant(参见 [`.env.example`](.env.example));离线/抽取式路径无需上述组件即可运行。

### 示例

`data/fixtures/` 目录提供了一份合成样例语料。

```bash
PYTHONPATH=src python examples/ask.py
```

该示例加载 `data/fixtures/sample_corpus.jsonl`,提出一个问题并打印带引用的答案。`codekb` CLI(经 `pip install -e .` 安装)提供相同的流程,参见 `codekb --help`。语料结构及如何将 Code-KB 指向自有数据,参见 [`data/fixtures/README.zh-CN.md`](data/fixtures/README.zh-CN.md)。

## 🧪 测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

整个测试套件不依赖网络,仅需安装 PyYAML 即可在零跳过(zero skips)下完成。

## ⚙️ 配置

所有运行时配置均为以 `CODEKB_` 为前缀的环境变量;完整清单参见 [`.env.example`](.env.example)(host/port、Postgres DSN、Qdrant URL、检索模式、答案模式、LLM 端点、鉴权 token,以及各类集成设置)。

## 🔌 集成

Code-KB 可与外部系统对接,作为 ingest/publish 的目标:Wiki(文档)、issue tracker(工单)、Git(代码)与 IM(鉴权与通知)。上述集成均为可选,通过以 `CODEKB_` 为前缀的配置启用。

## 🤝 参与贡献

参见 [`CONTRIBUTING.zh-CN.md`](CONTRIBUTING.zh-CN.md)。请严格遵守以下三条不可妥协的底线:内核仅依赖 PyYAML 即可导入;测试套件不依赖网络且零跳过;请勿向仓库提交任何真实数据或第三方数据。

## 📄 许可证

[MIT](LICENSE) © Code-KB contributors.
