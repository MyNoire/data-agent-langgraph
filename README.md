# QueryPilot

QueryPilot's target is a multi-turn natural-language business data assistant: select an authorized data source, retrieve business and schema knowledge, plan and safely execute analysis, recover from errors, and produce a traceable report. The current Phase 0 implementation is a measurable Text2SQL prototype, not the finished Agent. See the [product and evaluation roadmap](docs/product-evaluation-roadmap.md) for the staged architecture and experiment protocol.

The [architecture diagram](docs/querypilot-architecture.drawio.png) shows the current Phase 0 path alongside the planned LangGraph system. Its [editable draw.io source](docs/querypilot-architecture.drawio) is also included.

The first real-model BIRD smoke run is recorded in the [Phase 0.5 baseline report](docs/phase-0.5-baseline.md). It is a 12-question development result, not official BIRD EX.

The foundation uses Python 3.11/3.12, uv, FastAPI, Pydantic v2, sqlglot, and SQLite. The model client accepts an OpenAI-compatible URL and model name, including a locally served Qwen model. Phase 1 now has a single linear LangGraph path; PostgreSQL and the larger agent architecture remain planned.

## Local setup

```powershell
cd D:\AgentStudy\data-agent-langgraph
uv sync --extra dev
uv run python scripts/create_demo.py
uv run uvicorn querypilot.api:app --host 127.0.0.1 --port 8001
```

Health: `GET http://127.0.0.1:8001/health`. `POST /databases` accepts `{"database_id":"demo","path":"D:\\AgentStudy\\data-agent-langgraph\\data\\demo.sqlite"}`. `GET /databases/demo/schema` returns tables and columns. `POST /queries` accepts `{"database_id":"demo","sql":"SELECT name FROM customers"}`. Database files must be inside `QUERYPILOT_DATA_DIR`.

## Tests and evaluation

```powershell
uv run pytest -q
uv run python scripts/run_eval.py --provider fixture --cases evals/demo_smoke.json --output results/fixture.json
```

The fixture provider feeds gold SQL back into the runner and only validates data loading, guard, execution, metrics serialization, and latency collection. **Its score is not a Text2SQL baseline.** To run the actual direct-prompt baseline, set `QUERYPILOT_MODEL_BASE_URL`, `QUERYPILOT_MODEL_NAME`, and `QUERYPILOT_MODEL_API_KEY` for an available OpenAI-compatible server, then run:

Create a local `.env` from `.env.example` and fill in your own credentials. For a third-party Chat Completions endpoint serving GPT-5.5, set the model name to `gpt-5.5` (without LangChain's `openai:` prefix). `QUERYPILOT_MODEL_TEMPERATURE` is optional and omitted by default because some reasoning endpoints reject it. Do not commit `.env` or place credentials in `.env.example`.

```powershell
uv run python scripts/run_eval.py --provider model --cases evals/demo_smoke.json --output results/direct-demo.json
```

The runner records prediction-level SQL, errors, execution match, valid execution rate, and P50/P95 latency. The demo set is four original examples, not BIRD. Current execution matching is a smoke metric under the configured row cap; it is not the official BIRD evaluator.

## Phase 1: first LangGraph path

The first graph requires a registered `database_id` and question. Its four nodes load the current SQLite schema, generate SQL with the existing model adapter, validate the SQL AST, and execute it through the existing read-only executor. It returns the guarded SQL and query result. The graph has no router, retrieval, repair, checkpointer, or memory yet.

```powershell
uv run python scripts/create_demo.py
uv run python scripts/ask_graph.py --database-id demo --question "How many customers are in the East region?"
uv run pytest -q tests/test_single_graph.py
```

The implementation is in [single.py](src/querypilot/graph/single.py), with a deterministic SQLite-backed test that replaces only the model call. See the [architecture notes](docs/architecture.md) for the state and node boundaries.

If you want to type this workflow yourself from the Phase 0.5 Git snapshot, follow the [Phase 1 hands-on guide](docs/phase-1-handson.md).

`evidence` is an optional hint about business meaning or values that may not be clear from the question and table names. The demo set has no `evidence`. BIRD Mini-Dev includes expert-annotated evidence in its question records. The model baseline now defaults to `--evidence-mode none`: the prompt contains only the question and Schema. To measure a separate, oracle-assisted setting, pass `--evidence-mode oracle`; this includes the case's annotated evidence. Each result records `evidence_mode` and whether evidence was used for each question. Keep the two settings separate when reporting scores. Phase 0 does not retrieve evidence on its own.

## BIRD Mini-Dev preparation

See [BIRD Mini-Dev](https://github.com/bird-bench/mini_dev) and its [canonical dataset page](https://huggingface.co/datasets/birdsql/bird_mini_dev). The small SQLite question JSON and the three databases needed by this smoke set are available locally under ignored `data/bird/`; their SHA-256 values, the question source revision, and 12 fixed question IDs are recorded in [the smoke manifest](evals/bird_smoke_manifest.json). The selected cases cover three databases (four each) and simple/moderate/challenging difficulty (4/6/2). `--questions-only` prepares `evals/bird_smoke.json` without requiring SQLite databases on a fresh checkout:

```powershell
uv run python scripts/import_bird.py --root data/bird --manifest evals/bird_smoke_manifest.json --questions-only --output evals/bird_smoke.json
```

On a fresh checkout, obtain the [complete database package linked by BIRD](https://github.com/bird-bench/mini_dev), save the archive under `data/`, and independently record its SHA-256. Extracting an archive is explicit and checksum-verified:

```powershell
uv run python scripts/fetch_bird.py --archive data/mini_dev_download.zip --sha256 "<64-hex-sha256>"
uv run python scripts/import_bird.py --root data/bird --manifest evals/bird_smoke_manifest.json --output evals/bird_smoke.json
uv run python scripts/run_eval.py --provider model --cases evals/bird_smoke.json --output results/direct-bird.json
# Optional assisted comparison using expert annotations:
uv run python scripts/run_eval.py --provider model --evidence-mode oracle --cases evals/bird_smoke.json --output results/oracle-bird.json
```

The importer accepts `mini_dev_sqlite.json` or the Hugging Face shard filename, plus `<db_id>.sqlite` for each selected database. The manifest prevents accidentally evaluating a different question revision. The database files are never modified or bundled in Git. This is a fixed development smoke set, not the official BIRD EX evaluation; the current runner caps result rows and uses simplified execution matching. [LiveSQLBench Base Lite SQLite](https://huggingface.co/datasets/birdsql/livesqlbench-base-lite-sqlite) is a separate benchmark whose public release omits gold SQL and includes management tasks, so it is not used in this read-only baseline. Dataset terms and project provenance are in [attribution](docs/attribution.md).

## Scope

Implemented: configurable OpenAI-compatible direct prompt, registered local SQLite data sources, AST guard, read-only execution, API health/schema/query endpoints, JSON logging, tests, smoke evaluation, and one linear LangGraph workflow. Pending: durable checkpoints, PostgreSQL/pgvector, retrieval, SQL repair, multi-turn context/memory, Python analysis, SSE, and production authentication.
