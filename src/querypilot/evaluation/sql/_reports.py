"""显式读取历史报告或完整运行目录，按原白名单排他导出。"""

import json
import math
from collections import Counter
from pathlib import Path

from querypilot.runtime.api import publish_json, read_run_result

TOP_LEVEL = (
    "dataset_sha256", "provider", "status", "run_id", "summary", "evidence_mode", "model",
    "model_temperature", "prompt_version", "graph_version", "count", "max_rows", "query_timeout_ms",
    "max_response_bytes", "valid_sql_rate", "execution_accuracy", "p50_latency_ms", "p95_latency_ms", "workers",
    "token_usage",
)
RECORD = (
    "index", "question_id", "database_id", "graph_run_id", "evidence_used", "predicted_sql",
    "valid_sql", "execution_match", "outcome", "latency_ms", "frozen", "prompt_tokens", "completion_tokens",
)


def read_report(source: Path) -> dict:
    """读取明确给定的旧 JSON 或完整新运行目录，返回原字段，不改写或选择最新。

    参数：source 为现有文件或运行目录。异常：OSError/ValueError/RunArtifactError。
    """
    source = Path(source)
    is_run_directory = source.is_dir()
    raw = read_run_result(source) if is_run_directory else json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("records"), list):
        raise ValueError("invalid evaluation report")
    if not all(isinstance(record, dict) for record in raw["records"]):
        raise ValueError("invalid evaluation records")
    if is_run_directory:
        _validate_run_report(raw)
    return raw


def _number(value, *, nullable: bool = False) -> bool:
    """判断有限非负JSON数值；排除bool，nullable仅允许明确的None。"""
    if value is None:
        return nullable
    if type(value) is int:
        return value >= 0
    return type(value) is float and math.isfinite(value) and value >= 0


def _validate_run_report(raw: dict) -> None:
    """校验当前完整运行的报告形状与计数一致性；失败拒绝，绝不修补原结果。

    参数：已通过运行终态检查的JSON对象。返回None，损坏时抛ValueError。
    仅用于新运行目录；历史显式JSON继续使用读取入口原有的宽松规则。
    """
    required_text = ("dataset", "dataset_sha256", "provider", "status", "run_id")
    nullable_text = ("evidence_mode", "model", "model_base_url", "prompt_version")
    integer_fields = ("count", "max_rows", "query_timeout_ms", "max_response_bytes")
    number_fields = ("model_temperature", "valid_sql_rate", "execution_accuracy", "p50_latency_ms", "p95_latency_ms")
    if any(type(raw.get(key)) is not str for key in required_text):
        raise ValueError("invalid evaluation report text fields")
    if any(key not in raw or (raw[key] is not None and type(raw[key]) is not str) for key in nullable_text):
        raise ValueError("invalid evaluation report nullable fields")
    if any(type(raw.get(key)) is not int or raw[key] < (0 if key == "count" else 1) for key in integer_fields):
        raise ValueError("invalid evaluation report integer fields")
    if any(key not in raw or not _number(raw[key], nullable=True) for key in number_fields):
        raise ValueError("invalid evaluation report numeric fields")
    if raw["provider"] not in {"fixture", "model", "graph"} or raw["status"] not in {"completed", "model_unavailable", "no_predictions"}:
        raise ValueError("invalid evaluation report mode")
    if raw["evidence_mode"] not in {None, "none", "oracle"}:
        raise ValueError("invalid evaluation evidence mode")
    if "graph_version" in raw and type(raw["graph_version"]) is not str:
        raise ValueError("invalid evaluation graph version")
    if raw["provider"] == "graph" and "graph_version" not in raw:
        raise ValueError("missing evaluation graph version")

    records = raw["records"]
    for index, record in enumerate(records):
        text_fields = ("database_id", "question", "outcome")
        flags = ("evidence_used", "valid_sql", "execution_match")
        if any(type(record.get(key)) is not str for key in text_fields):
            raise ValueError("invalid evaluation case text fields")
        if any(type(record.get(key)) is not bool for key in flags):
            raise ValueError("invalid evaluation case flags")
        if "question_id" not in record or (record["question_id"] is not None and type(record["question_id"]) not in (int, str)):
            raise ValueError("invalid evaluation question identifier")
        if type(record.get("index")) is not int or record["index"] != index or not _number(record.get("latency_ms")):
            raise ValueError("invalid evaluation case index or latency")
        if any(key in record and type(record[key]) is not str for key in ("predicted_sql", "error", "error_type", "graph_run_id")):
            raise ValueError("invalid evaluation optional case text")
        if "row_count" in record and (type(record["row_count"]) is not int or record["row_count"] < 0):
            raise ValueError("invalid evaluation case row count")
        if raw["provider"] == "graph" and record.get("graph_run_id") != f"{raw['run_id']}:case:{index + 1}":
            raise ValueError("invalid evaluation child run identifier")
        outcome = "error" if "error" in record else "matched" if record["execution_match"] else "mismatched"
        if record["outcome"] != outcome or (record["execution_match"] and not record["valid_sql"]):
            raise ValueError("inconsistent evaluation case outcome")

    outcomes = Counter(record["outcome"] for record in records)
    expected_counts = {
        "total": len(records), "success": outcomes["matched"], "mismatch": outcomes["mismatched"],
        "error": outcomes["error"], "failure": outcomes["mismatched"] + outcomes["error"],
        "no_prediction": sum("predicted_sql" not in record for record in records),
        "valid_sql": sum(record["valid_sql"] for record in records),
    }
    summary = raw.get("summary")
    if raw["count"] != len(records) or not isinstance(summary, dict):
        raise ValueError("inconsistent evaluation record count")
    if any(type(summary.get(key)) is not int or summary[key] != value for key, value in expected_counts.items()):
        raise ValueError("inconsistent evaluation summary counts")
    expected_status = "completed"
    if records and expected_counts["no_prediction"] == len(records):
        expected_status = {"model": "model_unavailable", "graph": "no_predictions"}.get(raw["provider"], "completed")
    if raw["status"] != expected_status:
        raise ValueError("inconsistent evaluation report status")
    rates_available = bool(records) and expected_status == "completed"
    for key in ("valid_sql_rate", "execution_accuracy"):
        if rates_available and (raw[key] is None or raw[key] > 1):
            raise ValueError("invalid evaluation report rates")
        if not rates_available and raw[key] is not None:
            raise ValueError("invalid evaluation report rates")


def export_public(source: Path, destination: Path) -> dict:
    """按原字段白名单导出，返回导出数据；目标已存在时拒绝覆盖。

    参数：source 是显式报告位置，destination 是新文件。
    副作用：新建父目录及结果文件；可能包含 predicted_sql，调用方须审查后分享。
    """
    raw = read_report(source)
    public = {key: raw[key] for key in TOP_LEVEL if key in raw}
    public["records"] = [{key: record[key] for key in RECORD if key in record} for record in raw["records"]]
    publish_json(destination, public)
    return public
