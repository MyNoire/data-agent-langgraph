# QueryPilot

QueryPilot's target is a multi-turn natural-language business data assistant: select an authorized data source, retrieve business and schema knowledge, plan and safely execute analysis, recover from errors, and produce a traceable report. The current Phase 0 implementation is a measurable Text2SQL prototype, not the finished Agent. See the [product and evaluation roadmap](docs/product-evaluation-roadmap.md) for the staged architecture and experiment protocol.

The [architecture diagram](docs/querypilot-architecture.drawio.png) shows the current Phase 0 path alongside the planned LangGraph system. Its [editable draw.io source](docs/querypilot-architecture.drawio) is also included.

The first real-model BIRD smoke run is recorded in the [Phase 0.5 baseline report](docs/phase-0.5-baseline.md). It is a 12-question development result, not official BIRD EX. A separate, frozen 100-question set is available for later model comparisons; no real-model score has been reported for that set.

The foundation uses Python 3.11/3.12, uv, FastAPI, Pydantic v2, sqlglot, and SQLite. The model client accepts an OpenAI-compatible URL and model name, including a locally served Qwen model. Phase 1 now has a single linear LangGraph path; PostgreSQL and the larger agent architecture remain planned.

## Local setup

```powershell
cd D:\AgentStudy\data-agent-langgraph
uv sync --extra dev
uv run uvicorn querypilot.api:app --host 127.0.0.1 --port 8001
```

Health: `GET http://127.0.0.1:8001/health`. After importing the BIRD databases below, `GET /databases/formula_1/schema` returns tables and columns. `POST /queries` accepts `{"database_id":"formula_1","sql":"SELECT forename FROM drivers LIMIT 5"}`. `POST /databases` also accepts a `database_id` and a SQLite `path` inside `QUERYPILOT_DATA_DIR`.

## Tests and evaluation

```powershell
uv run pytest -q
uv run python scripts/run_eval.py --provider fixture --cases evals/bird_smoke.json
```

The fixture command needs the generated BIRD case file and imported databases described below.

The fixture provider feeds gold SQL back into the runner and only validates data loading, guard, execution, metrics serialization, and latency collection. **Its score is not a Text2SQL baseline.** To run the actual direct-prompt baseline, set `QUERYPILOT_MODEL_BASE_URL`, `QUERYPILOT_MODEL_NAME`, and `QUERYPILOT_MODEL_API_KEY` for an available OpenAI-compatible server, then run:

Create a local `.env` from `.env.example` and fill in your own credentials. For a third-party Chat Completions endpoint serving GPT-5.5, set the model name to `gpt-5.5` (without LangChain's `openai:` prefix). `QUERYPILOT_MODEL_TEMPERATURE` is optional and omitted by default because some reasoning endpoints reject it. Do not commit `.env` or place credentials in `.env.example`.

```powershell
uv run python scripts/run_eval.py --provider model --cases evals/bird_smoke.json
```

The runner records prediction-level SQL, errors, execution match, valid execution rate, and P50/P95 latency. Its JSON and terminal output contain a `summary` with `total`, `success`, `failure`, `mismatch`, `error`, `no_prediction`, and `valid_sql` counts. Success means a capped-row execution match; failure combines mismatches and errors, with no-prediction cases included under errors. Each record has an `outcome` and, for BIRD, a `question_id` for follow-up analysis. Current execution matching is a smoke metric, not official BIRD EX. The 12 questions are for quick iteration; keep the 100-question set separate for less frequent comparisons.

Without `--output`, files are named `results/<dataset>__model-<model>__<prompt>__evidence-<mode>__<UTC timestamp>.json` or `results/<dataset>__fixture__<UTC timestamp>.json`. The command prints the actual `output_file`. An explicit `--output` is allowed but refuses to replace an existing file. Raw results stay Git-ignored; the public exporter can copy selected fields into a separately named `*-public.json`.

Single-graph and evaluation commands emit live progress events to standard error and an ignored JSONL file. Progress events have a `run_id` and omit questions, SQL, Schema, rows, and credentials; separate SQL audit logs can contain SQL. See the [logging design](docs/observability.md) and [event interface](docs/observability-interface.md).

## Phase 1: first LangGraph path

The first graph requires a registered `database_id` and question. Its four nodes load the current SQLite schema, generate SQL with the existing model adapter, validate the SQL AST, and execute it through the existing read-only executor. It returns the guarded SQL and query result. The graph has no router, retrieval, repair, checkpointer, or memory yet.

```powershell
uv run python scripts/ask_graph.py --database-id formula_1 --question "What's Bruno Senna's Q1 result in the qualifying race No. 354?"
uv run pytest -q tests/test_single_graph.py
```

The implementation is in [single.py](src/querypilot/graph/single.py), with a deterministic SQLite-backed test that replaces only the model call. See the [architecture notes](docs/architecture.md) for the state and node boundaries.

If you want to type this workflow yourself from the Phase 0.5 Git snapshot, follow the [Phase 1 hands-on guide](docs/phase-1-handson.md).

`evidence` is an optional hint about business meaning or values that may not be clear from the question and table names. BIRD Mini-Dev includes expert-annotated evidence in its question records. The model baseline defaults to `--evidence-mode none`: the prompt contains only the question and schema. To measure a separate, oracle-assisted setting, pass `--evidence-mode oracle`; this includes the case's annotated evidence. Each result records `evidence_mode` and whether evidence was used for each question. Keep the two settings separate when reporting scores. Phase 0 does not retrieve evidence on its own.

## BIRD Mini-Dev preparation

See [BIRD Mini-Dev](https://github.com/bird-bench/mini_dev) and its [canonical dataset page](https://huggingface.co/datasets/birdsql/bird_mini_dev). The small SQLite question JSON and the three databases needed by this smoke set are available locally under ignored `data/bird/`; their SHA-256 values, the question source revision, and 12 fixed question IDs are recorded in [the smoke manifest](evals/bird_smoke_manifest.json). The selected cases cover three databases (four each) and simple/moderate/challenging difficulty (4/6/2). `--questions-only` prepares `evals/bird_smoke.json` without requiring SQLite databases on a fresh checkout:

```powershell
uv run python scripts/import_bird.py --root data/bird --manifest evals/bird_smoke_manifest.json --questions-only --output evals/bird_smoke.json
```

On a fresh checkout, obtain the [complete database package linked by BIRD](https://github.com/bird-bench/mini_dev), save the archive under `data/`, and independently record its SHA-256. Extracting an archive is explicit and checksum-verified:

```powershell
uv run python scripts/fetch_bird.py --archive data/mini_dev_download.zip --sha256 "<64-hex-sha256>"
uv run python scripts/import_bird.py --root data/bird --manifest evals/bird_smoke_manifest.json --output evals/bird_smoke.json
uv run python scripts/run_eval.py --provider model --cases evals/bird_smoke.json
# Optional assisted comparison using expert annotations:
uv run python scripts/run_eval.py --provider model --evidence-mode oracle --cases evals/bird_smoke.json
```

The importer accepts `mini_dev_sqlite.json` or the Hugging Face shard filename, plus `<db_id>.sqlite` for each selected database. The manifest prevents accidentally evaluating a different question revision. The database files are never modified or bundled in Git. This is a fixed development smoke set, not the official BIRD EX evaluation; the current runner caps result rows and uses simplified execution matching. [LiveSQLBench Base Lite SQLite](https://huggingface.co/datasets/birdsql/livesqlbench-base-lite-sqlite) is a separate benchmark whose public release omits gold SQL and includes management tasks, so it is not used in this read-only baseline. Dataset terms and project provenance are in [attribution](docs/attribution.md).

## Frozen 100-question set

The [100-question manifest](evals/bird_final100_manifest.json) fixes the source checksum, question IDs, 11 database checksums, and preflight limits. It excludes all 12 smoke IDs. The selection rules are in `scripts/select_bird_final100.py`; `scripts/freeze_bird_final100.py` validates reference SQL before writing a new manifest and refuses to overwrite the committed one. On a fresh checkout, obtain the same BIRD Mini-Dev question JSON and complete `dev_databases` directory, then run:

```powershell
uv run python scripts/prepare_bird_final100_data.py --database-source "D:\path\to\dev_databases"
uv run python scripts/import_bird.py --root data/bird --manifest evals/bird_final100_manifest.json --output evals/bird_final100.json
$env:QUERYPILOT_MAX_ROWS = '10000'
$env:QUERYPILOT_QUERY_TIMEOUT_MS = '20000'
$env:QUERYPILOT_MAX_RESPONSE_BYTES = '10485760'
uv run python scripts/run_eval.py --provider fixture --cases evals/bird_final100.json
```

The preparation script verifies checksums before copying databases into ignored `data/bird/`. Fixture results verify the evaluation path; they are not model accuracy. Before a model comparison, keep the frozen IDs and `--evidence-mode` fixed, and record the model, prompt, execution caps, and raw result file. The three environment values above match the manifest preflight for this set.

When ready for the first real-model 100-question baseline, use the same three execution limits and run `uv run python scripts/run_eval.py --provider model --evidence-mode none --cases evals/bird_final100.json`. This command invokes the configured model 100 times; it has not been run for this set yet.

## Single-graph evaluation with node progress

`scripts/run_graph_eval.py` evaluates the same frozen cases through the four-node LangGraph workflow. It is a separate run from `scripts/run_eval.py`'s direct-prompt baseline. Every case has its own graph `run_id`; node events carry the batch `parent_run_id`, `question_id`, and `case_index`. The result JSON includes per-case outcomes and a summary. Progress is printed to standard error and saved in a matching `.events.jsonl` file. No model calls are made until you run one of the following commands yourself.

From the project root, first check the graph path on the 12-question smoke set, then run the frozen 100-question set. The three limits match the 100-question manifest and should be kept fixed for comparisons:

```powershell
$env:QUERYPILOT_MAX_ROWS = '10000'
$env:QUERYPILOT_QUERY_TIMEOUT_MS = '20000'
$env:QUERYPILOT_MAX_RESPONSE_BYTES = '10485760'
uv run python scripts/run_graph_eval.py --cases evals/bird_smoke.json
uv run python scripts/run_graph_eval.py --cases evals/bird_final100.json
```

The graph uses the same direct prompt without oracle evidence, then validates and executes SQL in separate nodes. The reported execution match is this project's capped-row smoke metric, not official BIRD EX.

The first 100-question graph model run completed on 2026-10-02 using the configured `gpt-5.5` model, `direct-v2`, `single-v1`, and no evidence or RAG. It recorded 30 matched cases, 66 mismatches, and 4 errors (70 failures in total). Execution match was 30%; `valid_sql_rate` was 96% (successful graph execution, not syntax validation alone). Per-case evaluation latency was P50 14.063 s and P95 54.421 s, including reference SQL execution and result comparison. Execution limits were 10,000 rows, 20,000 ms per SQL execution, and 10 MiB per response; model temperature was not explicitly set.

The local raw report is `results/bird-final100__graph-single-v1__model-gpt-5-5__direct-v2__evidence-none__20261002T073342236010Z.json` (SHA-256: `d4293131528a4723112bca18e2a9feeb0e8bdc39554186d8cb85c62ef96cb70c`). Its paired `.events.jsonl` contains progress events. Raw reports and event logs remain ignored by Git; retain both local files for later RAG comparisons. The evaluated case file has SHA-256 `e29ab5ed86a793884bcff26e5e602ed56efda019bf95b4bd7f9f4a16762c4b8f`.

Both evaluation paths share the [result contract and summary rules](docs/evaluation-results.md), while keeping their generation and execution flows separate.
For a step-by-step explanation of the single-graph evaluator, see the [learning guide](docs/learning/single-graph-evaluation.md).

## Scope

Implemented: configurable OpenAI-compatible direct prompt, registered local SQLite data sources, AST guard, read-only execution, API health/schema/query endpoints, JSON logging, tests, smoke evaluation, and one linear LangGraph workflow. Pending: durable checkpoints, PostgreSQL/pgvector, retrieval, SQL repair, multi-turn context/memory, Python analysis, SSE, and production authentication.
