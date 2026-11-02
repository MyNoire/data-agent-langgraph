"""日期应用日志按调用写入，不替换进程内任何共享 logger 的处理器。"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._events import JsonFormatter
from .settings import Settings, get_settings


def log_application(event: str, *, settings: Settings | None = None,
                    secrets: tuple[str, ...] = (), **fields: Any) -> Path:
    """把一个安全事件追加到 UTC 日期文件，返回文件路径；写失败正常抛出 OSError。

    参数：event 为固定事件名，fields 仅写白名单；不保存异常或业务正文。
    副作用：新建日期目录并追加一条独立 JSONL，不更改全局 logging。
    """
    settings = settings or get_settings()
    directory = settings.artifacts_dir / "logs" / datetime.now(timezone.utc).date().isoformat()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "app.jsonl"
    record = logging.LogRecord("querypilot.app", logging.INFO, "", 0, event, (), None)
    for key, value in fields.items():
        if key not in record.__dict__:
            setattr(record, key, value)
    api_secret = settings.model_api_key
    secrets = tuple(secrets) + ((api_secret,) if api_secret and api_secret != "local" else ())
    payload = (JsonFormatter(secrets=secrets).format(record) + "\n").encode("utf-8")
    # 单次 write 保持不同线程/进程的一条事件作为一个追加请求。
    with path.open("ab", buffering=0) as stream:
        if stream.write(payload) != len(payload):
            raise OSError("application event write incomplete")
    return path
