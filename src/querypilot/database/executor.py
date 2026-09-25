"""在 SQLite 只读连接中执行已校验的查询，并限制资源消耗。"""

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from querypilot.database.schema import inspect_sqlite
from querypilot.sql.guard import guard_sql

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
                     timeout_ms: int = 3000, max_response_bytes: int = 262144) -> QueryResult:
    """执行单条受控 SELECT，拒绝写入、长时间运行和过大响应。

    `guard_sql` 负责事前 AST 校验；SQLite 的只读模式、authorizer 和进度回调
    是运行时的第二道限制。此函数适用于本地 SQLite 基线，尚非多租户服务。
    """
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
        """在 SQLite 编译 SQL 时只放行读取、查询及非禁用函数。"""
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() not in {"load_extension", "readfile", "writefile"}:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def progress() -> int:
        """每执行一批虚拟机指令检查耗时；返回非零值将中断查询。"""
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
