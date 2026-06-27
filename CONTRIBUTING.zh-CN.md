[English](CONTRIBUTING.md) · **简体中文**

# 为 Code-KB 贡献代码

感谢你有意参与贡献！本指南介绍一些基础内容。

## 开发环境搭建

需要 Python ≥ 3.11。

```bash
pip install -e '.[api,storage]'
```

核心库仅依赖 `PyYAML`;`fastapi`/`uvicorn`(`[api]`)、`psycopg`(`[storage]`)和 `anthropic`(`[llm]`)都是可选的附加项。

## 运行测试

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

`data/fixtures/` 下附带了一份合成语料,测试与示例无需额外准备即可运行。请勿向仓库添加任何真实或第三方数据。

## 项目结构

```
src/codekb/      核心库、连接器、FastAPI 应用、CLI、MCP 服务
src/codekb/web/  单页控制台（静态资源）
tests/           单元测试（每个模块/领域一个文件）
data/fixtures/   合成样例语料 + 黄金问题
examples/        可运行的小示例
docs/            设计说明与架构文档
deploy/          示例服务/运行脚本
```

关于 core / connectors / api / web / cli 的分层,参见 `README.md` 的「Architecture」一节。

## 开发规范

- 保持内核仅依赖 `PyYAML` 即可导入;将重量级或可选依赖归入附加项,并按需懒加载导入。
- 任何行为变更都应新增或更新相应测试;保持测试套件零跳过、不依赖网络。
- 优先使用小而自描述、可定位的检索单元(原子)——本项目是一个检索/导航层,而非推理引擎。
- 提交 pull request 前先运行测试套件。

## 反馈问题

请附上 Python 版本、你运行的命令,以及完整的输出。涉及安全问题的报告,请私下联系维护者,而不要公开提交 issue。
