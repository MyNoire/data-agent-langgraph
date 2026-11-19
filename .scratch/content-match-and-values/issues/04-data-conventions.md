# 04: 数据约定

**What to build:** 语义配置可以给表写数据约定（来源 `observed`，与 `inferred` 分开），构建 Corpus 时表 Chunk 在「记录粒度」后多出一行「数据约定」，metadata 记录来源。Claude 对两个库的每张表做同一组只读检查，写出草稿；用户审阅批准后才写进语义配置。

**Blocked by:** 03

**Status:** ready-for-agent

- [ ] 语义配置可选地给每张表带数据约定文本，来源标 `observed`；旧格式配置照常可用
- [ ] 表 Chunk 只在有约定时多出「数据约定」一行，metadata 记录来源为 `observed`；没有约定时表 Chunk 不变（有测试）
- [ ] 对两个库的每张表做同一组只读检查（成对列是否对称存储、只取一个值的列、ID 的组成规律等），检查方法和结果记在本工单 Comments 里，不针对具体题目
- [ ] 草稿交给用户审阅；用户批准后才写进两个库的语义配置，批准的原话记在 Comments
- [ ] 没有发现约定的表不写
- [ ] 知识库文档写明数据约定的来源和审阅规则
- [ ] 模块边界测试和全部单元测试通过

## Comments

**2026-11-19 代码完成（7980721），草稿待审**

- 语义配置的表可以带 `data_convention: {"text": ..., "origin": "observed"}`；有约定时表 Chunk 在「记录粒度」后多一行 `数据约定: ...`，metadata 记 `data_convention: {origin: observed}`。没有约定的表不变。测试 `tests/test_knowledge_conventions.py`（先红后绿）。全部单元测试 589 通过，失败的只有之前就有的 CLI PATH 那一个。

**检查方法**（两个库的每张表都跑同一组，脚本只读打开 SQLite，隐私列只统计个数、不输出取值）：

- A 行数、每列去重非空个数和空值个数
- B 只取一个值的列、全为空的列
- C 没声明为主键却逐行唯一的列
- D 成对列：同一张表里取值有交集的两列 (x, y)，(y, x) 也存在的行占比
- E ID 组成：一列的值是否以同一行另一列的值开头；键列取值的形状（数字记 9、字母记 a）
- F 外键在目标表里找不到的值
- G 文本列里能当数字读的占比

对 A–G 发现的线索再做核对：派生列的计算关系（remaining、spent）、跨表是否一致（event_status）、日期格式、编号规则、孤立记录。

**结果**

| 表 | 发现 | 写成约定？ |
|---|---|---|
| toxicology.connected | 18312 行 = 9156 条键 × 2；每条键 (atom_id, atom_id2) 和 (atom_id2, atom_id) 都存，bond_id 相同；38 个原子不在这张表里 | 写 |
| toxicology.atom | atom_id 全部是 molecule_id + '_' + 序号，序号不补零（TR000_1 … TR000_100） | 写 |
| toxicology.bond | bond_id 全部是 molecule_id + '_' + 较小的原子序号 + '_' + 较大的原子序号；每条键两端的原子都属于这个分子 | 写 |
| toxicology.molecule | label 只有 '+' / '-'（取值样例已经能看到） | 不写 |
| student_club.budget | 每个 (link_to_event, category) 一行；remaining = amount − spent 全部成立，5 行为负；spent = 这条预算下 expense.cost 的合计，全部成立（28 行没有支出，spent 为 0）；event_status 与对应 event.status 全部相同 | 写 |
| student_club.expense | approved 只有 'true'（31 行）和 NULL（1 行），没有 'false'；expense_date 是 'YYYY-MM-DD' | 写 |
| student_club.event | event_date 是 'YYYY-MM-DDTHH:MM:SS'，带时间，和 expense、income 的日期格式不同；42 个事件中 25 个没有出勤记录 | 写日期格式 |
| student_club.income | date_received 是 'YYYY-MM-DD' | 写 |
| student_club.member | zip 是整数，前导 0 丢失（5 名会员的 zip 小于 10000） | 写 |
| student_club.zip_code | zip_code 是整数，前导 0 丢失（最小 501）；state 是全称、short_state 是缩写，一一对应；county 带后缀（'... County'，波多黎各是 '... Municipio'），88 行为空 | 写 |
| student_club.attendance | 只有两列外键，无其他规律 | 不写 |
| student_club.major | major_name 逐行唯一 | 不写（价值低） |
| 所有 student_club 的 ID | 全部是 'rec' 开头的 17 位随机串，不含其他信息 | 不写（取值样例能看到） |

**草稿（待用户批准）**

- toxicology.connected：每条化学键正反各存一行：(atom_id, atom_id2) 和 (atom_id2, atom_id) 都在，bond_id 相同，所以本表行数是键数的两倍；数键用 bond 表或 COUNT(DISTINCT bond_id)。没有任何键的原子不出现在本表。
- toxicology.atom：atom_id 由 molecule_id、下划线和原子序号组成，序号不补零（如 'TR000_1'、'TR000_10'），按文本排序与按序号排序不同。
- toxicology.bond：bond_id 由 molecule_id、较小的原子序号、较大的原子序号用下划线连接而成（如 'TR000_1_2' 连接 'TR000_1' 和 'TR000_2'）。
- student_club.budget：每个活动的每个类别一行。remaining = amount - spent；spent 是关联到这一行的 expense.cost 之和，没有支出时为 0；remaining 可以为负（超支）。event_status 与所属 event 的 status 相同。
- student_club.expense：approved 只有 'true' 和 NULL 两种，没有 'false'。expense_date 是 'YYYY-MM-DD' 文本。
- student_club.event：event_date 是 'YYYY-MM-DDTHH:MM:SS' 文本，带时间；和其他表的日期比较时要先取日期部分。
- student_club.income：date_received 是 'YYYY-MM-DD' 文本。
- student_club.member：zip 存成整数，以 0 开头的邮编丢了前导 0。
- student_club.zip_code：zip_code 存成整数，以 0 开头的邮编丢了前导 0。state 是州全称，short_state 是两字母缩写，一一对应。county 带后缀（如 'Orange County'；波多黎各是 '... Municipio'），部分为空。
