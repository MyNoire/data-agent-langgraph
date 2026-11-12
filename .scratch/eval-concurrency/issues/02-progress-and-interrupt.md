# 02: 进度与中断：按完成顺序写事件，主线程退出时停止

**What to build:** 评测开发者在并发运行时，每道题完成的那一刻就看到一条进度事件，某一题很慢也不会让进度停住；事件里能看出已经完成了几题。按 Ctrl+C 或主线程中途出错时，程序不再开始新题，等在途的请求结束后退出。

**Blocked by:** 01

**Status:** ready-for-agent

规格：`.scratch/eval-concurrency/spec.md`。结对工单（乒乓 TDD）：分工同工单 01，这次的概念是 `as_completed` 和并发里的 `try / finally`。本工单不做真实运行。

- [ ] `eval_case_finished` 按完成顺序写出：后提交的题先完成时，它的事件先出现（测试用 `threading.Event` 决定谁先完成，不用 `sleep`）
- [ ] 事件新增 `completed_cases`，从 1 递增到题目总数；`case_index` 仍是这题在题集里的位置
- [ ] 报告的 `records` 和 `config.retrieval` 仍按题集顺序（工单 01 的测试保持通过）
- [ ] 主线程因任何原因离开收集循环时置停止标志：主线程中途失败时，排队的题不再被调用
- [ ] Ctrl+C 走同一条置位路径，不单独模拟键盘中断
- [ ] 多道在途的题都失败时，抛出主线程最先看到的那个错误
- [ ] 事件里仍然没有问题原文

## Comments
