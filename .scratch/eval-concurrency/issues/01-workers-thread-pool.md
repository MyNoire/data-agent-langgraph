# 01: 并发跑通：`--workers` 从 CLI 到线程池

**What to build:** 评测开发者用 `eval retrieval --workers N` 同时检索 N 道题。报告和串行时一样（题目顺序、指标都不变），运行配置和报告能看出这次用了几个线程。某道题遇到致命错误时，不再开始新题，整批失败。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

规格：`.scratch/eval-concurrency/spec.md`。结对工单（乒乓 TDD）：用户写 `evaluate_retrieval` 里的并发主体，Claude 一次写一个失败的测试，每个测试之前先用几行小例子讲一个概念（线程池与 `submit`、`Future.result()`、`threading.Event`）。`--workers` 参数和配置记录由 Claude 直接写。本工单不做收费的真实运行。

- [x] `evaluate_retrieval` 增加 `workers`，默认 1；`workers=1` 也走线程池，没有单独的串行循环
- [x] `workers=N` 时有 N 道题同时处于检索中（测试用 `threading.Barrier` 证明，不用 `sleep`）
- [x] 工作线程只调一次检索器；计分、逐题记录都在主线程
- [x] 后提交的题先完成时，报告的 `records` 和 `config.retrieval` 仍按题集顺序
- [x] 并发时 `input_error` 只记在那一题上，其他题照常完成，不置停止标志
- [x] 工作线程遇到致命错误时先置停止标志再抛出；工作线程开始每道题之前检查标志，已置位就不调检索器
- [x] 致命错误之后：在途的题跑完后结果丢弃，排队的题不被调用，抛出的是原始错误，运行记为 failed，没有 `result.json`
- [x] 现有测试"第 1 题失败后第 2 题不会被调用"不改动、保持通过
- [x] `eval retrieval` 增加 `--workers N`，默认 1，传到运行器
- [x] `workers` 写进运行配置（需要加进运行配置的字段白名单）和报告的 `config`；报告版本号不变
- [x] ~~进度事件在本工单里仍按题集顺序写（工单 02 改成按完成顺序）~~ 不适用：工单 01 和 02 的实现是一起落地的，见下
- [x] 演示：用 lexical 各跑一次 `--workers 1` 和 `--workers 8`（不调用收费服务，需要知识库在运行），两份报告的 `records` 除 `elapsed_ms` 外完全相同

## Comments

- 实现（2026-11-12）：
  - Claude 先做了铺垫（bb2b004）：`evaluate_retrieval(..., workers=1)`、`retrieval_run_config` 多一个 `workers`、`eval retrieval --workers`，`workers` 加进运行配置的字段白名单并写进报告的 `config`。先写失败测试再实现的有两个：`workers` 写进运行配置和报告且默认为 1；CLI 的 `--workers`。
  - 并发主体由用户写：工作线程的函数 `_retrieve_case`（返回 `(outcome, 检索结果)`，`outcome` 为 `ok` / `input_error` / `skipped`）和主流程里的线程池、停止标志、按完成顺序收集。驱动它的失败测试是"`workers=3` 时 3 道题同时处于检索中"（`threading.Barrier`，串行实现会在关卡上超时）。
- **和计划不同的地方**：用户想先系统学一遍，Claude 写了一份教学文档（不在仓库里），里面给出了覆盖工单 01 和 02 的完整写法。用户照着一次写完了两张工单的实现，所以没有出现"工单 01 做完、事件仍按题集顺序"的中间状态。其余测试是实现之后补的，不是先红后绿；为确认它们有效，Claude 在仓库的临时副本里对两个版本各跑了一遍：对最终写法全部通过，对"没有 `as_completed` 和 `finally`"的中间写法，事件顺序和主线程失败两个测试失败。
- 审查用户的实现时发现并由用户改掉的两处：
  - `enumerate(cases, start=1)` 的结果被当作列表下标用，最后一题越界（`IndexError`），其余题的结果也会错位一格。去掉 `start=1`，给人看的序号由 `case_index=position + 1` 负责。
  - 编辑器自动补了一行 `from multiprocessing.resource_sharer import stop`，被局部变量盖住所以没有报错。已删除。
- "致命错误之后排队的题不被调用"的测试覆盖：
  - `workers=1`：现有测试，第 1 题失败后第 2 题不被调用。这是确定的，因为置位和取下一题在同一个线程里先后发生。
  - `workers=2`、两道题同时在途：第 1 题失败，第 2 题跑完后结果丢弃，抛出原始错误，运行记为 failed。
  - `workers>1` 且还有排队的题：没有单独的测试。测试无法知道停止标志是哪一刻置位的，要让在途的题"等到置位之后再返回"只能靠时间。它和 `workers=1` 走的是同一段检查标志的代码。
- 演示（2026-11-12，lexical，88 题，本地知识库，不调用收费服务）：
  - `--workers 1`：`artifacts/runs/evaluation/2026-11-12/708726076057461799633e395aee6bcb`，从 `eval_started` 到 `eval_finished` 17.8 秒，单题耗时 P50 / P95 为 192 / 233 ms。
  - `--workers 8`：`artifacts/runs/evaluation/2026-11-12/df479a2a7a9847d68d4d3d39fc038eb0`，3.7 秒，单题耗时 P50 / P95 为 315 / 490 ms。
  - 两份报告的 `records` 除 `elapsed_ms` 外完全相同，题目顺序、汇总指标和 `config.retrieval` 也相同。8 个线程同时访问知识库没有出错。
  - 单题耗时在并发下变长了（P50 192 → 315 ms），印证了"耗时只在 `workers=1` 的运行之间比较"。
- 测试结果：`pytest tests --ignore=tests/integration` 470 passed，1 skipped，1 failed（`test_console_and_module_share_help`，`querypilot` 不在 PATH 上，与本工单无关）；`pytest tests/integration/test_retrieval_eval.py --require-postgres` 1 passed；`tests/test_retrieval_eval_runner.py` 单独跑 19 passed。本工单没有调用收费服务。
