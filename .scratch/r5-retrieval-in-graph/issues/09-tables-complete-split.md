# 09: 按"表是否拿全"拆分

**What to build:** 学习者在检索组的报告里看到每道题"检索有没有拿全 Relevant Set 的表"，以及按这两类分别统计的执行匹配。这样能分清 SQL 写错是因为检索没给全，还是给全了模型仍然写错。

**Blocked by:** 08

**Status:** ready-for-agent

- [x] `retrieval` 来源的逐题记录多一项"表是否拿全"：这道题 Relevant Set 的表是否都出现在 Hit 所属的表里
- [x] Relevant Set 取自检索题集，按题号与 SQL 题集对应；题号在检索题集里找不到时在开始前报错
- [x] 检索失败的题没有 Hit，记为没拿全
- [x] 汇总按"拿全 / 没拿全"分别报题数、执行匹配数和比例，整体和分库各一份
- [x] 其他 Context Source 的报告里没有这一项
- [x] 报告不含 Hit 正文和问题原文
- [x] 测试用假图依赖，覆盖：拿全、没拿全、检索失败三种题的记录，以及两类的汇总

## Comments

- 实现（2026-11-17，Claude）：`_run_case` 从图的 stream 里取 `hits` 增量，只留 Hit 所属的表名（`hit_table`）；`_case_record` 对 retrieval 来源记 `tables_complete = Relevant Set 的表 ⊆ Hit 所属的表`，没走到检索或检索失败时为 False。汇总 `tables_complete = {overall, by_database}`，每份是 `complete` / `incomplete` 各自的 `cases`、`matched`、`rate`（没有题时 rate 为 None）。
- Relevant Set 来自 `evaluation.api.relevant_tables_from_cases(检索题集)`；缺题号的检查和缺 Scope 一样放在 `sql_run_config`，在建立运行记录之前报错。Relevant Set 本身不写进运行配置（检索题集的 sha256 已经在检索评测里记过，这里按题号读）。
- 公开导出加了逐题 `tables_complete` 和汇总；其他 Context Source 没有这一项。报告只有表名判断的结果，不含 Hit 正文和问题原文。
- 真实数据空跑（2026-11-17，不调用模型和付费服务）：88 题、lexical 不重排、生成器返回金标准 SQL，88/88、没有出错的题，检索版本记为 `r3-bm25-v1`。lexical 下拿全 74 题（toxicology 37/40，student_club 37/48），没拿全 14 题。运行目录 `artifacts/runs/evaluation/2026-11-17/5e5827e65d45401c8cbbc53cbac43c1d`。
- 测试：`tests/test_graph_eval_retrieval.py`（拿全 / 没拿全 / 检索失败三种题和两类汇总、缺 Relevant Set 时不留运行记录、公开导出与其他来源没有这一项、从检索题集读 Relevant Set）。
