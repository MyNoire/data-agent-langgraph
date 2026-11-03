# 关键词检索评测

Status: ready-for-agent

## 问题

用户（项目开发者，也是学习者）已经完成了 BM25 关键词检索 `retrieve_knowledge`，但还不知道它对真实问题有多大用：问题里提到的表和列，能不能出现在前几个 Hit 里。目前只有手写的 L01–L10 冒烟样例，而且只有一个 toxicology Corpus（20 个 Chunk）。这个规模下 K=5 时随机排序也能碰上不少，说明不了质量。toxicology 的内容是化学分子，初学者不好理解，也不好分析失败原因。

`evaluation/retrieval` 目前只是占位，没有题集、计分规则，也没有报告。

## 解决方案

1. 新增一个贴近日常业务的库 **student_club**（学生社团：会员、活动、签到、预算、报销、收入），按 toxicology 的流程构建 Corpus 并导入知识库。
2. 用 BIRD mini-dev 中 toxicology（40 题）和 student_club（48 题）的全部题目组成**检索题集**。查询用英文原题，不带 evidence。每题的 **Relevant Set** 从金标准 SQL 中解析，冻结题加标记。
3. 检索评测运行器逐题调用 `retrieve_knowledge`（每题按 `db_id` 给定 Knowledge Scope，top_k=20），把 Hit 归并成 **Table Ranking**，在表级 K=2/3/5 上计算 **Table Complete Recall@K**（主指标）和 **Table Recall@K**，再给出每题的 **Tables to Cover**；列层面只报告诊断用的 **Column Recall@20**（K 以 Hit 计）。最后输出逐题结果和汇总。
4. 把 L01–L10 落成真实 toxicology Corpus 上的集成测试，作为冒烟。它不进评测报告。

## 用户故事

1. 作为学习者，我想有一个讲会员、活动、预算的业务库，这样我看得懂题目，也能自己判断检索为什么失败。
2. 作为学习者，我想让 student_club 的 Data Semantics 和 toxicology 用同一种格式、同一个 Build Profile，这样两个库的结果可以放在一起看。
3. 作为评测开发者，我想让 student_club 的 Data Semantics 只依据表结构和 BIRD 字段说明来写，不看任何题目，这样知识里不会混入题目信息。
4. 作为评测开发者，我想让 student_club 的 Corpus 和 toxicology 一样，经过构建、校验、导入、回读核对，这样检索时用的是确定的一份 Corpus。
5. 作为评测开发者，我想用一条命令从 BIRD mini-dev 生成检索题集，这样题集可以复现，不用手工拼。
6. 作为评测开发者，我想让题集包含这两个库在 mini-dev 中的全部题目，这样样本量（88 题）足够看出趋势。
7. 作为评测开发者，我想让每题的 Relevant Set 自动从金标准 SQL 解析出表和列，这样不用逐题手工标注。
8. 作为评测开发者，我想让解析能处理别名、不带表名的列、JOIN 和子查询，这样 Relevant Set 不会漏掉或认错对象。
9. 作为评测开发者，我想让 `COUNT(*)` 这种 `*` 不算成列，这样列召回率的分母只包含真实的列。
10. 作为评测开发者，我想让 Relevant Set 里的表名和列名统一成 Catalog 里的写法，这样和 Hit 的定位字段比较时不会因为大小写不同而漏算。
11. 作为评测开发者，我想让解析失败的题目让题集生成整体失败，并报出题号，这样不会悄悄丢题，换掉分母。
12. 作为评测开发者，我想让题集记录每题是否属于冻结 100 题，这样分析失败时可以跳过冻结题。
13. 作为评测开发者，我想让题集记录每题的 Knowledge Scope，这样运行时直接用这一份 Corpus，不用再去猜。
14. 作为评测开发者，我想让题集文件留在本地，只把题号清单和来源 hash 组成的 manifest 提交进 Git，这样不会提交 BIRD 原始数据。
15. 作为评测开发者，我想用一条命令跑完整个检索评测，这样每次改完检索都能马上重测。
16. 作为评测开发者，我想让每题只检索一次（top_k=20），所有指标都从这一份排序算出，这样各指标之间彼此一致，也更省时间。
17. 作为评测开发者，我想把 Hit 按 rank 归到所属的表（Document）并去重，得到 Table Ranking，这样评的就是下游真正拿到的 Documents。
18. 作为评测开发者，我想让关系类 Hit 归到它的源表，这样排进一个有用的关系不会被当成"浪费名额"。
19. 作为评测开发者，我想让表级 K 以表计，而不是以 Hit 计，这样一张表切成多少个 Chunk 不会影响分数，不同 Build Profile 之间可以公平比较。
20. 作为评测开发者，我想看到 Table Complete Recall@K（K=2/3/5），这样我知道有多少题的必需表全部拿到了；少一张表，SQL 基本就写不对。
21. 作为评测开发者，我想同时看到 Table Recall@K，这样能区分"差一张表"和"一张都没拿到"。
22. 作为评测开发者，我想看到每题的 Tables to Cover，以及它的中位数和平均数，这样在只有 4 到 8 张表的小库上，也能看出检索混进了多少无关的表。
23. 作为评测开发者，我想看到 Column Recall@20（K 以 Hit 计），只作诊断，不和表级指标合成一个分数，这样能看出排序在列层面哪里不好，又不会让下游用不到的列影响主结论。
24. 作为评测开发者，我想让 Relevant Set 里没有列的题不参与 Column Recall 平均，并在报告里写明这样的题有几道，这样平均值不会被 0/0 扭曲。
25. 作为评测开发者，我想在逐题结果里看到每个相关表在 Table Ranking 中的名次、每个相关列第一次出现的 Hit rank（没出现就是空），这样不用重跑也能分析失败。
26. 作为评测开发者，我想看到按库分开的汇总和整体汇总，这样能比较两个库的差别。
27. 作为评测开发者，我想看到每题和整体的检索耗时，这样能注意到性能问题。
28. 作为评测开发者，我想让查询没有有效词（input_error）的题记为未覆盖，计入分母并写明原因，这样不会因为报错少算一道题。
29. 作为评测开发者，我想让知识库连接失败、范围不存在这类基础设施错误直接中止整批评测，这样不会得到一份有一半题失败的报告。
30. 作为评测开发者，我想让报告记录题集 hash、每个库的 Knowledge Scope、检索配置版本、top_k 和 K 列表，这样两次报告能对上，知道它们的条件是否相同。
31. 作为评测开发者，我想让报告里不包含问题原文和 SQL，这样报告可以放心保存和分享。
32. 作为评测开发者，我想让报告写进现有的运行记录目录，并带运行事件，这样和 SQL 评测的产物管理方式一致。
33. 作为评测开发者，我想让评测运行器可以换成假检索器，这样计分规则不用连 PG 就能测，测试又快又稳定。
34. 作为学习者，我想亲手写计分规则（单题在某个 K 下的表覆盖和列召回），这样能讲清楚这个指标怎么算、为什么这样算。
35. 作为维护者，我想让 L01–L10 成为真实 toxicology Corpus 上的集成测试，这样以后改检索时，基本行为有回归保护。
36. 作为维护者，我想让评测模块只通过公开入口依赖检索和知识模块，这样模块边界测试能守住依赖方向。
37. 作为面试准备者，我想得到一组可追溯的检索基线数字（BM25、英文原题、两个库），这样以后接入向量检索或混合检索时有对照。

## 实现决定

**student_club 的 Corpus（Claude 直接做）**
- 走现有流程：抽取 Catalog → 用 BIRD 字段说明丰富 → 批准修正 → 用 Data Semantics 构建 Corpus → 校验 → 导入 → 回读核对。不新增代码接口。
- 字段说明 CSV 取自本地完整的 BIRD mini-dev 数据集（不需要下载）。原始数据不进 Git。
- Data Semantics 由 Claude 起草，用户审阅：每张表的中文用途、一行代表什么、每个外键关系的含义。起草时只参考表结构和字段说明 CSV，不看题目、evidence 和金标准 SQL。
- 修正计划只处理字段说明 CSV 本身的格式和对应问题，也不参考题目。
- Build Profile 与 toxicology 相同（`r1-4-v1`）。

**检索题集准备（Claude 直接做，测试接口 2）**
- 公开入口 `prepare_retrieval_cases`，输入为：BIRD mini-dev 题目文件、冻结 100 题文件、每个库对应的 Corpus 目录、输出路径。
- 题目范围：`db_id` 属于给定 Corpus 的全部题目。
- Relevant Set：用 sqlglot 按 SQLite 方言解析金标准 SQL，借助该库 Catalog 补全不带表名的列、解析别名，然后取出引用到的全部表和列（包括 JOIN 键和过滤条件里的列）。`*` 不算列。名称统一成 Catalog 中的写法。
- 任一题解析失败，或引用了 Catalog 中不存在的对象，题集生成就整体失败，并报出题号。
- 每题记录：题号、`db_id`、难度、问题原文、是否冻结、Knowledge Scope、相关表、相关列。Knowledge Scope 从 Corpus 的 manifest 中读取。
- 金标准 SQL 和 evidence 不写进题集，只在解析时读取。
- 题集文件放在本地评测目录（Git 忽略）。另外提交一份 manifest，内容是题号清单、来源文件 sha256、各库 Knowledge Scope 和题集文件 sha256。

**检索评测运行器（Claude 写外层，计分是结对核心，测试接口 1）**
- 公开入口 `evaluate_retrieval(cases, settings, *, retriever=retrieve_knowledge, run_context=None)`，形状参照 SQL 评测的 `run_direct`。`retriever` 是可以替换的检索函数，签名和 `retrieve_knowledge` 相同。
- 每题构造一个检索请求：英文原题、题集里的 Knowledge Scope、top_k=20、不限知识类型。
- 错误处理：`input_error` 记到这道题上，按空的 Hit 列表计分（表级指标为 0，Tables to Cover 为空，有相关列时 Column Recall 为 0）；其他检索错误直接中止整批，并保留原因。
- **计分规则（结对核心，用户来写）**：
  - 名称比较不区分大小写。
  - **Table Ranking**：按 Hit 的 rank 顺序，把每个 Hit 归到所属的表，去重，保留第一次出现的顺序。表 Chunk 和列 Chunk 取 `table_name`；关系 Chunk 的定位字段为空，从 `chunk_key` 取源表（`chunk_key` 的格式由测试固定）。
  - **Table Complete Recall@K**（K=2/3/5，以表计）：单题为"相关表全部在 Table Ranking 前 K 张中"的是/否。
  - **Table Recall@K**（同一组 K）：单题为前 K 张表中相关表所占比例。
  - **Tables to Cover**：沿 Table Ranking 取到相关表全部出现时的表数；取完都没有全部出现时为空。
  - **Column Recall@20**（以 Hit 计）：相关列中有列 Chunk 出现在前 20 个 Hit 里的比例；没有相关列时为空。
  - 表级和列级指标分开报告，不合成总分。
- 逐题结果包括：题号、`db_id`、难度、是否冻结、结果（ok / input_error）、每个相关表在 Table Ranking 中的名次、每个相关列第一次出现的 Hit rank、各表级 K 下的完整召回和召回率、Tables to Cover、Column Recall@20、耗时。**不包含问题原文、SQL 和 Hit 正文。**
- 汇总分整体和各库：每个表级 K 下的 Table Complete Recall@K 与 Table Recall@K、Tables to Cover 的中位数和平均数（另报为空的题数）、Column Recall@20，还包括题数、input_error 题数、无相关列的题数，以及耗时 P50/P95。库的表数不超过 K 时，该库这一项标为"无区分度"。
- 冻结题和其他题一起计分，汇总里不单独拆出冻结题（见 ADR 0001）。
- 报告配置包括：题集 sha256、各库 Knowledge Scope、检索方法、检索配置版本、top_k、表级 K 列表、列级 K、Relevant Set 的来源说明（"金标准 SQL 引用的对象，代理标注"）。
- 运行记录沿用 runtime 的 `task_run`，与 SQL 评测一致。报告本身不含原题和 SQL，因此不需要另外做 public 导出。

**CLI（Claude 直接做，测试接口 4）**
- 在 `eval` 下新增两条命令：一条准备检索题集，一条运行检索评测。参数形式沿用现有的 `eval direct/graph`。

**模块边界**
- `evaluation.retrieval` 的依赖放开到 `retrieval`、`knowledge`、`runtime`，只能从它们的 `api.py` 导入。
- 根 `evaluation.api` 重新导出检索评测的公开入口。

**冒烟（Claude 直接做，测试接口 3）**
- L01–L10 写成集成测试：把本地真实的 toxicology Corpus 导入测试库，通过 `retrieve_knowledge` 检查前 5 名。
- 期望值沿用旧规格中的样例表。实现时如果发现 BM25 的真实结果和旧期望不一致，先报告给用户，不改期望。
- 本地没有 Corpus 文件时跳过并写明原因，但不能当作通过。

## 测试决定

- **好测试的标准**：只通过公开入口观察行为。题集准备看输出的题集内容；评测看报告；冒烟看 `retrieve_knowledge` 的结果。不直接测私有函数。内部重构时测试不需要修改。
- **接口 1 `evaluate_retrieval`（主接口，不连 PG）**：每个测试在临时目录写一份很小的题集，注入一个假检索器，让它按题返回固定的 Hit 列表，然后断言报告。乒乓 TDD 的计分用例都在这里，例如：
  - 同一张表的多个 Hit 在 Table Ranking 里只算一次，名次取第一次出现；
  - 只命中某张表的一个列 Chunk，这张表也进入 Table Ranking；
  - 关系类 Hit 按 `chunk_key` 归到源表；
  - 相关表排在 Table Ranking 第 3 张时，K=2 未完整召回、K=3 完整召回；
  - Tables to Cover 的正常值，以及取完仍未全覆盖时为空；
  - 大小写不同的名称照样能匹配；
  - 没有相关列的题不参与列召回平均；
  - `input_error` 计入分母，算作未覆盖；
  - 基础设施错误中止整批；
  - 冻结标记原样出现在逐题结果里；
  - 报告不含问题原文。
- **接口 2 `prepare_retrieval_cases`（不连 PG）**：使用原创的小型 Catalog、小题目文件和小 Corpus manifest 夹具，覆盖别名、不带表名的列、JOIN、子查询、`COUNT(*)`、大小写、冻结标记、解析失败时整体报错并给出题号、题集里不出现金标准 SQL 和 evidence。
- **接口 3 `retrieve_knowledge` 冒烟（真实 PG）**：L01–L10，跑在 `querypilot_knowledge_test` 测试库上。
- **端到端一条（真实 PG）**：用原创的小夹具 Corpus 加两三道小题，经 `evaluate_retrieval` 和真实检索器跑出一份报告，确认真实接线没问题。
- **接口 4 CLI**：参照现有 CLI 契约测试，检查参数、输出位置和退出码。
- **模块边界**：修改现有模块边界测试的允许规则，并加一个反例。
- **可参照的已有测试**：SQL 评测的 `run_direct` 测试（注入假 generator）、现有 CLI 契约测试、`tests/integration` 中的测试库夹具和 `_bm25_fixtures.py`。
- 检索质量的数字只来自真实运行，不写进测试断言。

## 不在范围内

- 选库评测：Knowledge Scope 由评测直接给定。
- 中文翻译版的问题。
- MRR、nDCG：留到混合检索评测时再加。
- 列级的 Strict Recall，以及把列指标升为主指标：等做列级裁剪时再说。
- 从金标准 SQL 的 JOIN 条件评"连接路径是否找到"：等做外键扩展时再说。
- 人工复核 Relevant Set：只抽查解析结果，不逐题手工标注。
- 根据这次评测结果修改检索、Data Semantics 或 Build Profile：那是下一步，要遵守 ADR 0001。
- 其他 BIRD 库的 Corpus。
- `querypilot retrieval search` CLI：这是另一项工作。
- SQL 评测的新指标（例如 Soft F1）。

## 补充说明

- 题数：toxicology 40 题（其中冻结 8 题），student_club 48 题（其中冻结 8 题；另有 4 题属于 12 题开发集）。分析失败时只看非冻结的 72 题。
- 指标设计参考了表检索和 schema linking 的论文：JAR 和多表迭代检索用 Recall@K 与 Complete Recall@K，K 以表计；RSL-SQL 把列召回分为 Strict 和 Non-Strict；CRUSH4SQL 在固定预算下算召回。它们的相关集合都取自金标准 SQL，表和列都分开报告。
- 每题通常需要 2 张表（toxicology 40 题中有 24 题，student_club 48 题中有 29 题），所以表级 K=2 是"刚好够数"，最严格。toxicology 只有 4 张表，K=5 无区分度。
- 只给定一个库时，表级检索偏容易；论文常把多个库的 schema 合在一起来加大难度。以后做选库或多库评测时，可以沿用同一组指标。
- 可以预见英文问题和中文表用途之间会有词汇对不上的问题。这本身就是这次要量化的局限，不要为了提高分数提前修补。
- 结对核心那张工单（计分规则）标为 `ready-for-human`。
