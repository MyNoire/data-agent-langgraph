# 01: 并发跑通：`--workers` 从 CLI 到线程池

**What to build:** 评测开发者用 `eval retrieval --workers N` 同时检索 N 道题。报告和串行时一样（题目顺序、指标都不变），运行配置和报告能看出这次用了几个线程。某道题遇到致命错误时，不再开始新题，整批失败。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

规格：`.scratch/eval-concurrency/spec.md`。结对工单（乒乓 TDD）：用户写 `evaluate_retrieval` 里的并发主体，Claude 一次写一个失败的测试，每个测试之前先用几行小例子讲一个概念（线程池与 `submit`、`Future.result()`、`threading.Event`）。`--workers` 参数和配置记录由 Claude 直接写。本工单不做收费的真实运行。

- [ ] `evaluate_retrieval` 增加 `workers`，默认 1；`workers=1` 也走线程池，没有单独的串行循环
- [ ] `workers=N` 时有 N 道题同时处于检索中（测试用 `threading.Barrier` 证明，不用 `sleep`）
- [ ] 工作线程只调一次检索器；计分、逐题记录都在主线程
- [ ] 后提交的题先完成时，报告的 `records` 和 `config.retrieval` 仍按题集顺序
- [ ] 并发时 `input_error` 只记在那一题上，其他题照常完成，不置停止标志
- [ ] 工作线程遇到致命错误时先置停止标志再抛出；工作线程开始每道题之前检查标志，已置位就不调检索器
- [ ] 致命错误之后：在途的题跑完后结果丢弃，排队的题不被调用，抛出的是原始错误，运行记为 failed，没有 `result.json`
- [ ] 现有测试"第 1 题失败后第 2 题不会被调用"不改动、保持通过
- [ ] `eval retrieval` 增加 `--workers N`，默认 1，传到运行器
- [ ] `workers` 写进运行配置（需要加进运行配置的字段白名单）和报告的 `config`；报告版本号不变
- [ ] 进度事件在本工单里仍按题集顺序写（工单 02 改成按完成顺序）
- [ ] 演示：用 lexical 各跑一次 `--workers 1` 和 `--workers 8`（不调用收费服务，需要知识库在运行），两份报告的 `records` 除 `elapsed_ms` 外完全相同

## Comments
