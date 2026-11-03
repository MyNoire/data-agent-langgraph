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

**Hit（命中）**：
一次检索匹配到的 Chunk。
_避免_：result、match

**Retrieval Context（检索上下文）**：
由 Hit 展开、去重得到的 Documents，交给 SQL 生成使用。
_避免_：evidence、context（单独使用时）

## 查询

**Schema（结构文本）**：
提供给 SQL 生成的数据库结构文本。
_避免_：用 schema 指代 Catalog

## 评测

**Relevant Set（相关集合）**：
一道评测题回答所需的表和列，取自该题金标准 SQL 引用的对象。它是代理标注：等价的 SQL 可能用到别的表。
_避免_：标签、ground truth（泛指时）

**Table Ranking（表排名）**：
一次检索的 Hit 按 rank 归到所属 Document（关系类 Hit 归到它的源表），去重后得到的有序表列表。
_避免_：用 Hit 的名次代指表的名次

**Table Complete Recall@K（表完整召回率）**：
Relevant Set 中的表全部出现在 Table Ranking 前 K 张表里的题目占比；K 以表计。
_避免_：Table Full Coverage、strict recall（指表时）

**Table Recall@K（表召回率）**：
每道题 Relevant Set 中出现在 Table Ranking 前 K 张表里的表所占比例，再对题目取平均；K 以表计。

**Tables to Cover（全覆盖所需表数）**：
沿 Table Ranking 往下取，直到 Relevant Set 中的表全部出现时取到的表数；始终没有全部出现时为空。

**Column Recall@K（列召回率）**：
每道题 Relevant Set 中的列有列 Chunk 出现在前 K 个 Hit 里的比例，再对题目取平均；K 以 Hit 计。只用于诊断。
