# QueryPilot

一个用自然语言查询业务数据的 Text-to-SQL 助手，使用 Python + LangGraph。用户用一句话提问，系统检索数据库的结构知识，生成 SQL，经过安全检查后只读执行，返回结果。

这是一个求职作品。重点不在功能多，而在把每个环节**做对、做得可度量**：知识从哪里来、检索好不好、生成的 SQL 能不能安全执行，都有对应的测试或评测来回答。

## 现在能做什么

| 能力 | 状态 | 说明 |
|---|---|---|
| 查询图 | ✅ | LangGraph 四节点：读结构 → 生成 SQL → AST 安全检查 → 只读执行 |
| SQL 安全 | ✅ | 单条 SELECT、表范围检查、强制 LIMIT、`mode=ro` + authorizer、超时与结果大小上限 |
| 知识库 | ✅ | 从 SQLite 结构和 BIRD 字段说明生成带中文语义的知识块；修正须经批准；快照身份确定且不可变 |
| 知识存储 | ✅ | PostgreSQL 分角色存储，导入后逐字段读回核对，限定 Scope 的只读会话 |
| 关键词检索 | ✅ | pg_search BM25，中文用 jieba 分词 |
| 检索评测 | ✅ | 88 题检索题集，按 Hit 计的召回指标，BM25 基线和失败分析 |
| SQL 评测 | ✅ | 12 题开发集 + 冻结的 100 题，执行结果匹配 |
| 向量与混合检索 | ⏳ 下一项 | 见[路线图](docs/roadmap.md) |
| 检索接入查询图、选库、SQL 修复、多轮记忆 | 规划中 | 见[路线图](docs/roadmap.md) |

## 几个数字

| 评测 | 条件 | 结果 |
|---|---|---|
| 检索（BM25） | toxicology + student_club 共 88 题，top_k=20 | 前 10 个 Hit 拿全相关表：**84.1%**；前 5 个：**64.8%**；全覆盖所需 Hit 数的中位数为 **3** |
| SQL（未接检索） | 冻结 100 题，gpt-5.5，模型看到完整 Schema | 执行结果匹配 **30%**（项目口径，不等于官方 BIRD EX） |

指标怎么定义、为什么这样设计、失败的原因有哪些，见[检索评测](docs/evaluation/retrieval-evaluation.md)。

## 值得一看的地方

1. **检索评测的设计**：K 以 Hit 计，和检索参数 top_k 同一单位；用"至少需要多少个 Hit"（Hits to Cover）直接回答 top_k 设多少；区分"记 0"和"记空"；把失败归成五类，并对应到各自能解决它的技术。→ [docs/evaluation/retrieval-evaluation.md](docs/evaluation/retrieval-evaluation.md)
2. **知识不泄题**：评测题目、金标准 SQL 和 evidence 都不能进入知识库，构建配置里写死了这些开关；冻结题只用来评测、不用来调参（[ADR 0001](docs/adr/0001-frozen-set-for-component-eval.md)）。
3. **可复现的知识快照**：同样的输入得到同样的 `corpus_id`，资料修正必须经过批准、并且和内容 hash 绑定。→ [docs/knowledge/knowledge-base.md](docs/knowledge/knowledge-base.md)
4. **不信任模型输出的 SQL**：三层检查之后才执行。→ [docs/architecture/security.md](docs/architecture/security.md)
5. **工程边界**：模块只能通过 `api.py` 互相调用，由架构测试守住依赖方向；日志和报告里不出现问题原文、SQL 和凭据。→ [docs/architecture/](docs/architecture/)

## 架构

```text
src/querypilot/
  entrypoints/      CLI 和 HTTP
  querying/         LangGraph 查询图
  sql_generation/   提示词、模型调用
  databases/        数据库登记、结构读取、SQL 安全检查、只读执行
  knowledge/        知识准备（catalog → corpus）和 PostgreSQL 存储
  retrieval/        BM25 检索 retrieve_knowledge
  evaluation/       SQL 评测、检索评测、BIRD 数据准备
  runtime/          配置、运行记录、安全事件
```

各模块的公开入口见 [docs/architecture/modules.md](docs/architecture/modules.md)，全部文档见 [docs/README.md](docs/README.md)。

## 快速开始

需要 Python 3.11 或 3.12，以及 [uv](https://docs.astral.sh/uv/)。

```powershell
uv sync --frozen --extra dev --extra knowledge
uv run querypilot --help

# 单元测试（不需要数据库和模型）
uv run pytest tests --ignore=tests/integration -q
```

把 `.env.example` 复制为 `.env` 再填写配置。各功能需要的准备：

- **SQL 查询和 SQL 评测**：BIRD Mini-Dev 的 SQLite 库，用 `querypilot data bird ...` 准备，以及一个 OpenAI 兼容的模型服务。`eval direct --provider fixture` 不调用模型，只验证评测管线。
- **知识库和检索**：PostgreSQL 18 + pg_search 0.26.4（`compose.yaml`，安装说明见 [docs/operations/pg-search.md](docs/operations/pg-search.md)），以及 `.env` 里三个角色的 DSN。
- **集成测试**：`uv run pytest tests/integration --require-postgres`。

原始数据集、知识快照和运行记录都在 Git 忽略的 `data/` 和 `artifacts/` 下，仓库里只提交 manifest。

## 开发方式

先用具体例子对齐需求，再写一份短规格并拆成工单（在 `.scratch/` 下），然后一次一个测试地做测试驱动开发，最后从"是否符合规范"和"是否符合规格"两个方向审查。术语统一记在 [GLOSSARY.md](GLOSSARY.md)，难以从代码看出的决定记成 [ADR](docs/adr/)。

Agent 的核心逻辑（检索与计分规则、图的状态与节点、SQL 生成与修复等）由我和 AI 结对编写：AI 先写一个会失败的测试，我写实现让它通过；其余工程部分由 AI 按测试驱动完成，我负责审查。协作约定见 [AGENTS.md](AGENTS.md)。

## 数据与出处

评测数据来自 [BIRD Mini-Dev](https://github.com/bird-bench/mini_dev)（CC BY-SA 4.0）。需求参考了 [data-agent-tutorial](https://github.com/qifan777/data-agent-tutorial)，但没有复制它的代码和提示词。详见 [docs/attribution.md](docs/attribution.md)。
