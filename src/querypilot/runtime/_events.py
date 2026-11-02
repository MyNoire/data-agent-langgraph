"""安全事件使用固定消息名与元数据白名单，不格式化任意日志正文。"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable

from ._privacy import clean_value

EVENT_NAMES = frozenset({
    "run_started", "run_finished", "run_failed", "run_incomplete", "run_saved", "activity_started", "activity_finished",
    "graph_span_started", "graph_span_finished", "graph_interrupted", "graph_resumed",
    "eval_started", "eval_case_started", "eval_case_finished", "eval_finished",
    "http_request", "http_request_failed", "sql_rejected", "sql_guard", "sql_executed", "sql_execution_failed",
    "knowledge_started", "knowledge_finished", "knowledge_failed", "knowledge_import_started",
    "knowledge_import_finished", "knowledge_verify_started", "knowledge_verify_finished",
    "corpus_import_started", "corpus_import_finished", "corpus_verify_started", "corpus_verify_finished",
    "store_started", "store_finished", "corpus_started", "corpus_finished", "catalog_started", "catalog_finished",
    "source_verified", "transaction_started", "objects_written", "readback_verified", "import_finished",
    "verification_finished", "knowledge_operation_failed",
    "curation_started", "curation_finished", "corpus_build_started", "corpus_build_finished",
})
PROGRESS_FIELDS = (
    "event_version", "span_id", "parent_span_id", "span_name", "run_id", "parent_run_id", "graph_run_id",
    "component", "node", "status", "question_id", "case_index", "completed_cases", "total_cases",
    "success_count", "failure_count", "eta_seconds", "provider", "evidence_mode", "operation",
    "operation_outcome", "artifact_outcome", "request_id", "database_id", "elapsed_ms", "row_count", "error_type",
)


class JsonFormatter(logging.Formatter):
    """将固定事件和安全元数据写为 JSON；详细 SQL 仅允许专用审计实例。"""

    def __init__(self, *, audit: bool = False, secrets: tuple[str, ...] = ()) -> None:
        """参数：audit 只供私有审计端；secrets 为不得落盘的秘密，返回 None。"""
        super().__init__()
        self.audit = audit
        self.secrets = secrets

    def format(self, record: logging.LogRecord) -> str:
        """只读取受控字段，不调用 getMessage、str(exc) 或异常堆栈格式化。"""
        event = record.msg if isinstance(record.msg, str) and record.msg in EVENT_NAMES else "unrecognized_event"
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname, "logger": "querypilot.sql.audit" if self.audit else "querypilot.progress",
            "event": event,
        }
        for field in PROGRESS_FIELDS:
            value = getattr(record, field, None)
            if value is None or isinstance(value, (str, int, float, bool)):
                if field == "error_type" and value is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,95}", str(value)):
                    value = "Error"
                if hasattr(record, field):
                    payload[field] = value
        if self.audit:
            for field in ("sql", "decision"):
                if hasattr(record, field):
                    payload[field] = getattr(record, field)
        return json.dumps(clean_value(payload, self.secrets), ensure_ascii=False, allow_nan=False)


class RecordingHandler(logging.StreamHandler):
    """把写失败交给所属运行，避免 logging 向 stderr 输出含正文的错误回显。"""

    def __init__(self, stream, on_failure: Callable[[BaseException], None], *, audit: bool = False,
                 secrets: tuple[str, ...] = ()) -> None:
        """初始化自有流；on_failure 仅保存异常事实，不向全局 logger 传播。"""
        super().__init__(stream)
        self.on_failure = on_failure
        self.setFormatter(JsonFormatter(audit=audit, secrets=secrets))

    def handleError(self, record: logging.LogRecord) -> None:
        """记录当前写失败；不调用父类会打印日志正文的 handleError。"""
        import sys
        self.on_failure(sys.exception() or OSError("event write failed"))
