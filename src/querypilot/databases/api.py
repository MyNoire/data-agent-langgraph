"""数据库能力的完整公开操作与必要契约。"""

from ._registry import DatabaseRegistry, RegistryError
from ._schema import format_schema, inspect_sqlite
from ._guard import SqlRejected, guard_sql
from dataclasses import asdict
from pathlib import Path

from ._executor import QueryLimitError, QueryResult, execute_readonly as _execute_readonly
from querypilot.runtime.api import Settings


def get_schema(database_id: str, settings: Settings) -> dict[str, list[str]]:
    """读取登记源的简单结构。

    参数：database_id 为已登记源，settings 指定数据根。
    返回：原 table→columns 映射。
    异常：RegistryError/OSError/SQLite 错误；仅只读结构。
    """
    return inspect_sqlite(DatabaseRegistry(settings.data_dir).get(database_id))


def validate_sql(sql: str, allowed_tables: set[str], max_rows: int) -> str:
    """校验单语句只读 SQL；返回原 guard 的受限 SQL，无 I/O。"""
    return guard_sql(sql, allowed_tables, max_rows)


def execute_sql(database_id: str, sql: str, settings: Settings, *, run_context=None) -> QueryResult:
    """定位、校验并执行完整操作。

    参数：database_id/SQL、执行配置和可选运行上下文。
    返回：原 QueryResult；异常沿用 RegistryError/SqlRejected/QueryLimitError/SQLite。
    副作用：读取已登记 SQLite；执行器仍再次 guard 并限制资源。
    """
    from querypilot.runtime.api import task_run

    owned = run_context is None
    with task_run("query", settings, run_context=run_context, command="sql", private=True,
                  config={"max_rows": settings.max_rows, "query_timeout_ms": settings.query_timeout_ms,
                          "max_response_bytes": settings.max_response_bytes}) as context:
        path = DatabaseRegistry(settings.data_dir).get(database_id)
        result = _execute_readonly(path, sql, database_id=database_id, max_rows=settings.max_rows,
                                   timeout_ms=settings.query_timeout_ms,
                                   max_response_bytes=settings.max_response_bytes,
                                   audit_logger=context.audit_logger)
        if owned:
            context.complete(asdict(result))
        return result


def execute_readonly(path: Path, sql: str, *, database_id: str, max_rows: int = 100,
                     timeout_ms: int = 3000, max_response_bytes: int = 262144,
                     run_context=None) -> QueryResult:
    """对已由调用方明确选定的文件执行完整安全操作。

    参数：文件、SQL、源标识、原有资源上限及可选运行上下文。
    返回：原 QueryResult；异常为原 SQL/资源/SQLite 错误。
    副作用：只读 SQLite 并记录运行；供冻结工具预检尚未登记的文件，不绕过 guard。
    """
    from querypilot.runtime.api import task_run

    settings = Settings(_env_file=None, data_dir=path.parent)
    owned = run_context is None
    with task_run("query", settings, run_context=run_context, command="file-sql", private=True,
                  config={"max_rows": max_rows, "query_timeout_ms": timeout_ms,
                          "max_response_bytes": max_response_bytes}) as context:
        result = _execute_readonly(path, sql, database_id=database_id, max_rows=max_rows,
                                   timeout_ms=timeout_ms, max_response_bytes=max_response_bytes,
                                   audit_logger=context.audit_logger)
        if owned:
            context.complete(asdict(result))
        return result


__all__ = ["DatabaseRegistry", "RegistryError", "QueryLimitError", "QueryResult", "SqlRejected",
           "get_schema", "format_schema", "validate_sql", "execute_sql", "inspect_sqlite", "guard_sql", "execute_readonly"]
