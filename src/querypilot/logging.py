"""将 API 和 SQL 审计事件写为便于检索的单行 JSON 日志。"""

import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """把标准 LogRecord 和本项目的审计字段转换为 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        """保留基础字段，并按需加入 SQL 决策与执行信息。"""
        payload = {
                "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "event": record.getMessage(),
                "request_id": getattr(record, "request_id", None),
                "database_id": getattr(record, "database_id", None),
            }
        for field in ("sql", "decision", "elapsed_ms", "row_count", "error_type"):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    """为 querypilot 命名空间配置标准错误输出上的 JSON 日志。"""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("querypilot")
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    root.propagate = False
