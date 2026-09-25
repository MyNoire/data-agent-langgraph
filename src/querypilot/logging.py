"""将 API 和 SQL 审计事件写为便于检索的单行 JSON 日志。"""

import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """把标准 LogRecord 和本项目的审计字段转换为 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        """把日志记录转换为单行 JSON。

        参数:
            record: Python logging 创建的 LogRecord。
        返回:
            包含时间、级别、事件及可选 SQL 审计字段的 JSON 字符串。
        """
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
