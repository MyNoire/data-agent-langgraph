# QueryPilot

面向业务分析的自然语言数据助手：根据用户问题检索数据库结构与业务知识，生成并只读执行 SQL，给出可追溯的回答。

## 知识准备

**Catalog（结构目录）**：
某个数据库在某一时刻的表、列和外键结构，从数据库中抽取得到。
_避免_：catalog snapshot、结构快照

**Curated Catalog（整理后目录）**：
附加了字段说明、并应用了已批准修正的 Catalog；是构建 Corpus 的输入。
_避免_：bundle、修正包、批准包

**Schema Version（结构版本）**：
由 Catalog 内容决定的版本标识；结构有任何变化就是新的版本。

**Data Semantics（数据语义）**：
数据本身稳定的含义，例如某列的编码 `'+'` 表示致癌。可以写进 Corpus。
_避免_：业务知识（泛指时）

**Data Convention（数据约定）**：
查询数据本身观察到的表的存储性质，例如每条连接正反各存一行。它是 Data Semantics 的一种，来源标为观察所得，与只根据结构和字段说明推断出的语义分开；经用户审阅后才写进 Corpus。
_避免_：存储规则、数据规则

**Value Samples（取值样例）**：
从数据库里取出的一列的真实取值，原样保留大小写和格式。取值很少的文本列给出全部取值，其余列只给几个样例来展示格式；隐私列不给。
_避免_：示例数据、枚举值（泛指时）

**Business Rule（业务口径）**：
由组织或用户定义、可能随时间改变的判断标准，例如"月收入高于多少算高收入"。不写进 Corpus，应来自用户或组织文档，缺失时向用户澄清。
_避免_：业务规则知识、阈值知识

**Evidence（BIRD 提示）**：
BIRD 每道题附带的人工提示。只在编写 Data Semantics 时作参考，不进入在线检索或提示词。
_避免_：用 evidence 指检索到的知识

**Corpus（知识语料）**：
由一份 Curated Catalog 按某种构建方式生成、经校验后不再改动的知识集合，是检索的对象。同一份 Curated Catalog 用不同构建方式会得到不同的 Corpus。
_避免_：snapshot、知识快照、知识库快照

**Build Profile（构建方案）**：
把 Curated Catalog 变成 Corpus 的一组规则，即知识的结构模式；比较不同 Build Profile 是评测的一部分。
_避免_：知识结构模式、渲染配置

**Document（知识文档）**：
一张表的完整知识。
_避免_：表文档、page

**Chunk（知识块）**：
检索时被匹配的最小知识单元；每个 Chunk 属于一个 Document。
_避免_：片段、segment、atom

## 检索

**Knowledge Scope（知识范围）**：
一次检索所针对的那份 Corpus，及其对应的数据库和 Schema Version。它不代表授权。
_避免_：单独说 scope

**Access Grant（访问授权）**：
一次请求被允许访问哪些数据库。与 Knowledge Scope 是不同的概念。
_避免_：授权范围、scope

**Embedding Profile（嵌入方案）**：
把 Chunk 和查询变成向量的一组固定选择：用哪个模型、多少维、Chunk 取哪段文本嵌入、查询前加什么指令。按它算出的向量是 Corpus 的派生索引，不属于 Corpus；同一份 Corpus 可以同时有多个 Embedding Profile 的向量。
_避免_：向量快照、向量库

**Rerank Profile（重排方案）**：
对一次检索的候选做重排时的一组固定选择：用哪个 Reranker 模型、候选取哪段文本、带什么任务指令。重排只改变候选的先后顺序，不会找出候选之外的 Chunk。
_避免_：精排配置、rerank config

**Hit（命中）**：
一次检索匹配到的 Chunk。
_避免_：result、match

**Retrieval Context（检索上下文）**：
把 Hit 按所属的表归拢得到的内容：每张被命中的表带上这张表的表级知识，再加上命中的列和关系。它渲染成 Schema 后交给 SQL 生成；没被命中的列只出现列名，不带说明。
_避免_：evidence、context（单独使用时）

## 查询

**Schema（结构文本）**：
提供给 SQL 生成的数据库结构文本。
_避免_：用 schema 指代 Catalog

**Context Source（上下文来源）**：
SQL 生成所用的 Schema 取自哪里：裸表列名、全部建表语句、全量知识，或检索得到的 Retrieval Context。对比不同的 Context Source 是 SQL 评测的一部分。
_避免_：模式、上下文模式

## 评测

**Strict Execution Match（严格执行匹配）**：
预测 SQL 与金标准 SQL 在相同执行上限下的结果逐行相同：忽略行的顺序，保留重复行。受行数上限影响，不等于官方 BIRD EX。
_避免_：EX、准确率（单独使用时）

**Content Match（内容匹配）**：
预测 SQL 的结果包含了金标准结果的全部内容，只在形式上不同：可以多出列、列的顺序不同、有重复行、数字写成字符串、几列用空格拼成一列。数值、行的集合有任何不同都不算。它是 SQL 评测结论的主指标，报告时总与严格执行匹配并列。
_避免_：宽松 EX、soft match、语义正确率

**Relevant Set（相关集合）**：
一道评测题回答所需的表和列，取自该题金标准 SQL 引用的对象。它是代理标注：等价的 SQL 可能用到别的表。
_避免_：标签、ground truth（泛指时）

**首次命中名次（First-Hit Rank）**：
一张表第一次出现的 Hit rank。每个 Hit 归到所属 Document 的表（关系类 Hit 归到它的源表）；关系的目标表不计入：取到目标表要靠检索之后的外键扩展，不属于检索本身。列的首次命中名次只看这一列自己的列 Chunk。报告字段为 `table_ranks` / `column_ranks`。
_避免_：Table Ranking、去重后的表名次

评测里的 K 都以 Hit 计，和检索参数 top_k 是同一个单位：下游拿到的就是前 top_k 个 Hit。

**Table Complete Recall@K（表完整召回率）**：
Relevant Set 中的表都在前 K 个 Hit 里命中的题目占比。
_避免_：Table Full Coverage、strict recall（指表时）

**Table Recall@K（表召回率）**：
每道题 Relevant Set 中在前 K 个 Hit 里命中的表所占比例，再对题目取平均。

**Hits to Cover（全覆盖所需 Hit 数）**：
各相关表首次命中名次中的最大值，也就是这道题至少需要的 top_k；检索返回的 Hit 里始终没有全部命中时为空。
_避免_：Tables to Cover

**Column Recall@K（列召回率）**：
每道题 Relevant Set 中的列有列 Chunk 出现在前 K 个 Hit 里的比例，再对题目取平均；没有相关列的题不参与平均，另报题数。只用于诊断。
