"""逐题运行单图的 Text2SQL 评测；与 direct-prompt baseline 分开保存。"""

import json
import logging
import time
from pathlib import Path
from uuid import uuid4

from querypilot.config import Settings
from querypilot.database.executor import execute_readonly
from querypilot.database.registry import DatabaseRegistry
from querypilot.evals.baseline import PROMPT_VERSION, generate_sql
from querypilot.evals.compare import normalized_rows
from querypilot.evals.results import (
    CaseRecord, EvalReport, build_report, finish_case_record, new_case_record, write_report,
)
from querypilot.graph.single import SqlGenerator, build_single_query_graph


GRAPH_VERSION = "single-v1"


def run_graph_eval(cases_path: Path, settings: Settings, *, output: Path | None = None,
                   run_id: str | None = None, generator: SqlGenerator = generate_sql) -> EvalReport:
    """用同一个编译图逐题运行题集，比较执行结果并记录逐节点进度。

    参数:
        cases_path: 含 db_id、question、SQL 的 JSON 题集路径。
        settings: 模型、数据目录和只读执行上限。
        output: 可选原始 JSON 结果路径；已存在时拒绝覆盖。
        run_id: 可选批次 ID；每题另外生成关联到批次的图运行 ID。
        generator: 单图 SQL 生成函数；默认调用真实模型，测试可注入替身。
    返回:
        包含逐题记录、汇总及当前执行配置的字典。
    异常:
        FileExistsError: 输出文件已存在。
        OSError、ValueError: 题集无法读取或结构不正确。
    说明:
        逐题图异常会记录为该题 error 并继续。比较器是本项目 capped-row
        smoke 指标，不等同于官方 BIRD Execution Accuracy。
    """
    if output is not None and output.exists():
        raise FileExistsError(f"evaluation output already exists: {output}")
    case_bytes = cases_path.read_bytes()
    cases = json.loads(case_bytes)
    if not isinstance(cases, list):
        raise ValueError("evaluation cases must be a JSON list")
    batch_run_id = run_id or uuid4().hex
    graph = build_single_query_graph(settings, generator=generator)
    registry = DatabaseRegistry(settings.data_dir)
    progress = logging.getLogger("querypilot.progress")
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
                {
                    "database_id": database_id, "question": case["question"],
                    "run_id": graph_run_id, "parent_run_id": batch_run_id,
                    "question_id": case.get("question_id"), "case_index": index,
                },
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
            gold = execute_readonly(
                registry.get(database_id), case["SQL"], database_id=database_id,
                max_rows=settings.max_rows, timeout_ms=settings.query_timeout_ms,
                max_response_bytes=settings.max_response_bytes,
            )
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
    write_report(result, output)
    summary = result["summary"]
    progress.info("eval_finished", extra={
        "run_id": batch_run_id, "component": "evaluation", "provider": "graph",
        "status": result["status"], "completed_cases": summary["total"],
        "total_cases": summary["total"], "success_count": summary["success"],
        "failure_count": summary["failure"],
        "elapsed_ms": round((time.monotonic() - run_started) * 1000, 3),
    })
    return result
