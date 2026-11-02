# 一次运行的记录放在哪里

以前，查询主要把答案写到终端，评测把结果和事件平铺到 results。现在，任务自己创建一个目录，配置、进度、结果和完成状态放在一起；旧 results 和知识数据不搬、不改。

~~~text
artifacts/
  runs/<operation>/<UTC日期>/<run_id>/
    run.json              安全状态与结果位置
    config.json           允许记录的配置、输入指纹、代码来源
    events.jsonl          安全进度
    result.json           安全摘要（使用摘要的任务）
    private/result.json   详细结果（使用详细结果的任务，二者只选一个）
    private/sql-audit.jsonl   实际使用SQL执行器时才创建
  logs/<UTC日期>/app.jsonl    普通应用和请求摘要
  validation/...          开发验收证据，不是业务任务
~~~

根目录可用 QUERYPILOT_ARTIFACTS_DIR 或命令的 --artifacts-dir 设置。它不替代知识快照的 --output-dir。快照目录仍只放原格式文件，不能把事件塞进去，否则严格核验会拒绝。

## 谁创建，谁关闭

完整查询、SQL执行、评测、知识修正、构建和存储操作均是任务。顶级调用创建 RunContext，内部步骤借用同一个上下文；创建者调用 complete 保存最终结果并结束。图编译、内部校验和普通加载不创建运行目录。独立校验命令由命令层创建；HTTP 只有 SQL 执行创建任务，健康、列表、结构请求只写日期日志。

每次运行用内部 UUID 和 UTC 日期定位，不用外部 request-id 拼路径。各运行有自己的 logger 和 handler，不临时替换全局处理器。命令可把安全进度同时接到 stderr；完整答案仍按原领域输出到 stdout。

## 操作成功和文件保存成功是两回事

run.json 分开保存 operation_outcome 与 artifact_outcome。例如，PG 已确认导入，但结果文件发布失败，应保留 imported 与产物失败。COMMIT 确认丢失只能记录 unknown，不能猜成成功或回滚。业务拒绝和日志失败同时发生时，业务错误优先。

运行先写 started，只有结果发布和终态保存均完成才是 completed。硬终止可能留下 started 或部分文件。读取端将其视为不完整，不替它补写成功。磁盘写失败时也不保证一定能留下完整错误报告。

结果采用同目录暂存、fsync 和排他发布，拒绝覆盖旧结果；只有本次认领目录里的 run.json 可以更新终态。read_run_result 只接受完整记录和固定的目录内结果位置，不回退到“最新一次”。

## 什么信息可以出现

| 位置 | 可以保存 | 默认禁止 |
| --- | --- | --- |
| run/config/events/app/stderr | 固定事件、状态、耗时、计数、安全错误类别、允许的配置与摘要 | 问题、SQL、Schema、结果行、知识正文、异常原文、凭据 |
| 私有结果 | 所需问题、SQL、逐题结果或完整领域数据 | API key、DSN、密码、秘密 URL |
| 私有 SQL 审计 | guard 判定及执行 SQL | 凭据；不传播到普通日志 |

事件格式器只读白名单元数据，不格式化任意异常或日志正文。模型地址配置去除用户信息、查询参数和片段；详细结果保留普通业务正文，清理凭据。文件被 Git 忽略不等于可以公开分享；公开评测导出仍可能含 predicted_sql，分享前需审查。

## 图与评测怎样关联

图日志来自框架 callback，使用 event_version=2。业务 run_id 放在 RunnableConfig.metadata，不写入图状态。框架 span_id/parent_span_id 表示本次调用树，和业务 run_id 是不同概念。

一次 graph 评测只有一个批次目录，各题图 ID 为“批次:case:序号”，节点保留 parent_run_id、question_id 和 case_index。日志只记录生命周期、状态和耗时，不记录图输入输出。它不能代替 checkpoint，也不能证明 SQL 回答正确。

字段和调用示例见[事件接口](observability-interface.md)，完整公开操作见[模块入口](modules.md)。本次真实验证和未验证范围见 [004 验证记录](../specs/004-modular-refactor/quickstart.md)。
