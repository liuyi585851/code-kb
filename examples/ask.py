"""Code-KB 最简示例:加载内置的合成语料,然后提一个问题。

在仓库根目录下运行:

    PYTHONPATH=src python examples/ask.py
    # 或者执行 `pip install -e .` 安装后:
    python examples/ask.py
"""

import sys
from pathlib import Path

# 不安装也能直接跑(src 布局)。
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codekb.service import OfflineKbService


def main() -> None:
    service = OfflineKbService(
        fixture_path=str(ROOT / "data" / "fixtures" / "sample_corpus.jsonl"),
        aliases_path=str(ROOT / "data" / "entity_aliases.yaml"),
    )

    question = "What does DEVICE_SEQ mean?"
    answer = service.ask(question, sub_kbs={"testing"})

    print(f"Q: {question}")
    if answer.refused:
        print(f"Refused (cite-or-die): {answer.refusal_reason}")
        return

    print("A:", answer.answer.strip())
    for i, citation in enumerate(answer.citations, start=1):
        print(f"  [{i}] {citation.docid}")


if __name__ == "__main__":
    main()
