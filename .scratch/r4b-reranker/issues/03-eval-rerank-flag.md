# 03: 评测的 `--rerank` 开关

**What to build:** 评测开发者用 `eval retrieval --method <方法> --rerank` 跑一次带重排的检索评测。运行器、指标和 `top_k=20` 都不变，运行配置和报告能看出这次有没有重排、用的哪个 Rerank Profile。

**Blocked by:** 01, 02

**Status:** ready-for-agent

规格：`.scratch/r4b-reranker/spec.md`。本工单不做真实运行。

- [x] `evaluate_retrieval` 增加 `rerank`，默认关闭，传进每一道题的检索请求
- [x] `eval retrieval` 增加 `--rerank`，可与 `--method` 任意组合；不带时行为和输出与现在一样
- [x] 默认检索器在一次运行里只创建一个 Reranker，所有题共用；不开重排时不创建，不需要 Reranker 配置
- [x] 运行配置记录是否重排；报告的 `config.retrieval` 列出结果实际用到的 Rerank Profile 和带重排的配置版本
- [x] 某一题重排失败时中止整批运行，和现在 Embedding 失败时一样（运行器只对 `input_error` 按题计分，其他检索错误都中止）；不为重排新增处理
- [x] 假检索器测试覆盖：`rerank` 传进请求、写进运行配置和报告
- [x] 检索评测文档"怎么跑"一节补上 `--rerank` 的用法、费用提示和报告里的新字段

## Comments

- 实现（2026-11-12，Claude，TDD）：4617a92。`evaluate_retrieval` 和 `retrieval_run_config` 多了 `rerank`，`eval retrieval` 多了 `--rerank`；默认检索器在运行开始时按配置创建一个 Reranker，传给每一题的检索。先写失败测试再实现的有两个：`rerank` 传进每个请求并写进运行配置和报告；CLI 的 `--rerank`。
- 报告里比规格多写了一个字段：`config.rerank`（请求的开关），和已有的 `config.method` 对称。只看 `config.retrieval` 的话，整批题都没有检索结果时看不出这次有没有开重排。
- 运行配置的字段是白名单制（`runtime` 的 `CONFIG_FIELDS`），不在名单里的字段不会写进 `config.json`。把布尔值 `rerank` 加进了名单，和 R4 加 `method` 时的做法相同。
- "不带时行为和输出与现在一样"的实际情况：检索行为和指标不变；运行配置多了 `rerank: false`，报告多了 `config.rerank: false` 和 `rerank_profile_id: null`。这是"运行配置记录是否重排"带来的，报告版本号没有升。2026-11-10 的 hybrid 基线运行没有这三个字段，工单 04 读它时要按"没有重排"处理。
- 审查（两轴）：规范轴没有硬性违规，规格轴没有缺失的需求。按审查改了：
  - 补了"两道题收到同一个 Reranker"的测试（把 `retrieve_knowledge` 换成假的，不连数据库；做了改坏验证：默认检索器漏传 Reranker 时失败）。原来只有"开启重排而缺配置时在第一题之前失败"的测试，守不住"所有题共用"。
  - 文档三处措辞：每题"至多"调用一次 Reranker；`rerank` 和 `rerank_profile_id` 不开重排时也会写；旧运行缺的是三个字段。
- 没有改的审查意见：
  - 评测的 `TOP_K=20` 和重排的候选数 20 是两个常量，"重排只改变这 20 个 Hit 的顺序"依赖它们相等，没有测试守住。`TOP_K` 调大时请求会因"开启重排时 `top_k` 不超过 20"直接失败；调小时不会报错。两个数都是固定不调的，不加检查。
  - `method` 和 `rerank` 总是一起传：只有两个，不提前合并成一个类型。
  - 两个 CLI 测试有约 10 行相同的搭建代码，没有提成夹具。
  - "重排失败中止整批"的用例是手工构造的 `rerank_error`，没有经过真实的 Reranker 失败路径；运行器对所有非 `input_error` 的错误走同一个分支。
- 测试结果：`pytest tests --ignore=tests/integration` 462 passed，1 skipped，1 failed（`test_console_and_module_share_help`，`querypilot` 不在 PATH 上，与本工单无关）；`pytest tests/integration --require-postgres` 116 passed。审查后的改动只涉及运行器的测试文件和文档，单独重跑 `tests/test_retrieval_eval_runner.py` 12 passed。本工单没有真实调用。
