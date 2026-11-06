# 运行记录与事件

每个任务（查询、评测、知识构建和导入等）自己创建一个运行目录，把配置、进度、结果和完成状态放在一起。

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

完整公开操作见[模块入口](modules.md)。

## 接口

公开接口从 querypilot.runtime.api 导入；内部文件名不属于跨模块契约。

| 接口 | 用法与结果 | 错误/副作用 |
| --- | --- | --- |
| task_run(operation, settings=None, *, run_context=None, command="", config=None, private=False, secrets=()) | 上下文管理器，返回 RunContext；借用已有上下文时不结束它 | 顶级认领运行目录；退出时未 complete 不能变成 completed |
| RunContext.complete(result, *, outcome="succeeded") | 保存领域结果并记录终态 | 产物失败抛 RunArtifactError，保留已知操作结果 |
| ctx.event(event, **fields) | 记录固定事件和允许字段 | 不接受任意正文作为日志策略 |
| ctx.attach_stream(stream) | 增加本次运行的安全进度输出，如 stderr | 不关闭调用者的流，不替换全局 logger |
| ctx.graph_middleware(*, row_count=None) | 创建本次运行持有的 GraphLogMiddleware | metadata 关联，不改变图状态 |
| GraphLogMiddleware.config(run_id=..., database_id=..., parent_run_id=..., question_id=..., case_index=...) | 返回 RunnableConfig | 只记录安全元数据和生命周期 |
| read_run_result(path) | 从完整运行目录读取领域结果 | incomplete_run/invalid_run；不遍历最新目录 |
| publish_json(path, value) | 排他发布 JSON | 旧文件不覆盖，失败向调用者报告 |
| sanitize_private(value, *, secrets=()) | 转成可序列化结构并清理凭据 | 保留普通领域字段，不是公开发布器 |
| log_application(event, *, settings=None, secrets=(), **fields) | 写 UTC 日期应用摘要 | 不保存任意异常正文 |
| source_version(start=None) | 代码 revision、dirty、摘要及来源 | 没有 Git 时明确 unavailable，不猜源码层级 |

普通调用优先使用任务 API，它们自动持有上下文。需要组合多项内部步骤时才显式借用：

~~~python
from querypilot.runtime.api import task_run
from querypilot.querying.api import ask

# settings 和 fixed_generator 由调用方提供；此例不会主动配置真实模型。
with task_run("query", settings, command="example", private=True) as context:
    result = ask("demo", "统计条数", settings,
                 generator=fixed_generator, run_context=context)
    context.complete(result)
~~~

operation 固定为 query/evaluation/knowledge_catalog/knowledge_corpus/knowledge_import/knowledge_verify/knowledge_manage/data_prepare。command 是固定命令名，不包含用户问题或完整参数串。

### 安全运行记录

run.json 的 format_version 为 querypilot-run-v1，保存 run_id、operation、command、status、operation_outcome、artifact_outcome、started_at、finished_at、result_path、error_type。只保存安全状态和相对指针，详细结果单独写出。

operation_outcome 有 not_started/unknown/succeeded/imported/reused/verified/failed；artifact_outcome 独立表达产物状况。completed 必须有有效结果，消费者不能只看目录存在就认定完成。

### 事件字段

事件为逐行 JSON，time 使用 UTC。event 取固定名称。图回调事件带 event_version=2；普通任务事件不冒称图事件版本。

| 字段 | 含义 |
| --- | --- |
| run_id / parent_run_id / graph_run_id | 业务任务、上层批次和单题图关联 |
| span_id / parent_span_id / span_name | 框架调用树，不能当 checkpoint |
| operation / component / node | 操作类别、组件和节点 |
| status / operation_outcome / artifact_outcome | 生命周期和两类结果 |
| elapsed_ms / row_count | 耗时和计数，不含结果行 |
| question_id / case_index | 评测题号和从 1 开始的序号 |
| completed_cases / total_cases / success_count / failure_count / eta_seconds | 评测进度 |
| provider / evidence_mode | fixture/model、none/oracle 的既有含义 |
| request_id / database_id / error_type | 安全关联和错误类别；不记录异常消息 |

主要事件包括 run_started/run_finished/run_failed、graph_span_started/graph_span_finished、eval_started/eval_case_started/eval_case_finished/eval_finished，以及知识构建、导入和复验阶段。未知日志消息会变成受控事件，不能借白名单之外的字段塞入正文。

私有 SQL 审计可以额外含 sql/decision。审计端独立、不传播；进度端永远不输出这两个字段。

事件可能因硬终止缺少结束记录，没有跨进程全局顺序保证。运行记录不是分布式追踪或断点续跑；模型能力和数据库真实提交仍需相应实验验证。
