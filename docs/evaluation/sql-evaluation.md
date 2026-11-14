# SQL 评测

衡量的是"给一个问题，最后执行出来的结果对不对"。实现在 `src/querypilot/evaluation/sql/`，入口是 `querypilot eval direct` 和 `querypilot eval graph`。

## 题集

题目来自 [BIRD Mini-Dev](https://github.com/bird-bench/mini_dev)（SQLite 版）：

- **12 题开发集**：日常调试用，`evals/bird_smoke_manifest.json`；
- **冻结的 100 题**：最终验证用，`evals/bird_final100_manifest.json`。
- **两库 88 题**：toxicology（40 题）和 student_club（48 题）在 Mini-Dev 里的全部题，`evals/bird_toxicology_student_club_manifest.json`。知识库目前只有这两个库，所以检索接入查询图之后的对比（R5）都在这份题集上做；题号与[检索评测](retrieval-evaluation.md)的 88 题相同。其中 16 道属于冻结集，每题带 `frozen` 标记，评测的逐题记录里原样带上：冻结题一起计分，失败分析只看其余 72 道。

仓库里只提交 manifest，记录题号、来源 URL 和各文件的 sha256；原题和数据库放在 Git 忽略的 `data/` 下，用 `querypilot data bird ...` 命令准备。两库题集用 `querypilot data bird select-databases --database-id toxicology --database-id student_club --output evals/bird_toxicology_student_club.json` 生成：来源文件的 sha256 必须和冻结 manifest 记录的一致，题集文件留在本地，旁边的 manifest 提交。冻结题的使用规则见 [ADR 0001](../adr/0001-frozen-set-for-component-eval.md)。

## 两种跑法

| 命令 | 链路 | 说明 |
|---|---|---|
| `eval direct --provider model` | 问题加完整 Schema，直接让模型生成 SQL | 模型基线 |
| `eval direct --provider fixture` | 把金标准 SQL 当作预测 | **只用来验证评测管线**，分数不能当成模型能力 |
| `eval graph` | 走 LangGraph 四节点查询图：读结构 → 生成 → 安全检查 → 只读执行 | 和线上查询同一条链路 |

`eval graph --workers N` 同时跑 N 道题（默认 1）：工作线程里只跑查询图和金标准 SQL，结果比对、逐题记录和进度事件都在主线程，报告仍按题集顺序。单题出错照常记为 error 并继续；主线程出错或被 Ctrl+C 中断后不再开始新题。图评测每题的耗时从这道题开始跑图量到金标准 SQL 执行完，不含排队和结果比对；`workers` 大于 1 时会被同时进行的请求拉高，**耗时只在线程数相同的运行之间比较**；线程数记在运行配置和报告里。

`--evidence-mode oracle` 会把 BIRD 的人工提示（evidence）交给模型。这是隔离实验，不代表系统真实的能力，默认为 `none`。

## 计分

每道题只会落进一类：

- **matched**：预测 SQL 的执行结果和金标准一致。比较时忽略行的顺序，但保留重复行。
- **mismatched**：能执行，但结果不一致。
- **error**：没有生成预测、被安全检查拒绝，或者执行报错。

`failure = mismatched + error`。另外单独统计 `valid_sql`（能通过检查并执行的 SQL 数）和 `no_prediction`，它们是诊断用的计数，不能和上面三类直接相加。如果所有题都没有预测，执行匹配率记为空（`null`），不记成 0%。

**这是项目自己的"受行数上限影响的执行匹配"，不等于官方 BIRD EX。** 结果受执行上限影响，比较不同实验时必须固定行数上限、超时和字节上限。

## 报告与可追溯

- 报告记录题集 sha256、模型、提示词版本（`direct-v2`）、图版本（`single-v1`）、evidence 模式、执行上限、逐题结果，以及 P50/P95 耗时。
- **token 用量**取自模型接口的返回，不另外用 tokenizer 估算：逐题记录里的 `prompt_tokens`（提示词）和 `completion_tokens`（模型输出，推理模型的推理 token 也算在内），报告的 `token_usage` 给出两项各自的题数、总数、平均数和中位数。只对有值的题统计：fixture 不调用模型，没有用量；接口没返回用量的题记为空。
- 详细结果包含问题和预测 SQL，放在运行目录的 `private/` 下。`eval export-public` 按字段白名单导出一份可以分享的副本，但里面仍然有 `predicted_sql`，分享前要检查。
- 对比实验要固定题目、数据库快照、模型、提示词、检索配置和评测器，并保存逐题结果。只靠 12 题，不能支撑准确率结论。

## 已有结果

| 日期 | 设置 | 结果 |
|---|---|---|
| 2026-09 | direct，gpt-5.5，`direct-v2`，12 题开发集，无 evidence | 执行匹配 4/12（33%），12/12 都是有效 SQL |
| 2026-10-02 | graph，gpt-5.5，`single-v1`，冻结 100 题，无 evidence，行数上限 10000 / 超时 20s | **30 matched / 66 mismatched / 4 error，执行匹配 30%**，有效 SQL 96%，逐题 P50 14.1 秒、P95 54.4 秒 |

这两次都还没有接入检索：模型看到的是这个库的完整 Schema。检索接入查询图之后，会在同一组题上重新对比。
