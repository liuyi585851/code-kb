"""基于 Code-KB 的 LangGraph ReAct agent 示例。

展示推荐用法:让 agent 借助 Code-KB 快速定位代码
(概念展开 -> 标识符 -> search / find_files / list_dir / get_symbol),
再去读真实文件(本地优先,没有本地副本就用 ``read_file_range`` 兜底),
最后给出带 ``repo/file:line`` 的引用作答。

运行::

    pip install -e '.[agents]'
    # 先起一个 Code-KB 服务(见项目 README),然后:
    export CODEKB_URL=http://localhost:8000
    export OPENAI_API_KEY=...            # 也可换成下方任意已配置的对话模型
    python examples/langgraph_agent.py "where is third-party login handled?"
"""
from __future__ import annotations

import sys

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langgraph.prebuilt import create_react_agent

from langchain_tools import CODEKB_TOOLS  # 同一目录

SYSTEM = (
    "You are a code-navigation agent backed by Code-KB. To answer "
    "'where is X / how is Y implemented', first expand the concept to the likely English "
    "identifiers the source uses, then use find_files / list_dir / code_search / get_symbol to "
    "LOCATE the central module. Read the real file (your local checkout if available, else "
    "read_file_range). Don't answer from generic 'how to organize code' docs. Cite repo/file:line."
)


def main() -> None:
    question = " ".join(sys.argv[1:]) or "Which module handles third-party login?"
    # 换成你已配置好的任意对话模型(OpenAI / Anthropic / 本地皆可)。
    model = init_chat_model(__import__("os").getenv("CODEKB_AGENT_MODEL", "gpt-4o-mini"))
    agent = create_react_agent(model, tools=CODEKB_TOOLS, prompt=SYSTEM)
    result = agent.invoke({"messages": [HumanMessage(content=question)]})
    print(result["messages"][-1].content)


if __name__ == "__main__":
    main()
