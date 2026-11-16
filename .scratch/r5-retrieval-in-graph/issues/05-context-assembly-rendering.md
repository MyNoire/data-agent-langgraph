# 05: 组装与渲染

**What to build:** 一组 Hit 变成给模型看的精简 Schema 文本：先按表归拢成 Retrieval Context，再渲染。只放有用的结构、知识和线索；支持列名和建表语句两种结构写法。这一张不碰图。

**Blocked by:** None (can start immediately)

**Status:** ready-for-human

结对分工：组装（Hit → Retrieval Context）由**用户写**（乒乓 TDD）。渲染（Retrieval Context → Schema 文本）和"Hit 属于哪张表"的公开函数由 Claude 用 TDD 写。

"Hit 属于哪张表"

- [x] retrieval 模块公开这条规则：表 Chunk 和列 Chunk 取自己的表，关系 Chunk 取外键的源表
- [x] 检索评测的计分改用它，评测结果不变

组装（纯函数，不做 I/O）

- [x] 输入是 Hit 列表和这些 Hit 所属表的表 Chunk；每张被命中的表一项，带它的表 Chunk、命中的列 Chunk 和命中的关系 Chunk
- [x] 表 Chunk 自己被命中时不重复（"同一个 Hit 出现两次"不处理，见 Comments）
- [x] 关系 Hit 只归到源表，不为目标表新增一项
- [x] 表按其 Hit 的最小 `rank` 升序；表内的列按 `rank`，关系排在列之后、按 `rank`
- [x] 同样的输入得到同样的输出

渲染

- [x] 表：保留表用途、记录粒度；`columns` 写法另保留全部列名、主键、出向外键；去掉重复的数据库名和 `"main".` 前缀
- [x] 列：保留列名、声明类型、字段解释；展示名和取值解释有内容才放（展示名只是列名换个写法时不放），多行的取值解释完整保留；去掉 not_null / 主键位置 / hidden 一行、默认 SQL 表达式和所有"来源未说明"
- [x] 关系：保留字段对、关联语义、同表多条外键的角色区别；去掉候选 JOIN 和 ON UPDATE / DELETE / MATCH
- [x] `ddl` 写法：每张表的"全部列名、主键、出向外键"换成传入的建表语句原文，其余内容与 `columns` 写法相同
- [x] 渲染规则带版本号 `r5-schema-v1`

测试与验证

- [x] 组装的单元测试用手写的 Hit 列表，覆盖上面每一条
- [x] 渲染的单元测试用两个库的真实 Chunk 正文，覆盖每一项"保留"和"去掉"，以及两种写法的差别只在结构那几行
- [x] 用一个自编的问题（不取自评测题）在真实知识库上检索一次，把两种写法渲染出的 Schema 贴在 Comments 里给用户看

## Comments

- 实现（2026-11-16）：
  - `retrieval.api.hit_table` 由 Claude 写，检索评测的计分改用它，原有的计分测试不改就通过。
  - 组装 `querying.api.assemble_context(hits, table_chunks)` 由用户写（乒乓 TDD，`tests/test_retrieval_context.py` 4 个测试）：按 rank 排序后遍历，用以表名为键的字典归拢。字典按插入顺序保存，表的顺序自然就是"最小 rank 升序"。表 Hit 不追加，它的正文就是 `table_chunk`。
  - 渲染 `querying.api.render_context(context, structure_format=..., table_ddl=...)` 由 Claude 写（`querying/_render.py`，版本 `SCHEMA_RENDER_VERSION = "r5-schema-v1"`）。
- `TableContext.table_chunk` 是表 Chunk 的正文字符串，由调用方传入"表名 → 正文"。所以组装不依赖知识读取函数返回什么类型，图里谁去读，留到工单 06 和 08。
- 和规格不一样的地方："同一个 Chunk 只出现一次"只做了"表 Chunk 被命中时不重复"这一半。一次检索每个 Chunk 最多返回一次，`load_knowledge` 读的也是不重复的 Chunk，"同一个 Hit 出现两次"没有来源，所以不为它写去重。
- 模块边界：querying 现在可以从 retrieval 导入（规格里定的），仍然不能直接导入 knowledge。导入 retrieval 不会加载 psycopg，多出的导入时间约 0.12 秒。
- 渲染的排版：保留 Corpus 原有的标签，只把两个长标签改短（"行标识线索（主键声明、成员顺序）"改为"主键"，"出向关联（声明外键）"改为"外键"）。标识符保留引号。续行（取值解释、角色区别）缩进后原样保留。`ddl` 写法里建表语句原文不缩进、不改写。"外键: 未声明"原样保留。
- 渲染依赖 Corpus 模板：Chunk 每行以标签开头、行的顺序固定，只有取值解释和角色区别会续行。两个库全部 76 个 Chunk 都核对过，标签照抄在 `_render.py` 里，模板改了要同步改这里并升版本。
- 测试：`tests/test_schema_render.py` 4 个（其中一个参数化 4 种展示名），用两个库 12 条真实 Chunk 的正文（复制在 `tests/_real_chunks.py`，因为 `data/` 不进 Git），逐字比对输出。另外用两个库的全部 Chunk 全量渲染了一遍（脚本没有入库）：每条都能解析，输出里没有"来源未说明"、`"main".`、候选 JOIN、声明标志、默认值、数据库名和展示名。全量知识渲染后的长度约为 Chunk 原文的一半：toxicology 5339 → 2619 字符，student_club 15954 → 7371 字符。
- 真实知识库上的一次检索（2026-11-16，lexical，top_k=10，不调用收费服务；表 Chunk 取自本地 Corpus 文件，因为知识读取函数在工单 08 才写）。问题是自编的，不取自评测题："Which members went to events, and what is each member's major?"

```text
hits:
1 column ["column","student_club","main","member","link_to_major"]
2 column ["column","student_club","main","major","major_id"]
3 column ["column","student_club","main","member","last_name"]
4 column ["column","student_club","main","major","major_name"]
5 column ["column","student_club","main","income","link_to_member"]
6 column ["column","student_club","main","attendance","link_to_member"]
7 column ["column","student_club","main","expense","link_to_member"]
8 column ["column","student_club","main","member","member_id"]
9 table ["table","student_club","main","member"]
10 table ["table","student_club","main","major"]
```

columns 写法：

```text
表 "member"
  表用途: 记录社团会员的姓名、在社团中的职位、联系方式和 T 恤尺码，是按会员筛选、统计并关联专业、家乡和活动的入口。
  记录粒度: 一名社团会员。
  字段目录: "member_id", "first_name", "last_name", "email", "position", "t_shirt_size", "phone", "zip", "link_to_major"
  主键: "member_id"
  外键: "zip" → "zip_code" ("zip_code"); "link_to_major" → "major" ("major_id")
  列 "link_to_major" TEXT: The unique identifier of the major of the member. References the Major table
  列 "last_name" TEXT: member's last name
    取值解释: commonsense evidence: 
    full name is first_name + last_name. e.g. A member's first name is Angela and last name is Sanders. Thus, his/her full name is Angela Sanders.
  列 "member_id" TEXT: unique id of member

表 "major"
  表用途: 记录专业及开设该专业的院系和学院，用于按专业、院系或学院分析会员。
  记录粒度: 一个专业。
  字段目录: "major_id", "major_name", "department", "college"
  主键: "major_id"
  外键: 未声明
  列 "major_id" TEXT: A unique identifier for each major
  列 "major_name" TEXT: major name

表 "income"
  表用途: 记录社团收到的资金，包括到账日期、金额、来源（如会费、学校年度拨款）和备注。
  记录粒度: 一笔收入。
  字段目录: "income_id", "date_received", "amount", "source", "notes", "link_to_member"
  主键: "income_id"
  外键: "link_to_member" → "member" ("member_id")
  列 "link_to_member" TEXT: link to member

表 "attendance"
  表用途: 记录哪位会员参加了哪场活动，用于统计活动的出席人数或会员参加过的活动。
  记录粒度: 一名会员出席一场活动。
  字段目录: "link_to_event", "link_to_member"
  主键: "link_to_event", "link_to_member"
  外键: "link_to_member" → "member" ("member_id"); "link_to_event" → "event" ("event_id")
  列 "link_to_member" TEXT: The unique identifier of the member who attended the event
    取值解释: References the Member table

表 "expense"
  表用途: 记录会员为某条预算产生的具体支出，包括用途描述、日期、金额以及是否获批。
  记录粒度: 一笔支出。
  字段目录: "expense_id", "expense_description", "expense_date", "cost", "approved", "link_to_member", "link_to_budget"
  主键: "expense_id"
  外键: "link_to_member" → "member" ("member_id"); "link_to_budget" → "budget" ("budget_id")
  列 "link_to_member" TEXT: The member who incurred the expense
```

ddl 写法（只列第一张表，其余几张表同样是三行结构换成建表语句，其他行不变）：

```text
表 "member"
  表用途: 记录社团会员的姓名、在社团中的职位、联系方式和 T 恤尺码，是按会员筛选、统计并关联专业、家乡和活动的入口。
  记录粒度: 一名社团会员。
CREATE TABLE "member"
(
    member_id     TEXT
        constraint member_pk
            primary key,
    first_name    TEXT,
    last_name     TEXT,
    email         TEXT,
    position      TEXT,
    t_shirt_size  TEXT,
    phone         TEXT,
    zip           INTEGER,
    link_to_major TEXT,
    foreign key (link_to_major) references major(major_id),
    foreign key (zip) references zip_code(zip_code)
)
  列 "link_to_major" TEXT: The unique identifier of the major of the member. References the Major table
  列 "last_name" TEXT: member's last name
    取值解释: commonsense evidence: 
    full name is first_name + last_name. e.g. A member's first name is Angela and last name is Sanders. Thus, his/her full name is Angela Sanders.
  列 "member_id" TEXT: unique id of member
```

- 这次检索看到的现象：
  - 问的是"参加活动"，但 event 表没被命中，模型只能从 attendance 的外键行看到 `link_to_event → event`。工单 09 的"表是否拿全"拆分量的就是这种情况。
  - 展示名只是把列名的下划线换成空格（`member id`、`link to major`）。用户决定（2026-11-16）：去掉空格和下划线、忽略大小写后与列名相同的展示名不放。两个库里所有有值的展示名都属于这种（唯一的变体是 `atom_id2` → `atom id 2`），所以现在的输出里没有展示名；别的库的展示名若真有额外信息（比如全称）会保留。规则还没被任何评测用过，版本仍是 `r5-schema-v1`。
