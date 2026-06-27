[English](embedding-reranker-strategy.md) · **简体中文**

# Embedding and Reranker Strategy

更新时间:2026-06-11

## 结论

在 GPU 资源有限、只能短时使用的前提下:

1. **P1 不强依赖 embedding 和 reranker**。先基于 BM25、关键词、实体别名与 metadata boost,完成只读 RAG MVP 与黄金集评估的搭建与验证。
2. **Embedding 在中期是必要的**,否则语义问题、同义表达与长复盘案例检索都会受限。但 embedding 可以离线批量计算,无需常驻 GPU。
3. **Reranker 暂不建议自部署常驻 GPU**。P1/P2 阶段先采用 RRF + BM25 + metadata/composite score;只有当评估证明 hit@4 或正确率停滞不前时,再通过短时 GPU 或内部服务接入 reranker。
4. **优先申请 CPU 服务器,而非长期 GPU**。GPU 仅申请短时窗口,用于批量构建向量、模型评测或周期性重建。

## 为什么仍然需要 embedding

纯 BM25 对以下问题有效:

- 参数名、错误码、接口名、文档标题
- `DEVICE_SEQ`、`sourceFileName`、`Channel` 这类精确关键词
- 发布 SOP、UDT 参数表、明确标题检索

但纯 BM25 在以下问题上明显处于劣势:

- 用户的表述与文档的表述不一致,例如「进局卡住」与「loading 卡死」
- 复盘类问题,例如「以前类似的线上问题是如何处理的」
- 对会议纪要或长文档进行总结后形成的抽象知识
- 跨多个文档、多个段落的语义相似召回

因此,策略并非「完全不使用 embedding」,而是:

> P1 阶段不让 embedding 阻塞启动;从 P2 开始引入离线 embedding,以提升召回质量。

## 为什么 reranker 可以后置

Reranker 的主要收益体现在 top-20 到 top-4 的精排阶段,可以减少「已召回但排序偏后」的问题。其代价在于:

- 每一次在线 query 都需要执行,带来的延迟与算力压力更为直接。
- GPU 常驻并不经济。
- CPU reranker 可用,但 p95 延迟可能不稳定。

P1 的替代方案:

1. BM25 分数。
2. source freshness/owner/status boost。
3. exact entity boost:参数名、错误码、docid、玩法名。
4. sub_kb route boost。
5. atom composite score。

只有当黄金集出现以下问题时,才接入 reranker:

- hit@20 达标但 hit@4 不达标。
- 同一 query 频繁召回多个相近文档,且排序不稳定。
- 事故复盘类问题引用了错误的来源。

## GPU 使用方式

### 不建议

```text
常驻 GPU 在线服务：
- embedding 在线推理
- reranker 在线推理
```

原因:资源有限,常驻 GPU 成本较高;P1/P2 阶段的查询量尚不足以证明其必要性。

### 建议

```text
短时 GPU 批处理：
- 初次批量 embedding
- 大版本模型/索引重建
- 每周或每日增量 embedding 批处理
- reranker A/B 离线评测
```

产物的落地位置:

- embedding 向量写入 Qdrant
- atom metadata 写入 Postgres
- sparse 文本写入 ES/OpenSearch
- 不依赖常驻在线的 GPU

## 阶段策略

| 阶段 | Embedding | Reranker | GPU |
|---|---|---|---|
| P1 | 不作为硬依赖;先 BM25-lite/ES BM25 | 不接 | 不申请长期 GPU |
| P1.5 | 短时 GPU 批量生成 P1 文档向量 | 不接或离线评测 | 申请 2-4 小时 GPU 窗口 |
| P2 | 增量 embedding 进入常规流程 | 若 hit@4 不足再接内部服务/CPU 小模型 | 每日/每周短时 GPU,或使用内部 embedding 服务 |
| P3 | embedding 必备 | reranker 视评估结果启用 | 不常驻;优先内部服务 |
| P4+ | embedding 必备 | 建议启用,但可用内部模型服务 | 若自部署生产再考虑 GPU HA |

## P1 具体执行

P1 阶段首先完成:

1. ES/OpenSearch BM25。
2. entity alias 字典。
3. exact match boost。
4. doc freshness/owner/status boost。
5. golden hit@4 evaluator。

P1 通过条件:

- 如果 BM25 baseline 的 hit@4 >= 0.75,则继续推进 P1,暂不接入 embedding。
- 如果 BM25 baseline 的 hit@4 < 0.75,但 hit@20 明显更高,则优先增加 rerank 与 boost 规则。
- 如果 BM25 baseline 的 hit@20 同样不足,再引入 embedding。

## 短时 GPU 申请建议

如果确实需要申请 GPU,不应申请长期占用,而应申请「批处理窗口」:

```text
GPU 短时任务资源
- 16C CPU
- 64GB 内存
- 500GB SSD
- 1 张 16GB+ GPU，优先 24GB
- 使用方式：按需 2-4 小时窗口
- 用途：批量生成/重建 embedding，离线 reranker 评测
```

P1/P2 预计使用频率:

| 场景 | 频率 | 时长估计 |
|---|---:|---:|
| 初次 P1 试点向量构建 | 1 次 | < 1 小时 |
| P2 扩到 50k-200k atoms | 1-2 次 | 2-6 小时 |
| 每周增量重建 | 每周 | 1-3 小时 |
| reranker 离线评测 | 按需 | 1-2 小时 |

## 推荐申请口径

服务器常驻资源:

```text
必须：P1 All-in-one CPU 服务器
- 16C / 64GB / 1TB SSD
- 不含 GPU
```

GPU 资源:

```text
可选：短时 GPU 批处理资源
- 不要求常驻
- 每次使用 2-4 小时
- 用于 embedding 批处理和 reranker 离线评测
```

## 最终建议

当前不要为了 P1 申请长期 GPU。请先申请 CPU 服务器,把 BM25 baseline、数据清洗、trace 与黄金集评估搭建并运行起来。

当黄金集评估显示纯 BM25 无法达到 hit@4 时,再申请短时 GPU 生成 embedding。Reranker 则等到 hit@20 达标但 top-4 排序不稳定时再行接入。
