# QueryPilot

QueryPilot 的目标是让业务人员用自然语言查询统计数据，并能追溯答案依据。当前已实现一条四节点查询链：读 SQLite 结构 → 生成 SQL → 安全校验 → 只读执行。它还没有选库、SQL 修复、多轮记忆或完整在线检索。

知识准备面向开发者：从结构和批准说明生成知识快照，再导入 PostgreSQL。目录和调用方式见[模块入口](docs/modules.md)；本次重构保留业务行为和评测规则，R3 的匹配、排名等核心仍待结对完成。

## 本地启动

需要 Python 3.11/3.12 和 uv。从项目根运行：

~~~powershell
uv sync --frozen --extra dev
uv run querypilot --help
uv run python -m querypilot --help
uv run uvicorn querypilot.entrypoints.http:app --host 127.0.0.1 --port 8001
~~~

两种 CLI 调用同一个 main。HTTP 保留原接口：GET /health、GET/POST /databases、GET /databases/{id}/schema、POST /queries。最后一个接口接收 SQL，例如：

~~~json
{"database_id":"formula_1","sql":"SELECT forename FROM drivers LIMIT 5"}
~~~

数据库必须先登记，文件位于 QUERYPILOT_DATA_DIR 内。执行器保留单语句、表范围、只读连接、SQLite authorizer、行数、超时和响应字节限制。

复制 .env.example 为自己的 .env，填配置但不要提交凭据。普通 SQLite、HTTP、固定生成器和 fixture 评测不需要 PostgreSQL。知识存储另需 knowledge extra 和对应角色 DSN。

## 现在怎样运行和找结果

~~~powershell
uv run querypilot eval direct --provider fixture --cases evals/bird_smoke.json
uv run querypilot --artifacts-dir artifacts eval direct --provider fixture --cases evals/bird_smoke.json
~~~

这些命令需要已准备的题集及登记的数据库；克隆代码不会取得本地忽略的数据。fixture 使用金标准 SQL，只检查执行与计分管线，不能作为模型成绩。

任务统一归档到 artifacts/runs/<操作>/<UTC日期>/<run_id>/，其中有 run.json、config.json、events.jsonl 和领域结果。普通请求摘要到 artifacts/logs/<UTC日期>/app.jsonl。CLI 的 stderr 报告运行位置。设置 QUERYPILOT_ARTIFACTS_DIR 或 --artifacts-dir 可以换根目录。

旧 results 和 data 留在原位置。评测和导入不再接受旧式平铺 --output；知识快照等持久输出仍显式用 --output-dir。公开报告导出保留 --output，拒绝覆盖：

~~~powershell
uv run querypilot eval export-public --input <旧JSON或完整评测运行目录> --output <新的-public.json>
~~~

导出仍可能含 predicted_sql，分享前检查；命令不会上传。安全进度不输出问题、SQL、Schema、结果行和凭据；详细结果与 SQL 审计位于私有位置。记录故障和真实操作结果分开，数据库成功后写文件失败不会被说成回滚。见[运行记录](docs/observability.md)和[事件接口](docs/observability-interface.md)。

## 自然语言查询与评测

以下命令会调用配置的模型，需明确准备模型地址、名称、密钥和成本预算后自行执行：

~~~powershell
uv run querypilot query ask --database-id formula_1 --question "列出一位车手"
uv run querypilot eval direct --provider model --evidence-mode none --cases evals/bird_smoke.json
uv run querypilot eval graph --cases evals/bird_smoke.json
~~~

模型适配器保留 direct-v2、单 user 消息、60 秒客户端超时；温度未配置时不发送。direct 的 --evidence-mode oracle 是使用人工标注提示的隔离实验，不能混同真实检索能力。graph 保留 single-v1 的四节点和无 oracle 流程。

计分保留 matched/mismatched/error；failure=mismatch+error，未预测计入 error。执行匹配忽略行序但保留重复行，受项目行数上限影响，不等于官方 BIRD EX。详见[结果规则](docs/evaluation-results.md)。

SQL 评测实现集中在 `evaluation/sql`，调用方继续使用 `evaluation.api.run_direct/run_graph`；根入口直接导出同一个操作。`evaluation/retrieval` 单独保留检索评测职责，目前只有待实现说明，没有评分或可运行命令。SQL 的运行配置记录 `mode=sql`，两类评测不会混用样例、指标或报告。

冻结的 100 题与 12 题开发集分开。正式比较需固定题号、数据库、模型、提示词、evidence、执行上限及评测器。100 题原设定：

~~~powershell
$env:QUERYPILOT_MAX_ROWS = '10000'
$env:QUERYPILOT_QUERY_TIMEOUT_MS = '20000'
$env:QUERYPILOT_MAX_RESPONSE_BYTES = '10485760'
~~~

历史真实模型 graph 基线（2026-10-02）：gpt-5.5、direct-v2、single-v1、none，30 matched / 66 mismatched / 4 errors，执行匹配 30%，valid_sql_rate 96%，逐题 P50 14.063 秒、P95 54.421 秒。该耗时包含参考 SQL 和比较；不是本次重构重新测量。原始报告 SHA256 为 d4293131528a4723112bca18e2a9feeb0e8bdc39554186d8cb85c62ef96cb70c，保留在本地 results。见[工作计划](docs/learning/rag-work-plan.md)。

## 知识准备与存储

目录按业务分成一个 knowledge 大模块，内部再分 knowledge_catalog、knowledge_corpus、knowledge_store。普通调用只导入 knowledge.api；底层内部函数和裸 PG 连接不跨模块使用。

具备已批准 Toxicology 输入包后，构建/核验可使用：

~~~powershell
uv run querypilot knowledge corpus build --database-id toxicology --curated-dir data/rag/toxicology/r1-2-curated-v1 --semantic-file config/knowledge_semantics/toxicology-v1.json --output-dir data/rag/toxicology/r1-4-corpus
uv run querypilot knowledge corpus verify --corpus-dir data/rag/toxicology/r1-4-corpus
~~~

同一批准输入与语义配置保留同一 corpus 身份；已有目标合法且内容相同时只读复用，损坏或不同则拒绝。来源变化时构建新快照；没有后台巡检。运行记录不写进快照目录。

~~~powershell
uv sync --frozen --extra dev --extra knowledge
uv run querypilot knowledge store check
uv run querypilot knowledge store import --corpus-dir data/rag/toxicology/r1-4-corpus
uv run querypilot knowledge store verify --corpus-dir data/rag/toxicology/r1-4-corpus
~~~

存储操作选中后才加载驱动。迁移、导入与读取分角色；导入前核验源、事务锁防同 corpus 竞争、重复导入全字段核对而不覆盖。COMMIT 确认丢失保留未知结果，不猜成功。向量生成、在线 RAG 和检索效果仍未实现。

R1.4 的正式 Toxicology 快照为 4 文档/20 块/5 关系。R2 历史实库验收（2026-10-17）使用 PG18.6/pgvector0.8.7，26 项实库通过，重启后内容与首次时间指纹保持；这是历史事实，不替代本次新入口验收。原资料和安装步骤见 [R1.4](specs/001-r1-4-corpus/quickstart.md)、[R2](specs/002-r2-postgres-import/quickstart.md)。

## BIRD 数据工具

18 个旧脚本均有[对应新命令](specs/004-modular-refactor/contracts/cli.md)。数据工具保留原参数和保护：

~~~powershell
uv run querypilot data bird fetch --help
uv run querypilot data bird import --root data/bird --manifest evals/bird_smoke_manifest.json --questions-only --output <新题集.json>
uv run querypilot data bird prepare-final100 --database-source <本地dev_databases目录>
~~~

fetch 需显式 URL/归档与 SHA256。import 使用 manifest 固定问题版本；prepare 按已冻结 manifest 核对数据库。select-final100/freeze-final100 是维护工具，不应在每次评测时重选题目。本次只迁移和测试原创临时资料，没有下载或改正式数据。

来源为 [BIRD Mini-Dev](https://github.com/bird-bench/mini_dev) 和 [数据页](https://huggingface.co/datasets/birdsql/bird_mini_dev)，出处见[attribution](docs/attribution.md)。12 题及 100 题的 manifest 保留在 evals，原始题目和数据库在忽略的 data。

## 开发、测试与本次验证

~~~powershell
uv run --extra dev --extra knowledge pytest tests --ignore=tests/integration -q
uv run --extra dev python -X utf8 tests/architecture/test_module_boundaries.py
~~~

专用 PG 测试需三条 TEST DSN，必须指向 querypilot_knowledge_test；夹具会清理这个库里的测试行，不能指向正式知识库。真实模型评测不是默认测试的一部分。004 的历史模块化工作见其[验证指南](specs/004-modular-refactor/quickstart.md)和[改动与审查](specs/004-modular-refactor/review.md)；本轮 R3 先行重构的命令、结果与限制见 [R3 验证指南](specs/003-r3-lexical-retrieval/quickstart.md)及[审查记录](specs/003-r3-lexical-retrieval/review.md)。

协作采用先对齐、spec、plan、tasks、implement、独立 review，见[SDD 流程](specs/README.md)与 [AGENTS.md](AGENTS.md)。历史教程保留当时实现记录；现行导入和命令以[模块入口](docs/modules.md)为准。R3 不因这次结构调整而勾选未完成核心。

尚未完成：完整检索与排名、embedding、路由/授权、多轮上下文、SQL 修复、持久 checkpoint、Python 分析、SSE 与生产认证。
