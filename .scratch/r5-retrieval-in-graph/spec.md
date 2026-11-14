# R5 检索接入查询图

Status: ready-for-agent

## 问题

检索已经在 88 题检索题集上评测过（BM25、向量、hybrid、重排），但只量到"相关的表有没有排进前几位"。查询图至今没有用到检索：它把整个库的表名和列名（`表(列, 列…)`）直接交给模型，没有类型、字段说明、取值解释和表之间的关联语义。

用户（项目开发者，也是学习者）想知道三件事，现在都回答不了：

- **检索到的知识能不能帮模型写对 SQL？** 和"把全部知识都给模型"相比，检索丢了多少准确率、省了多少 token？
- **知识内容本身有没有用？** 字段说明和中文语义，比只给结构强多少？
- **建表语句是不是关键？** 结构写成 `CREATE TABLE`，和写成列名清单相比有没有差别？

## 解决方案

1. 查询图增加一个编译时参数 **Context Source**，决定给模型的 Schema 取自哪里：裸表列名（今天的行为，默认）、全部建表语句、全量知识、检索得到的 Retrieval Context。
2. 检索作为图里的一个节点：每题按问题原文检索一次，Hit 按表归拢成 Retrieval Context，再精简渲染成 Schema。只放有用的结构、知识和线索，去掉标识性字段和"来源未说明"。
3. 全量知识和检索两种来源各支持两种**结构写法**：列名清单，或这张表的建表语句。
4. SQL 评测在 toxicology 和 student_club 的 88 题上跑六组，提示词模板不变，各组只差 Schema 文本：

| | 无知识 | 全量知识 | 检索 |
|---|---|---|---|
| **列名** | ① 裸名字（现有基线） | ③ | ⑤ |
| **建表语句** | ② 全部建表语句 | ④ | ⑥ |

同一列上下两格的差是建表语句的作用；同一行从左到右是知识内容的作用和检索取舍的代价。

## 用户故事

1. 作为学习者，我想让查询图用检索到的知识生成 SQL，这样能量出检索对最终结果的贡献，而不只是对名次的贡献。
2. 作为学习者，我想在同一批题、同一个模型、同一份提示词模板下对比六组 Context Source，这样每两组之间只差一样东西。
3. 作为学习者，我想单独看到"全部建表语句、不带知识"这一组，这样建表语句的作用和知识内容的作用能分开。
4. 作为图的调用方，我想让 Context Source 默认是裸表列名，这样现有的命令行、HTTP 入口、测试和行为都不变。
5. 作为图的调用方，我想让 Context Source 在编译图时选定，这样一张编译好的图只有一条固定路径，没有条件边。
6. 作为图的调用方，我想按次把"库 → Knowledge Scope"对照表通过运行时 context 传进去，这样 Scope 不进图状态，模型看不到，同一张图可以给不同的库或不同版本的知识使用。
7. 作为图的调用方，我想在对照表里没有当前库时得到明确的失败，这样不会在不知道用哪份 Corpus 的情况下继续。
8. 作为图的调用方，我想让查询图里的检索默认用 hybrid + 重排、`top_k=10`，也能在编译时改成别的检索方法，这样产品路径用召回最高的配置，跑通链路时可以先用不调用外部服务的 lexical。
9. 作为 SQL 生成的使用者，我想让每张被命中的表都带上表用途、记录粒度和完整的结构（全部列、主键、外键），这样模型不会因为只看到命中的那一列而不知道主键和 JOIN 键。
10. 作为 SQL 生成的使用者，我想让命中的列带上类型、字段解释，以及有内容的展示名和取值解释，这样模型知道列的含义和编码。
11. 作为 SQL 生成的使用者，我想让命中的关系带上字段对和关联语义，同一对表有多条外键时带上角色区别，这样模型知道该用哪条外键连接。
12. 作为 SQL 生成的使用者，我不想看到 not_null / 主键位置 / hidden、默认 SQL 表达式、"来源未说明"、重复的数据库名、`"main".` 前缀、与字段对重复的候选 JOIN，以及 ON UPDATE / DELETE / MATCH，这样上下文里没有对写 SQL 无用的内容。
13. 作为 SQL 生成的使用者，我想让表按"最靠前的 Hit 名次"排序，表内的列和关系也按名次排，这样最相关的内容在最前面，同样的 Hit 每次渲染出同样的文本。
14. 作为学习者，我想让关系 Hit 只给外键的源表补表级知识，不补目标表，这样检索组量到的是检索本身，外键扩展留给 R6 单独量。
15. 作为学习者，我想让全量知识组和检索组走同一段组装和渲染代码，这样两组的差别只在"哪些 Chunk 进来了"。
16. 作为学习者，我想让检索继续匹配 Chunk 原来的 `content`、Corpus 不变，这样已有的检索评测数字仍然对应这次用到的检索。
17. 作为安全负责人，我想让 AST 检查继续允许这个库的全部表、不缩小到检索到的表，这样检查表达的仍是"允许访问什么"，模型用了一张没被检索到的表不会被误拒。
18. 作为安全负责人，我想让 Knowledge Scope、Hit 正文和建表语句都只当作数据，不作为授权依据，这样知识里的文字不能改变能查哪个库。
19. 作为安全负责人，我想让日志和运行记录仍然不写问题原文、SQL、Hit 正文和建表语句。
20. 作为评测开发者，我想在检索没有命中、查询没有有效词、Embedding 或重排失败时让这道题直接失败，不调用模型，也不退回裸表列名，这样检索组里不会混进基线的结果。
21. 作为评测开发者，我想要一份这两个库全部 88 题的 SQL 题集，标出其中 16 道冻结题，这样六组用同一批题，失败分析只看 72 道非冻结题。
22. 作为评测开发者，我想用 `eval graph` 的参数选择 Context Source、结构写法和检索配置，这样六组用同一个运行器。
23. 作为评测开发者，我想让报告记录 Context Source、结构写法、渲染版本、检索方法与配置版本、`top_k` 和线程数，这样每个运行目录是哪一组一眼能分清。
24. 作为学习者，我想逐题看到模型接口返回的 `prompt_tokens`，这样能讲清每种 Context Source 的上下文成本，而不用引入 tokenizer。
25. 作为学习者，我想让检索组的准确率按"检索是否拿全了 Relevant Set 的表"拆成两类，这样能分清 SQL 写错是因为检索没给全，还是给全了模型仍然写错。
26. 作为学习者，我想先用 lexical 把检索组完整跑通，再用 hybrid + 重排跑正式结果，并把两次都留在报告里，这样还能看到"检索质量提高，SQL 准确率跟不跟着涨"。
27. 作为评测开发者，我想用 `eval graph --workers N` 同时跑多道题，报告顺序不变，这样八次运行在二十分钟左右跑完。
28. 作为开发者，我想在测试里注入假的 SQL 生成器、假的检索器和假的知识读取，这样图和评测运行器的测试不联网、不花钱、结果确定。
29. 作为开发者，我想让所有收费的评测运行都在我同意后才开始，并记录配置和产物位置。

## 实现决定

### Context Source 与图

- `build_query_graph` 增加编译时参数：
  - `context_source`：`schema`（默认）、`ddl`、`knowledge`、`retrieval`；
  - `structure_format`：`columns` 或 `ddl`，只对 `knowledge` 和 `retrieval` 有效；
  - 检索配置：`method` 默认 `hybrid`，`rerank` 默认开启，`top_k` 固定为 10。只对 `retrieval` 有效。
- 四条路径在编译时选定，不用条件边：

```text
schema   : load_schema ──────────────────────────────→ generate_sql → validate_sql → execute_sql
ddl      : load_schema ──────────────────────────────→ 同上
knowledge: load_schema → load_knowledge → build_context → 同上
retrieval: load_schema → retrieve       → build_context → 同上
```

- `load_schema` 四条路径都运行：给 AST 检查提供这个库的全部表名，并从 SQLite 的 `sqlite_master` 读出每张表的建表语句。建表语句每题现读（实测约 0.5 毫秒），不落盘、不进 Corpus。
- `schema` 路径的行为和今天完全相同。`ddl` 路径的 Schema 是全部表的建表语句，按表名顺序拼接。
- **Knowledge Scope 的进入方式**：图声明运行时 context（LangGraph 的 `context_schema`），调用时传入"`database_id` → Knowledge Scope"对照表。`knowledge` 和 `retrieval` 路径按状态里的 `database_id` 查表；查不到就让请求失败。Scope 不写进图状态。Access Grant 不在 R5 实现，仍由调用方显式指定数据库。
- `retrieve` 节点：按问题原文、当前库的 Scope 和编译时的检索配置调用一次检索，得到 Hit；再读出这些 Hit 所属表的表 Chunk（表级知识的来源，列 Hit 所属的表不一定自己被命中）。一个 Hit 属于哪张表：表 Chunk 和列 Chunk 取自己的表，关系 Chunk 取外键的源表。这条规则现在在检索评测的计分里，改为由 retrieval 模块公开一份，评测和查询图共用。
- `load_knowledge` 节点：读出 Scope 下的全部 Chunk，按固定顺序当作 Hit 交给 `build_context`，所以 `build_context` 只有一种输入。
- 检索失败的处理和现有节点一致，直接抛出异常，不加条件边：没有命中（`no_match`）、查询没有有效词、Embedding 失败、重排失败、Scope 查不到，都让这次请求失败，不调用模型，不退回 `schema` 路径。错误类别在评测记录里能分清。
- AST 检查的允许范围不变：这个库的全部表。
- 图状态新增 Hit 列表、建表语句和 token 用量；`schema` 仍是最后给模型的文本。图版本升为 `single-v2`，`schema` 路径的行为与 `single-v1` 相同。
- 命令行和 HTTP 入口的默认 Context Source 仍是 `schema`。

### Retrieval Context 与 Schema

分成两步，对应术语表的两个词：

- **组装**（Hit → Retrieval Context）：纯函数，不做 I/O。
  - 每张被命中的表一项，带它的表 Chunk、命中的列 Chunk、命中的关系 Chunk。
  - 同一个 Chunk 只出现一次；表 Chunk 自己被命中时不重复。
  - 表按其 Hit 的最小 `rank` 升序；表内的列按 `rank`，关系排在列之后、按 `rank`。
  - 关系 Hit 只归到源表，不为目标表新增一项。
- **渲染**（Retrieval Context → Schema 文本）：对 Chunk 的 `content` 按行标签筛选和改写。正文由固定模板生成，每行以标签开头。

| Chunk | 保留 | 去掉 |
|---|---|---|
| 表 | 表用途、记录粒度；`columns` 写法另保留全部列名、主键、出向外键 | 重复的数据库名、`"main".` 前缀 |
| 列 | 列名、声明类型、字段解释；展示名和取值解释有内容才放（取值解释可以是多行） | not_null / 主键位置 / hidden 一行、默认 SQL 表达式、所有"来源未说明" |
| 关系 | 字段对、关联语义、同表多条外键的角色区别 | 候选 JOIN、ON UPDATE / DELETE / MATCH |

- `structure_format=ddl` 时，每张表的"全部列名、主键、出向外键"三行换成这张表的建表语句原文；其余内容两种写法相同。
- 只放 Retrieval Context 里的表：没被命中的表，它的建表语句和知识都不出现。
- 渲染规则带一个版本号（`r5-schema-v1`），写进评测报告。它依赖 Chunk 正文的模板，用两个库的真实 Chunk 做测试。
- 渲染出的文本形如：

```text
表 member：记录社团会员的姓名、在社团中的职位、联系方式和 T 恤尺码…
  一行 = 一名社团会员；主键 member_id
  列：member_id, first_name, last_name, email, position, t_shirt_size, phone, zip, link_to_major
  外键：zip → zip_code(zip_code)；link_to_major → major(major_id)
  position TEXT：The position the member holds in the club
  关系 member.zip → zip_code.zip_code：<关联语义>
```

具体排版在实现时确定，以"内容符合上表"为准。

### SQL 生成

- 提示词模板 `direct-v2` 不改，Schema 文本作为它的 `schema` 参数传入。
- SQL 生成函数除 SQL 文本外，还返回模型接口报告的 `prompt_tokens` 和 `completion_tokens`；图把它们写进状态。接口没有返回用量时记为空。

### 知识读取

- 知识存储的只读会话增加两个有上限的读取：按表名取表 Chunk；取 Scope 下的全部 Chunk。由 retrieval 模块对外提供，查询图只从 retrieval 的公开入口导入。

### 评测

- **题集**：一条命令从 BIRD Mini-Dev 筛出 toxicology 和 student_club 的全部 88 题，生成 SQL 题集；题集文件留在本地（Git 忽略），manifest 提交在 `evals/`，每题标明是否属于冻结集。
- `eval graph` 增加：`--context-source`、`--structure-format`、`--method`、`--rerank / --no-rerank`、`--workers`，以及检索题集文件的路径。检索题集提供各库的 Knowledge Scope 和每题 Relevant Set 的表，`knowledge` 和 `retrieval` 两种来源必须提供。`--method` 和 `--rerank` 的默认值与查询图一致。
- **并发**：沿用检索评测的主线程派发。工作线程里跑一道题的图和金标准 SQL；比对、逐题记录和进度事件在主线程；同时在跑的题不超过 `workers` 道；报告按题集顺序。`workers` 大于 1 时逐题耗时只在同样线程数的运行之间比较。
- **报告**新增：Context Source、结构写法、渲染版本、检索方法、Embedding Profile、Rerank Profile、检索配置版本、`top_k`、线程数；逐题的 `prompt_tokens`、`completion_tokens`、是否冻结题；`retrieval` 来源另记"Relevant Set 的表是否都出现在 Hit 所属的表里"。
- **汇总**：执行匹配（整体和分库）、有效 SQL 数、`prompt_tokens` 的中位数和平均数、耗时；`retrieval` 来源另按"表拿全 / 没拿全"分别报题数和执行匹配。
- **运行**：模型用用户当前配置的对话模型，八次运行必须是同一个（gpt-5.5 已下架，2026-11-14 起用户改用 gpt6.1sol，实际模型名以运行时的配置为准并记进报告）；执行上限与 2026-10-02 的运行相同，不带 evidence。共八次运行，每次 88 题：
  1. ①②③④ 各一次；
  2. ⑤⑥ 用 lexical 各一次（跑通链路，结果保留）；
  3. ⑤⑥ 用 hybrid + 重排各一次（正式结果）。
- 每组只跑一次，±2 道题以内的差异当作噪声。逐题分析只看 72 道非冻结题（ADR 0001），`top_k`、渲染规则和检索配置都不根据 SQL 结果调整。

### 分工

- **用户写**（乒乓 TDD，Claude 先写失败的测试）：
  - 组装：Hit → Retrieval Context；
  - 图的改动：状态字段、`retrieve` / `load_knowledge` / `build_context` 节点、四条路径的连接、运行时 context。
- **Claude 写**：渲染、知识读取、Hit 所属表的公开函数、SQL 生成返回用量、88 题题集、评测运行器与并发、命令行、文档。
- LangGraph 运行时 context 对用户是新内容，实现前 Claude 先写一份带最终代码的短教程。

## 测试决定

- 好的测试只看对外行为：模型收到的 Schema 文本是什么、图返回什么、报什么错、外部服务被调用了几次、报告里记了什么。知识和检索有没有用由评测回答，不由测试回答。
- 实现前先和用户确认下面的 seam。
- **Seam 1：组装函数**，单元测试，手写的 Hit 列表。覆盖：按表归拢；列 Hit 所属的表带上表 Chunk；表 Chunk 自己被命中时不重复；关系 Hit 归到源表、不为目标表新增一项；表和表内内容的排序；同样的输入得到同样的输出。
- **Seam 2：渲染函数**，单元测试，用两个库的真实 Chunk 正文。覆盖：上表每一项"保留"和"去掉"；多行的取值解释完整保留；两种结构写法的差别只在结构那几行；同表多条外键的角色区别保留。
- **Seam 3：`build_query_graph`**，假 SQL 生成器、假检索器、假知识读取，SQLite 用测试夹具。覆盖：
  - 四种 Context Source 下假生成器各自收到的 Schema；
  - `schema` 路径的行为不变；
  - 运行时 context 里的 Scope 传到了检索请求，状态和输出里没有 Scope；
  - 对照表里没有当前库、检索没有命中、检索报错时请求失败，假生成器没有被调用；
  - 检索配置的默认值（hybrid、重排、`top_k=10`）和编译时覆盖；
  - AST 检查仍允许没被检索到的表。
- **Seam 4：知识读取**，真实 PG 加原创小 Corpus，沿用检索集成测试的夹具（`--require-postgres`）。覆盖：按表名取表 Chunk；取全部 Chunk 的顺序固定；只读到当前 Scope 的内容。
- **Seam 5：评测运行器**，假图依赖。覆盖：参数写进运行配置和报告；逐题的 token 用量和"表是否拿全"；按"拿全 / 没拿全"的汇总；`workers` 为 1 和大于 1 时报告相同；单题出错后记为 error 并继续；主线程出错或被中断后不再开始新题。
- **Seam 6：题集生成**，小的假 BIRD 文件。覆盖：只取指定的库；冻结标记；manifest 的 hash。
- 模块边界测试继续守住依赖方向：querying 从 retrieval 和 databases 的 `api.py` 导入。

## 不在范围内

- 外键扩展（R6）、选库与 Access Grant、澄清与拒答、SQL 错误分类与修复、多轮与记忆、查询改写。
- 把检索做成 tool 或 MCP server：路线图上单独的"模型自主检索"阶段。
- token 预算和超出预算时的截断规则、切块粒度和 small-to-big：留给知识结构评测。
- 新的 Build Profile、把字段解释存成结构化字段、把建表语句存进 Corpus 或落盘。
- 修改提示词模板；修改 `RetrievalRequest` 和 `eval retrieval` 的默认值（仍是 lexical、不重排）。
- 把命令行和 HTTP 入口的默认 Context Source 换成检索：六组结果出来后由用户决定。
- 预测 SQL 与金标准 SQL 的执行时间对比：用户决定等选出最好的组合后再讨论。
- 调整 `top_k`、每次检索约 180 毫秒的数据库检查开销、接入其他数据库。
- 每组重复多次运行、统计检验。

## 补充说明

- 这两个库很小：全部 Chunk 的正文 toxicology 约 5.3k 字符、student_club 约 16k 字符，全量知识完全放得进上下文。很可能的结果是检索组的准确率不超过全量知识组，只是省 token。这个结论本身可以讲，不要预期检索带来大幅提分。
- 六组的一处不对称：① 只有 `表(列, 列…)`，没有主键和外键；③⑤ 的列名写法带主键和外键。报告里写明。
- toxicology 只有 20 个 Chunk，`top_k=10` 已经是它的一半；hybrid + 重排在这个库上等于重排整个 Corpus。报告里写明。
- 默认的 hybrid + 重排让每题多依赖 Embedding 和重排两个外部服务，检索耗时从约 0.3 秒变成 1–11 秒，任一服务失败这道题就失败。
- 建表语句每题从 SQLite 现读，和 Corpus 的 Schema Version 没有绑定。BIRD 的库文件不会变；接入会变化的库时再处理。
- 费用：八次运行共 704 次模型调用；⑤⑥ 的正式运行另有两次 88 题的 Embedding 和重排调用。按用户给的价格（输入 0.78 元 / 百万 token，输出 3.9 元 / 百万 token）估算，全部运行在几元到十几元之间，主要取决于模型的推理 token；先跑 ① 一组，用报告里的用量核对后再继续。运行前向用户确认，并记录配置和产物位置。
- 已有的 30%（冻结 100 题）是在 11 个库上、用已下架的 gpt-5.5 测的，不能和这 88 题的结果直接比较；① 在 88 题上用新模型重跑后作为基线。
- GitHub 流程：每完成一张工单，经用户确认后推送 `r5-retrieval-in-graph`。第一张工单完成后开 Draft PR，正文用 `/pr` 写。
