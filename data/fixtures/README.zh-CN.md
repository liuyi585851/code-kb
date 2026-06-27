[English](README.md) · **简体中文**

# 样例 fixtures

一份极小的**合成**样例语料,随仓库附带,使测试、示例和 CLI 开箱即用。它不含任何真实或第三方数据——请用你自己的语料替换它,才能将 Code-KB 用于真实数据。

- `sample_corpus.jsonl` —— 一份小型 JSONL 语料,供内存原子存储、BM25-lite 检索器以及黄金 hit@4 评估器使用。
- `golden_questions.md` —— 针对该语料的一小组示例黄金问题(markdown 表格格式),用于评估功能。

## 语料结构（`sample_corpus.jsonl`）

每行一个 JSON 对象:

```json
{"docid": "1000001", "sub_kb_id": "testing", "title": "Example doc", "content_type": "DOC", "url": "https://wiki.example.com/p/1000001", "metadata": {"system": "example", "owner": "alice"}, "body": "## Section\n\nExample body text used for retrieval."}
```

- `docid` —— 唯一 id。黄金问题可以引用数字 id(7 位及以上)。
- `sub_kb_id` —— 已配置的子知识库之一(例如 `code`、`docs`、`release`、`incident`、`testing`);检索范围按子知识库分别限定。
- `body` —— markdown;标题会成为章节锚点,并在引用中使用。

## 黄金问题结构（`golden_questions.md`）

一个 markdown 表格,`ID | question | expected sources | focus`:

```
| TST-001 | What does DEVICE_SEQ mean? | `1000001` | parameter meaning |
```

## 让 Code-KB 指向你自己的数据

```bash
CODEKB_FIXTURES=path/to/your_corpus.jsonl   # or pass --fixtures on the CLI
```

`.yaml`/`.yml` 源路径会作为 wiki 风格的 manifest 加载;其他任何路径则作为 JSONL 加载。
