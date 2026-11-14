# 05: 组装与渲染

**What to build:** 一组 Hit 变成给模型看的精简 Schema 文本：先按表归拢成 Retrieval Context，再渲染。只放有用的结构、知识和线索；支持列名和建表语句两种结构写法。这一张不碰图。

**Blocked by:** None (can start immediately)

**Status:** ready-for-human

结对分工：组装（Hit → Retrieval Context）由**用户写**（乒乓 TDD）。渲染（Retrieval Context → Schema 文本）和"Hit 属于哪张表"的公开函数由 Claude 用 TDD 写。

"Hit 属于哪张表"

- [ ] retrieval 模块公开这条规则：表 Chunk 和列 Chunk 取自己的表，关系 Chunk 取外键的源表
- [ ] 检索评测的计分改用它，评测结果不变

组装（纯函数，不做 I/O）

- [ ] 输入是 Hit 列表和这些 Hit 所属表的表 Chunk；每张被命中的表一项，带它的表 Chunk、命中的列 Chunk 和命中的关系 Chunk
- [ ] 同一个 Chunk 只出现一次；表 Chunk 自己被命中时不重复
- [ ] 关系 Hit 只归到源表，不为目标表新增一项
- [ ] 表按其 Hit 的最小 `rank` 升序；表内的列按 `rank`，关系排在列之后、按 `rank`
- [ ] 同样的输入得到同样的输出

渲染

- [ ] 表：保留表用途、记录粒度；`columns` 写法另保留全部列名、主键、出向外键；去掉重复的数据库名和 `"main".` 前缀
- [ ] 列：保留列名、声明类型、字段解释；展示名和取值解释有内容才放，多行的取值解释完整保留；去掉 not_null / 主键位置 / hidden 一行、默认 SQL 表达式和所有"来源未说明"
- [ ] 关系：保留字段对、关联语义、同表多条外键的角色区别；去掉候选 JOIN 和 ON UPDATE / DELETE / MATCH
- [ ] `ddl` 写法：每张表的"全部列名、主键、出向外键"换成传入的建表语句原文，其余内容与 `columns` 写法相同
- [ ] 渲染规则带版本号 `r5-schema-v1`

测试与验证

- [ ] 组装的单元测试用手写的 Hit 列表，覆盖上面每一条
- [ ] 渲染的单元测试用两个库的真实 Chunk 正文，覆盖每一项"保留"和"去掉"，以及两种写法的差别只在结构那几行
- [ ] 用一个自编的问题（不取自评测题）在真实知识库上检索一次，把两种写法渲染出的 Schema 贴在 Comments 里给用户看
