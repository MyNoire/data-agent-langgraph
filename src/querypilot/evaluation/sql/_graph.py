"""逐题运行单图的 Text2SQL 评测；与 direct-prompt baseline 分开保存。"""

import json
import time
from pathlib import Path

from querypilot.runtime.api import Settings
from querypilot.databases.api import execute_sql
from querypilot.databases.api import DatabaseRegistry
from querypilot.sql_generation.api import PROMPT_VERSION, generate_sql
from ._compare import normalized_rows
from ._results import (
    CaseRecord, EvalReport, build_report, finish_case_record, new_case_record,
)
from querypilot.querying.api import SqlGenerator, build_query_graph, query_result_row_count
from querypilot.runtime.api import GraphLogMiddleware


GRAPH_VERSION = "single-v1"


def run_graph(cases_path: Path, settings: Settings, *, generator: SqlGenerator = generate_sql,
              run_context) -> EvalReport:
    """运行原单图批次；参数为题集、配置、生成器与借用上下文，返回原报告。

    每题错误保持 error 并继续；原始生成SQL、节点顺序和批次子标识不变。
    """
    case_bytes = cases_path.read_bytes()
    cases = json.loads(case_bytes)
    if not isinstance(cases, list):
        raise ValueError("evaluation cases must be a JSON list")
    batch_run_id = run_context.run_id
    graph = build_query_graph(settings, generator=generator, run_context=run_context)
    registry = DatabaseRegistry(settings.data_dir)
    middleware = run_context.graph_middleware(row_count=query_result_row_count)
    progress = middleware.logger
    run_started = time.monotonic()
    progress.info("eval_started", extra={
        "run_id": batch_run_id, "component": "evaluation", "provider": "graph",
        "evidence_mode": "none", "total_cases": len(cases),
    })
    records: list[CaseRecord] = []
    success_count = 0
    failure_count = 0
    for index, case in enumerate(cases, start=1):
        case_started = time.monotonic()
        database_id = case["db_id"]
        graph_run_id = f"{batch_run_id}:case:{index}"
        fields = {
            "run_id": batch_run_id, "graph_run_id": graph_run_id,
            "component": "evaluation", "provider": "graph",
            "database_id": database_id, "question_id": case.get("question_id"),
            "case_index": index, "total_cases": len(cases),
        }
        progress.info("eval_case_started", extra=fields)
        record = new_case_record(index - 1, case, evidence_used=False,
                                 graph_run_id=graph_run_id)
        try:
            answer: dict[str, object] = {}
            for part in graph.stream(
                {"database_id": database_id, "question": case["question"]},
                config=middleware.config(
                    run_id=graph_run_id, parent_run_id=batch_run_id,
                    database_id=database_id, question_id=case.get("question_id"), case_index=index,
                ),
                stream_mode="updates", version="v2",
            ):
                if part["type"] != "updates":
                    continue
                for delta in part["data"].values():
                    if not isinstance(delta, dict):
                        continue
                    if "generated_sql" in delta:
                        record["predicted_sql"] = delta["generated_sql"]
                    if "sql" in delta:
                        answer["sql"] = delta["sql"]
                    if "result" in delta:
                        answer["result"] = delta["result"]
            if set(answer) != {"sql", "result"}:
                raise RuntimeError("graph stream ended without a complete answer")
            actual = answer["result"]
            record["valid_sql"] = True
            record["row_count"] = actual["row_count"]
            gold = execute_sql(database_id, case["SQL"], settings, run_context=run_context)
            record["execution_match"] = normalized_rows(actual["rows"]) == normalized_rows(gold.rows)
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            record["error"] = f"{type(exc).__name__}: {exc}"
        finish_case_record(record, case_started)
        records.append(record)
        success_count += int(record["execution_match"])
        failure_count += int(not record["execution_match"])
        finished_fields = {
            **fields, "status": record["outcome"], "elapsed_ms": record["latency_ms"],
            "completed_cases": index, "success_count": success_count,
            "failure_count": failure_count,
            "eta_seconds": round((time.monotonic() - run_started) / index * (len(cases) - index), 1),
        }
        if "error_type" in record:
            finished_fields["error_type"] = record["error_type"]
        progress.info("eval_case_finished", extra=finished_fields)

    result = build_report(
        cases_path=cases_path, case_bytes=case_bytes, settings=settings,
        provider="graph", run_id=batch_run_id, records=records,
        evidence_mode="none", prompt_version=PROMPT_VERSION,
        graph_version=GRAPH_VERSION,
    )
    summary = result["summary"]
    progress.info("eval_finished", extra={
        "run_id": batch_run_id, "component": "evaluation", "provider": "graph",
        "status": result["status"], "completed_cases": summary["total"],
        "total_cases": summary["total"], "success_count": summary["success"],
        "failure_count": summary["failure"],
        "elapsed_ms": round((time.monotonic() - run_started) * 1000, 3),
    })
    return result
