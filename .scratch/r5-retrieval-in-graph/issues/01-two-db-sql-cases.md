# 01: 两库 88 题 SQL 题集

**What to build:** 评测开发者运行一条命令，从 BIRD Mini-Dev 生成 toxicology 和 student_club 全部 88 题的 SQL 题集，每题标明是否属于冻结集。现有的 `eval graph` 不用改就能在这份题集上跑，六组对比都用它。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [x] 命令从固定的来源文件筛出这两个库的全部题（toxicology 40 道、student_club 48 道），题目顺序确定
- [x] 每题带冻结标记：题号在冻结 100 题的 manifest 里为真，应有 16 道
- [x] manifest 提交在 `evals/`，记录题号、来源文件和题集文件的 sha256、冻结题号；题集文件留在本地，被 Git 忽略
- [x] 来源文件的 hash 与现有 manifest 记录的不一致时拒绝生成
- [x] SQL 评测的逐题记录带上冻结标记；题目没有这个标记时（12 题、100 题题集）行为不变
- [x] 题号集合与检索题集的 88 题一致，核对结果记在 Comments
- [x] 测试用小的假 BIRD 文件，覆盖：只取指定的库、冻结标记、manifest 的 hash、来源 hash 不一致时拒绝
- [x] 用 fixture provider 在 88 题上跑通一次管线（不调用模型），确认题集可用
- [x] SQL 评测文档的"题集"一节补上这份题集

## Comments

- 实现（2026-11-14，Claude，TDD）：`select_database_cases`（evaluation 公开入口，实现在 SQL 评测这一部分，和检索题集的生成放在检索评测里是同一个做法）和命令 `querypilot data bird select-databases`。失败时抛 `SqlCaseSetError`，带固定的错误类别。题集是来源文件里指定库的全部题，保持来源顺序，每题多一个 `frozen`；manifest 写在题集旁边。来源 hash 的基准取冻结 100 题 manifest 里的 `source_sha256`，所以命令只需要这一份 manifest，不再另传 12 题的 manifest。
- 规格之外加了一条校验：指定的库在来源文件里一道题都没有时拒绝生成。挡的是库名写错后悄悄得到一份变小的题集。
- 文件名用 `bird_toxicology_student_club`，不用"88"：题数由库决定，名字写库更不容易过时。题集文件已加进 `.gitignore`。
- 真实生成：student_club 48、toxicology 40，共 88 题，冻结 16 道；题集 sha256 `fbd95e30…`。各库题号、冻结题号与检索题集的 manifest 逐项相同。
- 两个来源文件的差别：SQL 题集用的是 HuggingFace 的 `mini_dev_sqlite-00000-of-00001.json`（sha256 `88ceb071…`，和 12 题、100 题同一来源），检索题集当时用的是 Mini-Dev 压缩包里的 `mini_dev_sqlite.json`（sha256 `def4b2b4…`）。核对过：这两个库的 88 题在两个文件里题号、问题原文和金标准 SQL 完全相同。
- 管线验证：`eval direct --provider fixture --cases evals/bird_toxicology_student_club.json`（行数上限 10000、超时 20 秒，不调用模型）88/88 匹配，运行目录 `artifacts/runs/evaluation/2026-11-13/39790162186b4c5a99b91e9f5911f357/`。
- 留给工单 07 的提醒：`.env` 没有设置行数上限和超时，默认是 100 行、3 秒；正式运行要像 2026-10-02 那次一样用环境变量设成 10000 行、20 秒。
- 更正（工单 03 期间发现）：最初把实现放在 `evaluation/datasets/` 下，模块边界测试不允许——那里对外只开放既有的六个导入，是有意封闭的。提交工单 01 时只跑了相关测试，没跑边界测试，所以当时没发现；已挪到 SQL 评测部分，边界测试通过。SQL 评测公开出口的契约测试相应从八项改成十项。
- 测试：`pytest tests/test_bird_database_cases.py`（6 个）和 `tests/test_graph_eval.py` 里冻结标记的 1 个。
