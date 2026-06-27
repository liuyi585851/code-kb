[English](code-retrieval-design.md) · **简体中文**

# 代码检索设计（Code-KB）

> 范围澄清(关键):**本项目只构建知识库侧,以及客户端的 harness、skill 与 MCP。真正负责分析与解决问题的是客户端强模型(如 LLM agent)。** 因此,KB **不是代码理解引擎,而是检索与导航服务**。成功标准只有两条:
> 1. 返回**正确**的参考(文档、知识原子或代码片段)——即召回与精度;
> 2. 返回的代码片段必须**能被客户端 AI 直接看懂**——自描述、不碎片化、可定位、可续取。
>
> 跨文件理解与调用图多跳推理**由客户端负责**(客户端会再次调用我们的 MCP 工具,自行完成多跳),不属于本服务的职责范围。

## 1. 架构：检索/导航服务（非理解引擎）

- **文档(.md/说明文本)→ 现有向量 RAG**(散文文本,正是 RAG 擅长的),子库 `docs`。
- **代码 → 结构感知、自描述的代码原子** + **导航 MCP 工具**,子库 `code`。
- 客户端拿到「正确、可读懂、可续取」的片段后,自行推理并沿引用继续检索。

## 2. 代码原子设计（已落地，零 schema 迁移）

每个代码原子都是**一个完整语义单元**(整函数或整类;仅超大函数才会附带重叠分片),并自带来源信息:

- **正文头注**(写入 `text`,随片段一同流转至客户端):
  `« 代码大仓 · <repo> · <path>:L<起>-<止> · <语言> · <符号> »`
- **位置承载于现有字段**(无需新增列):
  - `source_docid = <repo>/<repo相对路径>`(如 `AIKnowledge/Source/Weapon/Weapon.lua`)
  - `source_anchor = <source_docid>#L<起>-<止>`
  - `section_path = (repo, 目录…, 符号)`
- `code_location.parse_code_location()` 据此还原出 `repo/file_path/start_line/end_line/language/qualified_symbol`;`CitationPack` 将这些字段作为**可选字段**加入,命中代码时,`/ask` 的引用 JSON 会输出 `file_path/start_line/end_line/language/repo_id/symbol`,供客户端定位、跳转或续取。文档原子不受影响(没有 `#L`,解析返回 None)。

实现文件:
- `src/codekb/code_location.py` — 解析器(CodeLocation)
- `src/codekb/code_chunker.py` — 结构感知切片 `chunk_code()` + 遍历入库的源码 `walk_repo()`
- `src/codekb/models.py` — `CitationPack` 新增可选代码字段
- `src/codekb/citation.py`、`api.py` — 填充并暴露代码位置

切片策略(`code_chunker`):按定义边界切分(py: def/class;lua: function;md: 标题;c-family: 类型声明与函数签名,采取保守策略以免误切到函数内部);将小段打包至 ~110 行的预算上限;超大函数按 ~110 行窗口、12 行重叠进行分片,并将所属符号回填到每一片;遍历时剪枝 vendored、build、cache 目录(但不剪除 `Content`,资产则通过扩展名与二进制跳过来过滤),并跳过二进制、压缩或超大文件。

## 3. 检索流程（中文 NL 问题）

1. (可选)查询改写:deepseek 生成候选英文符号,**与原文取并集而非替换**。
2. 混合检索:dense(语义)+ **标识符感知 BM25**(精确符号、路径或错误码)→ RRF。
3. reranker 精排 → top-k。
4. 向客户端返回**自描述片段 + file:line 引用**;客户端按需调用导航工具续取。

## 4. 导航 MCP 工具（计划 P3，把多跳交还客户端）

- `search_code(query, sub_kbs?)` → 命中片段(含头注 + file:line)
- `get_symbol(name)` → 某符号的定义片段
- `read_file_range(path, L起-止)` / `get_file_outline(path)` → 按需补全上下文
- (可选)`find_references(symbol)`

这套工具十分轻量(只需符号与文件索引即可支撑),将「跨文件关联」交给最适合承担该任务的强客户端,从而避免在调用图或 PageRank 上耗费我们的工程投入。

## 5. 关键选型（依据见 code-retrieval 调研工作流）

- **Reranker**:网关 `qwen3-reranker-8b`(MTEB-Code 41→81,GPU 托管、无本地 CPU 开销、无需重建)——单项精度 ROI 最高;**仅提升精度,不增加召回**。
- **Sparse**:标识符感知 BM25(拆分 camelCase、snake_case 与路径),在精确符号上可证明胜过任何 embedder,且 CPU 开销近乎为零。
- **Embedding**:推迟到最后一步,并作为**证据闸门**;先尝试证伪 BGE-large-zh(经头注富化后已接近同一模态),在迁移到 qwen3 之前,先修复 `embedding_remote.py`(其 instruction 与 dimensions 处理);**禁止跨向量空间回退**。
- **Chunking**:结构感知,辅以头注富化;拒绝 naive 行切分与纯函数切分。

## 6. 部署现实

- **仓不在服务器**:采用离线索引与在线服务分离的双平面设计——切片与嵌入在仓库可见的环境中运行,查询时则只有一条简短的问题;或者,当客户端位于仓库内部时,服务主要返回 file:line 指针 + 跨仓库与 curated 原子。
- **纯 CPU**:embedding 与 reranking 上移至网关;CPU 只负责 ANN 与 RRF。
- **保鲜**:增量索引(以内容哈希为键,仅对发生变更的文件重新切片与嵌入)。
- **Qdrant**:影子集合 + alias 原子切换;绝不原地 recreate 生产库。

## 7. 分期

- **P0**(待做):RepoCrawler(排除规则)+ 增量索引 + 评测(中文 NL → 正确片段命中 gold)。
- **P0.5 ✅ 已落地**:代码位置随原子承载(零 schema 迁移)+ CitationPack 代码字段 + /ask 暴露 file:line。
- **P1 ✅ 已落地**:结构感知 + 头注切片管线(`code_chunker`)。
- **P2**:标识符 BM25 + 网关 reranker(把排序做对,是性价比最高的质量提升)。
- **P3**:导航 MCP 工具接入现有 MCP server + skill。
- **P4(证据闸门,可选)**:embedder 正面对比(BGE-zh vs qwen3)。

## 8. 明确拒绝（这些是客户端的活或低 ROI）

我们这侧**不做**以下事项:跨文件理解与调用图推理、符号图 PageRank 中心度、GraphExpander 预物化邻居、逐块生成的 LLM 中文摘要、Neo4j、SCIP(没有 Lua 索引器)、CPU 自托管代码 embedder、naive 行切分。
