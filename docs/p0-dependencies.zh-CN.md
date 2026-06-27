[English](p0-dependencies.md) · **简体中文**

# P0 Dependencies

更新时间:2026-06-11

本文件记录 P1 只读 RAG MVP 的模型、存储、服务和权限依赖。P0 结束前,需要逐项确认其可用性或给出替代方案。

## 模型依赖

| 能力 | 首选 | 用途 | P0 确认项 | 备选 |
|---|---|---|---|---|
| Query rewrite | 低成本改写模型(OpenAI 兼容) | 生成改写 query、抽取实体 | API endpoint、鉴权、限流、成本 | 先关闭 query rewrite,仅使用原始 query |
| Embedding | BGE-large-zh-v1.5 | 中文语义向量 | 是否已有内网服务;向量维度;批量 QPS | bge-m3 或现有 embedding 服务 |
| Reranker | BGE Reranker v2-m3 | top-20 重排到 top-4 | 是否已有内网服务;p95 延迟 | 先采用 RRF 分数 + BM25 boost |
| Generator | OpenAI 兼容模型 / 团队模型代理 | 生成带引用的答案 | 模型代理、service token、最大上下文、审计要求 | 先输出 citation pack,不生成长答案 |
| AI self eval | 与 Generator 共用 | P2 自评分 | P1 无需强依赖 | 在 P2 接入 |

## 存储依赖

| 组件 | 首期用途 | P1 必需 | P0 确认项 | 降级方案 |
|---|---|---|---|---|
| Postgres | Atom 元数据、source_documents、trace | 是 | 实例、库名、账号、迁移方式 | SQLite 仅用于本地 PoC,不能作为 P1 验收依据 |
| Qdrant | Dense vector index | 是 | 部署地址、collection 命名、payload filter | 本地 Qdrant 容器,仅限开发环境 |
| Elasticsearch | Sparse BM25 | 是 | 索引权限、IK 分词、字段 mapping | 以 OpenSearch 或 Postgres FTS 临时替代 |
| Redis | L0 cache | 否 | P1 可暂缓 | 暂不使用缓存,在 P2/P6 阶段实现 |
| S3/object storage | 原始附件和图片归档 | 否 | P1 仅保留附件 URL | 暂不归档附件 |
| Neo4j | KG 1-hop | 否 | P1 暂缓 | entity_alias + BM25 boost |

## 数据源依赖

| 数据源 | P1 用途 | 当前状态 | P0 待确认 |
|---|---|---|---|
| Wiki MCP `getDocument` | 深度抓取试点文档正文 | 已验证 DOC/MD 可读;TXDOC 可读但噪声较高 | 批量读取限流、TXDOC 清洗策略 |
| Wiki MCP `metadata` | 文档 owner、更新时间、ACL metadata | 已验证 | 字段稳定性、跨空间 ACL 表达 |
| Wiki MCP `getSpacePageTree` | 枚举复盘目录 | 已验证 `1000000004` | 大目录分页/深度限制 |
| Wiki 写接口 | P3 出站同步 | 已确认支持评论、追加、保存、复制、移动;未发现新建和标签接口 | 新建文档和标签能力,或模板复制降级方案 |
| Git | P4/P5 owner 和 MR 候选卡 | P1 不依赖 | 项目 ID、webhook、commit/MR API 权限 |
| issue tracker | P4 gap ticket/P5 诊断 | P1 不依赖 | workspace、bug/story 创建权限 |
| IM | P3 Catcher Bot | P1 不依赖 | Bot 回调、卡片按钮、主动消息权限 |

## 运行环境依赖

| 项 | P1 要求 |
|---|---|
| Python | 3.11+ |
| API 框架 | FastAPI + Uvicorn |
| 任务队列 | 首版可使用轻量 worker/cron;P2 之后再接入 Celery/Arq |
| 配置 | YAML registry + env vars |
| 部署 | 先部署内网单实例;索引 worker 与 API 可拆分为独立进程 |
| 观测 | structured log + trace_id;P2 阶段再接入 dashboard |

## 权限与安全

1. `/ask` 仅允许使用 service-account token 调用。
2. 必须在检索前或返回结果前执行 ACL 过滤,不能仅依赖 UI 隐藏。
3. trace 中的 `user_id` 以 hash 形式存储,不记录明文的个人敏感信息。
4. source doc 的 `can_edit` 仅作为写入能力的参考,并不意味着可以随意改写其内容。
5. P1 不会自动写入 Wiki,以避免权限与知识污染风险。

## P0 决策

| 决策 | 结论 |
|---|---|
| 是否将 Redis 纳入 P1 | 不纳入,P1 优先保证准确性与可追溯性 |
| 是否将 Neo4j 纳入 P1 | 不纳入,以 metadata/entity_alias 作为兜底 |
| 是否将 TXDOC 纳入 P1 深度抓取 | 暂不作为 P1 验收项,优先处理 metadata |
| 是否将 OCR 纳入 P1 | 不纳入,截图仅保留附件占位与 source link |
| 跨空间文档是否可进入问答 | 可以,但必须保留 ACL 与 source URL |
| 在没有模型服务时是否可以推进 | 可以;先完成 ingest/retrieval/eval,generator 延后接入 |
