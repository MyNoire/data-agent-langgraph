"""Direct-prompt Text2SQL 基线及逐题 smoke 评测。"""

import json
import logging
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

from openai import OpenAI

from querypilot.config import Settings
from querypilot.database.executor import execute_readonly
from querypilot.database.registry import DatabaseRegistry
from querypilot.database.schema import format_schema, inspect_sqlite
from querypilot.evals.compare import normalized_rows
from querypilot.evals.results import (
    CaseRecord, EvalReport, build_report, finish_case_record, new_case_record, write_report,
)


PROMPT_VERSION = "direct-v2"


def direct_prompt(question: str, schema: str, evidence: str = "") -> str:
    """把问题和 Schema 拼成 direct-prompt 模型输入。

    参数:
        question: 用户的自然语言问题。
        schema: 当前数据库的表列说明文本。
        evidence: 可选业务证据；空字符串表示不提供 oracle 证据。
    返回:
        要求模型只输出一条 SQLite SELECT 的 Prompt 文本。
    """
    evidence_line = f"Evidence: {evidence}\n" if evidence else ""
    return (
        "Generate exactly one read-only SQLite SELECT query. Return SQL only, without Markdown.\n"
        f"Schema:\n{schema}\n"
        f"{evidence_line}"
        f"Question: {question}"
    )


def generate_sql(question: str, schema: str, evidence: str, settings: Settings) -> str:
    """调用 OpenAI-compatible 服务生成候选 SQL。

    参数:
        question: 用户自然语言问题。
        schema: 当前数据库的表列说明文本。
        evidence: 可选业务证据；生产路径和无证据基线传空字符串。
        settings: 模型地址、名称、密钥和可选温度配置。
    返回:
        去掉首尾 Markdown SQL 代码围栏后的模型输出文本。
    异常:
        模型服务或网络失败时向上抛出 OpenAI 客户端异常。
    """
    client = OpenAI(base_url=settings.model_base_url, api_key=settings.model_api_key, timeout=60)
    request = {
        "model": settings.model_name,
        "messages": [{"role": "user", "content": direct_prompt(question, schema, evidence)}],
    }
    # 部分推理模型不接受 temperature；默认省略，仅在明确配置时传入。
    if settings.model_temperature is not None:
        request["temperature"] = settings.model_temperature
    response = client.chat.completions.create(**request)
    return (response.choices[0].message.content or "").strip().removeprefix("```sql").removesuffix("```").strip()


def run_eval(cases_path: Path, settings: Settings, *, provider: Literal["model", "fixture"] = "model",
             evidence_mode: Literal["none", "oracle"] = "none", output: Path | None = None,
             run_id: str | None = None) -> EvalReport:
    """逐题生成、执行并比较预测 SQL，形成 smoke 结果。

    参数:
        cases_path: JSON 题集文件路径，题目需包含 db_id、question 和 SQL。
        settings: 数据库目录、模型连接及执行资源限制。
        provider: ``model`` 调用真实模型；``fixture`` 直接使用标准 SQL。
        evidence_mode: ``none`` 不提供标注证据；``oracle`` 提供题目 evidence。
        output: 可选 JSON 输出路径；为空时只返回内存结果，已有文件不会被覆盖。
        run_id: 可选日志关联 ID；为空时为本次评测生成随机 ID。
    返回:
        包含配置、逐题结果标签、成功/失败计数、执行匹配率和延迟的字典。
    异常:
        ValueError: provider 或 evidence_mode 取值无效。
        FileExistsError: output 已存在，避免覆盖先前的评测结果。
    说明:
        fixture 仅验证管线；当前行数上限和比较器不等于官方 BIRD EX。
    """
    if provider not in {"model", "fixture"}:
        raise ValueError("provider must be model or fixture")
    if evidence_mode not in {"none", "oracle"}:
        raise ValueError("evidence_mode must be none or oracle")
    if output is not None and output.exists():
        raise FileExistsError(f"evaluation output already exists: {output}")
    case_bytes = cases_path.read_bytes()
    cases = json.loads(case_bytes)
    registry = DatabaseRegistry(settings.data_dir)
    run_id = run_id or uuid4().hex
    progress = logging.getLogger("querypilot.progress")
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
        path = registry.get(database_id)
        schema = format_schema(inspect_sqlite(path))
        evidence = case.get("evidence", "") if provider == "model" and evidence_mode == "oracle" else ""
        record = new_case_record(index, case, evidence_used=bool(evidence))
        try:
            # fixture 是管线自检；只有 model 模式才实际调用 Text2SQL 模型。
            prediction = case["SQL"] if provider == "fixture" else generate_sql(case["question"], schema, evidence, settings)
            record["predicted_sql"] = prediction
            actual = execute_readonly(path, prediction, database_id=database_id, max_rows=settings.max_rows,
                                      timeout_ms=settings.query_timeout_ms, max_response_bytes=settings.max_response_bytes)
            record["valid_sql"] = True
            gold = execute_readonly(path, case["SQL"], database_id=database_id, max_rows=settings.max_rows,
                                    timeout_ms=settings.query_timeout_ms, max_response_bytes=settings.max_response_bytes)
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
    write_report(result, output)
    summary = result["summary"]
    progress.info("eval_finished", extra={
        "run_id": run_id, "component": "evaluation", "status": result["status"],
        "completed_cases": summary["total"], "total_cases": summary["total"],
        "success_count": summary["success"], "failure_count": summary["failure"],
        "elapsed_ms": round((time.monotonic() - run_started) * 1000, 3),
    })
    return result
