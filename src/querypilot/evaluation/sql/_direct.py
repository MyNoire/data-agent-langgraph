"""Direct-prompt Text2SQL 基线及逐题 smoke 评测。"""

import json
import time
from pathlib import Path


from querypilot.runtime.api import Settings
from querypilot.databases.api import execute_sql, get_schema
from querypilot.databases.api import DatabaseRegistry
from querypilot.databases.api import format_schema
from ._compare import normalized_rows
from ._results import (
    CaseRecord, EvalReport, build_report, finish_case_record, new_case_record,
)


from querypilot.sql_generation.api import PROMPT_VERSION, direct_prompt, generate_sql


def run_direct(cases_path: Path, settings: Settings, *, provider="model", evidence_mode="none",
               generator=generate_sql, run_context) -> EvalReport:
    """执行原 direct 批次；参数为题集、模式、生成器与借用上下文，返回原报告。

    查库/结构失败仍在逐题 try 外中止；题内错误记录后继续，不关闭或发布外层运行。
    """
    if provider not in {"model", "fixture"}:
        raise ValueError("provider must be model or fixture")
    if evidence_mode not in {"none", "oracle"}:
        raise ValueError("evidence_mode must be none or oracle")
    case_bytes = cases_path.read_bytes()
    cases = json.loads(case_bytes)
    registry = DatabaseRegistry(settings.data_dir)
    run_id = run_context.run_id
    progress = run_context.logger
    run_started = time.monotonic()
    progress.info("eval_started", extra={
        "run_id": run_id, "component": "evaluation", "provider": provider,
        "evidence_mode": evidence_mode if provider == "model" else None,
        "total_cases": len(cases),
    })
    records: list[CaseRecord] = []
    success_count = 0
    failure_count = 0
    for index, case in enumerate(cases):
        started = time.monotonic()
        database_id = case["db_id"]
        progress.info("eval_case_started", extra={
            "run_id": run_id, "component": "evaluation", "database_id": database_id,
            "question_id": case.get("question_id"), "case_index": index + 1,
            "total_cases": len(cases),
        })
        registry.get(database_id)
        schema = format_schema(get_schema(database_id, settings))
        evidence = case.get("evidence", "") if provider == "model" and evidence_mode == "oracle" else ""
        record = new_case_record(index, case, evidence_used=bool(evidence))
        try:
            # fixture 是管线自检；只有 model 模式才实际调用 Text2SQL 模型。
            prediction = case["SQL"] if provider == "fixture" else generator(case["question"], schema, evidence, settings)
            record["predicted_sql"] = prediction
            actual = execute_sql(database_id, prediction, settings, run_context=run_context)
            record["valid_sql"] = True
            gold = execute_sql(database_id, case["SQL"], settings, run_context=run_context)
            # 同一列位置的值需要相等；行顺序不影响结果，重复行仍计入。
            record["execution_match"] = normalized_rows(actual.rows) == normalized_rows(gold.rows)
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
        finish_case_record(record, started)
        records.append(record)
        success_count += int(record["execution_match"])
        failure_count += int(not record["execution_match"])
        completed = index + 1
        progress.info("eval_case_finished", extra={
            "run_id": run_id, "component": "evaluation", "database_id": database_id,
            "question_id": case.get("question_id"), "case_index": completed,
            "completed_cases": completed, "total_cases": len(cases),
            "status": record["outcome"], "elapsed_ms": record["latency_ms"],
            "success_count": success_count, "failure_count": failure_count,
            "eta_seconds": round((time.monotonic() - run_started) / completed
                                 * (len(cases) - completed), 1),
        })
    result = build_report(
        cases_path=cases_path, case_bytes=case_bytes, settings=settings,
        provider=provider, run_id=run_id, records=records,
        evidence_mode=evidence_mode if provider == "model" else None,
        prompt_version=PROMPT_VERSION if provider == "model" else None,
    )
    summary = result["summary"]
    progress.info("eval_finished", extra={
        "run_id": run_id, "component": "evaluation", "status": result["status"],
        "completed_cases": summary["total"], "total_cases": summary["total"],
        "success_count": summary["success"], "failure_count": summary["failure"],
        "elapsed_ms": round((time.monotonic() - run_started) * 1000, 3),
    })
    return result
