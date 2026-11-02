"""在 SQLite 只读连接中执行已校验的查询，并限制资源消耗。"""

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from ._schema import inspect_sqlite
from ._guard import guard_sql

logger = logging.getLogger("querypilot.sql")


class QueryLimitError(ValueError):
    """查询超过运行时间或响应大小限制。"""

    pass


@dataclass(frozen=True)
class QueryResult:
    """返回实际执行的 SQL、表格数据与本次执行耗时。"""

    sql: str
    columns: list[str]
    rows: list[list[object]]
    row_count: int
    elapsed_ms: float


def execute_readonly(path: Path, sql: str, *, database_id: str, max_rows: int = 100,
                     timeout_ms: int = 3000, max_response_bytes: int = 262144,
                     audit_logger: logging.Logger | None = None) -> QueryResult:
    """校验并执行单条 SQLite 只读查询。

    参数:
        path: 已登记的 SQLite 文件路径。
        sql: 待校验和执行的 SQL 文本。
        database_id: 日志审计所用的数据库 ID。
        max_rows: 最多返回的结果行数。
        timeout_ms: 允许的最大执行时长，单位为毫秒。
        max_response_bytes: 允许的最大 JSON 响应大小，单位为字节。
    返回:
        QueryResult：实际执行的 SQL、列名、结果行、行数及耗时。
    异常:
        SqlRejected: SQL 不符合只读语句或表访问规则。
        QueryLimitError: 查询超时或结果超过字节上限。
        sqlite3.DatabaseError: SQLite 查询执行失败。
    说明:
        AST 校验之外仍使用只读连接、authorizer 和进度回调；尚非多租户服务。
    """
    logger = audit_logger or logging.getLogger("querypilot.sql")
    schema = inspect_sqlite(path)
    started = time.monotonic()
    try:
        guarded = guard_sql(sql, set(schema), max_rows)
    except Exception as exc:
        logger.warning("sql_guard", extra={"database_id": database_id, "sql": sql,
                                            "decision": "rejected", "error_type": type(exc).__name__})
        raise
    logger.info("sql_guard", extra={"database_id": database_id, "sql": sql, "decision": "allowed"})
    # mode=ro 从文件打开层面禁止写入；query_only 再约束此连接的 SQL 行为。
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")

    def authorize(action: int, arg1: str | None, arg2: str | None, _db: str | None, _source: str | None) -> int:
        """在 SQLite 编译查询时决定操作是否允许。

        参数:
            action: SQLite authorizer 的操作码。
            arg1: 操作相关的第一个名称；当前策略不使用。
            arg2: 操作相关的第二个名称；函数调用时为函数名。
            _db: SQLite 提供的数据库名；当前策略不使用。
            _source: SQLite 提供的触发源；当前策略不使用。
        返回:
            SQLITE_OK 表示允许；SQLITE_DENY 表示拒绝。
        """
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() not in {"load_extension", "readfile", "writefile"}:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def progress() -> int:
        """在 SQLite 进度回调中检查运行时长。

        参数:
            无。
        返回:
            超时返回 1 以中断查询；未超时返回 0。
        """
        return int((time.monotonic() - started) * 1000 > timeout_ms)

    connection.set_authorizer(authorize)
    connection.set_progress_handler(progress, 1000)
    try:
        cursor = connection.execute(guarded)
        columns = [field[0] for field in cursor.description]
        # guard 已加 LIMIT；多取一行是执行层的防御性行数检查。
        rows = [list(row) for row in cursor.fetchmany(max_rows + 1)]
        if len(rows) > max_rows:
            rows.pop()
        size = len(json.dumps({"columns": columns, "rows": rows}, default=str).encode("utf-8"))
        if size > max_response_bytes:
            raise QueryLimitError("response exceeds byte limit")
        result = QueryResult(guarded, columns, rows, len(rows), round((time.monotonic() - started) * 1000, 3))
        logger.info("sql_executed", extra={"database_id": database_id, "sql": guarded,
                                           "decision": "success", "elapsed_ms": result.elapsed_ms,
                                           "row_count": result.row_count})
        return result
    except sqlite3.OperationalError as exc:
        logger.warning("sql_execution_failed", extra={"database_id": database_id, "sql": guarded,
                                                    "decision": "failed", "error_type": type(exc).__name__})
        if "interrupted" in str(exc).lower():
            raise QueryLimitError("query timed out") from exc
        raise
    finally:
        connection.close()
