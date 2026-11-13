# 01: 两库 88 题 SQL 题集

**What to build:** 评测开发者运行一条命令，从 BIRD Mini-Dev 生成 toxicology 和 student_club 全部 88 题的 SQL 题集，每题标明是否属于冻结集。现有的 `eval graph` 不用改就能在这份题集上跑，六组对比都用它。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 命令从固定的来源文件筛出这两个库的全部题（toxicology 40 道、student_club 48 道），题目顺序确定
- [ ] 每题带冻结标记：题号在冻结 100 题的 manifest 里为真，应有 16 道
- [ ] manifest 提交在 `evals/`，记录题号、来源文件和题集文件的 sha256、冻结题号；题集文件留在本地，被 Git 忽略
- [ ] 来源文件的 hash 与现有 manifest 记录的不一致时拒绝生成
- [ ] SQL 评测的逐题记录带上冻结标记；题目没有这个标记时（12 题、100 题题集）行为不变
- [ ] 题号集合与检索题集的 88 题一致，核对结果记在 Comments
- [ ] 测试用小的假 BIRD 文件，覆盖：只取指定的库、冻结标记、manifest 的 hash、来源 hash 不一致时拒绝
- [ ] 用 fixture provider 在 88 题上跑通一次管线（不调用模型），确认题集可用
- [ ] SQL 评测文档的"题集"一节补上这份题集
