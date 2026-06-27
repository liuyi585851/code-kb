[English](README.md) · **简体中文**

# 设计笔记与参考

关于 Code-KB 构建方式的背景资料。建议先从项目 [README](../README.zh-CN.md) 开始,了解总体概览与快速上手。

## 设计与参考

| 文档 | 涵盖内容 |
|-----|----------------|
| [code-retrieval-design.zh-CN.md](code-retrieval-design.zh-CN.md) | 结构感知的代码切片、自描述的正文内代码头注,以及代码导航原语(search / symbol / find_files / list_dir / read) |
| [embedding-reranker-strategy.zh-CN.md](embedding-reranker-strategy.zh-CN.md) | Embedding 模型 + cross-encoder 重排器策略与取舍 |
| [data-contracts.zh-CN.md](data-contracts.zh-CN.md) | 核心数据结构:Atom、引用/答案、反馈、trace、子知识库 registry |
| [interface-spec.zh-CN.md](interface-spec.zh-CN.md) | 模块边界、Python 接口,以及对外暴露的 HTTP API |
| [schema.sql](schema.sql) | 原子存储及相关表的 Postgres schema |
| [p0-dependencies.zh-CN.md](p0-dependencies.zh-CN.md) | 运行时依赖,以及每个可选附加项引入的内容 |
| [integration-credentials-guide.zh-CN.md](integration-credentials-guide.zh-CN.md) | 配置可选连接器(Wiki / issue tracker / Git / IM) |

## 配置文件（运行时加载）

| 文件 | 使用方 |
|------|---------|
| [kb-registry.draft.yaml](kb-registry.draft.yaml) | 子知识库 registry(`CODEKB_REGISTRY`)——声明 `code` / `docs` / … 等子知识库 |
| [governance-policy.draft.yaml](governance-policy.draft.yaml) | 治理阈值(过期、属主、缺口策略) |
| [diagnose-webhook-mapping.draft.yaml](diagnose-webhook-mapping.draft.yaml) | 将外部 webhook payload(CI / MR / issue-tracker / crash / generic)映射为诊断请求 |
| [diagnose-webhook-samples.draft.yaml](diagnose-webhook-samples.draft.yaml) | 上述映射所用的样例 webhook payload |

`*.draft.yaml` 是可用的默认值——复制并按你的部署进行调整。
