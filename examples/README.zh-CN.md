[English](README.md) · **简体中文**

# 示例

小而自包含的示例,针对内置的合成语料(`data/fixtures/sample_corpus.jsonl`)运行。

## `ask.py`

加载样例语料并提出问题,打印 cite-or-die 答案及其引用。

```bash
PYTHONPATH=src python examples/ask.py
# or, after `pip install -e .`:
python examples/ask.py
```

## `langchain_tools.py` —— LangChain 工具适配器

将 Code-KB 的 HTTP API 封装为 LangChain `@tool`(`code_search`、`find_files`、
`list_dir`、`get_symbol`、`read_file_range`);`CODEKB_TOOLS` 可直接交给任意
LangChain/LangGraph agent。通过 `CODEKB_URL` 指向运行中的服务。

## `langgraph_agent.py` —— 一个 LangGraph ReAct agent

一个 ReAct agent,以预期方式驱动 Code-KB:**先借助 KB 定位**(概念→标识符→
search/find_files/list_dir),再读取真实文件。需要 `[agents]` extra 与运行中的 Code-KB 服务。

```bash
pip install -e '.[agents]'
export CODEKB_URL=http://localhost:8000
export OPENAI_API_KEY=...          # 或设置 CODEKB_AGENT_MODEL 为任意聊天模型
python examples/langgraph_agent.py "第三方登录在哪个模块处理?"
```

> 这两个 agent 示例为演示用途,需要网络访问与 LLM,因此**不**纳入(零网络、零跳过的)测试套件。
