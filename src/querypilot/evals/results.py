"""定义评测结果的数据形状，并集中计算逐题与整批的公共字段。"""

import hashlib
import json
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any, Literal, Mapping, NotRequired, TypedDict

from querypilot.config import Settings


EvalProvider = Literal["fixture", "model", "graph"]
CaseOutcome = Literal["matched", "mismatched", "error"]
RunStatus = Literal["completed", "model_unavailable", "no_predictions"]


class CaseRecord(TypedDict):
    """一题的原始评测记录；SQL、问题和错误详情只写入非公开结果。"""

    index: int
    question_id: int | str | None
    database_id: str
    question: str
    evidence_used: bool
    valid_sql: bool
    execution_match: bool
    outcome: NotRequired[CaseOutcome]
    latency_ms: NotRequired[float]
    predicted_sql: NotRequired[str]
    error: NotRequired[str]
    error_type: NotRequired[str]
    graph_run_id: NotRequired[str]
    row_count: NotRequired[int]


class EvalSummary(TypedDict):
    """供 CLI、原始结果和进度事件复用的累计计数。"""

    total: int
    success: int
    failure: int
    mismatch: int
    error: int
    no_prediction: int
    valid_sql: int


class EvalReport(TypedDict):
    """保留既有 JSON 字段的整批评测结果。"""

    dataset: str
    dataset_sha256: str
    provider: EvalProvider
    status: RunStatus
    run_id: str
    summary: EvalSummary
    evidence_mode: str | None
    model: str | None
    model_base_url: str | None
    model_temperature: float | None
    prompt_version: str | None
    count: int
    max_rows: int
    query_timeout_ms: int
    max_response_bytes: int
    valid_sql_rate: float | None
    execution_accuracy: float | None
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    records: list[CaseRecord]
    graph_version: NotRequired[str]


def new_case_record(index: int, case: Mapping[str, Any], *, evidence_used: bool,
                    graph_run_id: str | None = None) -> CaseRecord:
    """创建一题的公共记录，不提前写入尚未发生的预测或结果。

    参数:
        index: 从零开始的题目索引。
        case: 至少含 db_id、question 的题集条目。
        evidence_used: 本题是否向模型提供了标注证据。
        graph_run_id: 单图评测的逐题图运行 ID；其他模式为空。
    返回:
        默认尚未成功执行、尚未匹配的 CaseRecord。
    """
    record: CaseRecord = {
        "index": index, "question_id": case.get("question_id"),
        "database_id": case["db_id"], "question": case["question"],
        "evidence_used": evidence_used, "valid_sql": False,
        "execution_match": False,
    }
    if graph_run_id is not None:
        record["graph_run_id"] = graph_run_id
    return record


def finish_case_record(record: CaseRecord, started: float) -> CaseRecord:
    """根据错误或执行匹配结果给一题定类并计算耗时。

    参数:
        record: 已写入预测、执行结果或错误的当前记录。
        started: 本题开始时的 ``time.monotonic()`` 值。
    返回:
        原记录对象，新增 outcome 与 latency_ms 两个字段。
    """
    record["outcome"] = (
        "error" if "error" in record else "matched" if record["execution_match"] else "mismatched"
    )
    record["latency_ms"] = round((time.monotonic() - started) * 1000, 3)
    return record


def summarize_records(records: list[CaseRecord]) -> EvalSummary:
    """统计互斥结果类别及两个可重叠的诊断计数。

    参数:
        records: 已经调用 ``finish_case_record`` 的逐题记录。
    返回:
        success、mismatch、error 的互斥计数及总数；no_prediction
        是 error 中可能出现的子集，valid_sql 是独立计数。
    """
    outcomes = Counter(record["outcome"] for record in records)
    return {
        "total": len(records), "success": outcomes["matched"],
        "failure": outcomes["mismatched"] + outcomes["error"],
        "mismatch": outcomes["mismatched"], "error": outcomes["error"],
        "no_prediction": sum("predicted_sql" not in record for record in records),
        "valid_sql": sum(record["valid_sql"] for record in records),
    }


def build_report(*, cases_path: Path, case_bytes: bytes, settings: Settings,
                 provider: EvalProvider, run_id: str, records: list[CaseRecord],
                 evidence_mode: str | None, prompt_version: str | None,
                 graph_version: str | None = None) -> EvalReport:
    """用共同规则构建可序列化的整批评测结果。

    参数:
        cases_path: 题集路径；写入 dataset 字段。
        case_bytes: 原始题集字节；计算可复核的 SHA-256。
        settings: 模型标识及 SQL 执行上限。
        provider: fixture、model 或 graph。
        run_id: 本次批次 ID。
        records: 完成并分类的逐题记录。
        evidence_mode: 使用的证据模式；fixture 为 None。
        prompt_version: 实际使用的 Prompt 版本；fixture 为 None。
        graph_version: 单图实现版本；其他模式为空。
    返回:
        与现有原始评测 JSON 兼容的 EvalReport。没有任何预测时模型
        准确率和有效 SQL 率为 None，避免把服务不可用算成 0%。
    """
    summary = summarize_records(records)
    no_predictions = bool(records) and summary["no_prediction"] == len(records)
    status: RunStatus = "completed"
    if no_predictions and provider == "model":
        status = "model_unavailable"
    elif no_predictions and provider == "graph":
        status = "no_predictions"
    model_used = provider != "fixture"
    latencies = sorted(record["latency_ms"] for record in records)
    rate_available = bool(records) and status == "completed"
    report: EvalReport = {
        "dataset": str(cases_path),
        "dataset_sha256": hashlib.sha256(case_bytes).hexdigest(),
        "provider": provider, "status": status, "run_id": run_id,
        "summary": summary, "evidence_mode": evidence_mode,
        "model": settings.model_name if model_used else None,
        "model_base_url": settings.model_base_url if model_used else None,
        "model_temperature": settings.model_temperature if model_used else None,
        "prompt_version": prompt_version, "count": len(records),
        "max_rows": settings.max_rows,
        "query_timeout_ms": settings.query_timeout_ms,
        "max_response_bytes": settings.max_response_bytes,
        "valid_sql_rate": summary["valid_sql"] / len(records) if rate_available else None,
        "execution_accuracy": summary["success"] / len(records) if rate_available else None,
        "p50_latency_ms": statistics.median(latencies) if latencies else None,
        "p95_latency_ms": latencies[min(len(latencies) - 1, int(len(latencies) * .95))] if latencies else None,
        "records": records,
    }
    if graph_version is not None:
        report["graph_version"] = graph_version
    return report


def write_report(report: EvalReport, output: Path | None) -> None:
    """将原始评测结果写入独占创建的文件。

    参数:
        report: 待保存的完整评测结果。
        output: 输出路径；为 None 时仅保留内存结果。
    返回:
        None；存在输出路径时写入 UTF-8 JSON 与末尾换行。
    异常:
        FileExistsError: 输出文件已存在，避免覆盖此前的评测证据。
    """
    if output is None:
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
