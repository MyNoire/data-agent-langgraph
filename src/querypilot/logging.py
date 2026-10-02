"""统一格式化 API、SQL 审计与运行进度事件，并保存 CLI 进度流。"""

import json
import logging
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, TextIO


PROGRESS_FIELDS = (
    "event_version", "span_id", "parent_span_id", "span_name",
    "run_id", "parent_run_id", "graph_run_id", "component", "node", "status", "question_id", "case_index",
    "completed_cases", "total_cases", "success_count", "failure_count",
    "eta_seconds", "provider", "evidence_mode", "log_file",
)


class JsonFormatter(logging.Formatter):
    """把标准 LogRecord 和本项目的审计字段转换为 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        """把日志记录转换为单行 JSON。

        参数:
            record: Python logging 创建的 LogRecord。
        返回:
            包含时间、级别、事件及对应审计或进度字段的 JSON 字符串。
        """
        payload = {
                "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "event": record.getMessage(),
                "request_id": getattr(record, "request_id", None),
                "database_id": getattr(record, "database_id", None),
            }
        # 进度流只接收运行元数据；SQL 审计仍保留在独立的 SQL logger 中。
        audit_fields = ("elapsed_ms", "row_count", "error_type")
        if record.name != "querypilot.progress":
            audit_fields += ("sql", "decision")
        for field in audit_fields:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        for field in PROGRESS_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    """为 querypilot 命名空间配置 JSON 日志处理器。

    参数:
        无。
    返回:
        None；将日志处理器、INFO 级别和传播策略写入 logger。
    """
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("querypilot")
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    root.propagate = False


@contextmanager
def progress_logging(path: Path, *, stream: TextIO | None = None) -> Iterator[None]:
    """将进度事件同时写到实时终端和独占创建的 JSONL 文件。

    参数:
        path: 本次运行的事件文件；已有文件会触发 FileExistsError。
        stream: 可选终端流；默认写到标准错误，保留标准输出给最终结果。
    返回:
        上下文管理器；进入后配置 querypilot.progress，退出时恢复原配置。
    异常:
        FileExistsError: 事件文件已存在，避免覆盖此前运行记录。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as event_file:
        logger = logging.getLogger("querypilot.progress")
        old_handlers = logger.handlers[:]
        old_level = logger.level
        old_propagate = logger.propagate
        terminal = logging.StreamHandler(stream or sys.stderr)
        disk = logging.StreamHandler(event_file)
        for handler in (terminal, disk):
            handler.setFormatter(JsonFormatter())
        logger.handlers = [terminal, disk]
        logger.setLevel(logging.INFO)
        logger.propagate = False
        try:
            yield
        finally:
            logger.handlers = old_handlers
            logger.setLevel(old_level)
            logger.propagate = old_propagate
            terminal.close()
            disk.close()
