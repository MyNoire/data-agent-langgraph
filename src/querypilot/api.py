"""Phase 0 HTTP 入口：健康检查、SQLite 注册、Schema 查询与受控 SQL 执行。"""

import logging
import sqlite3
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from querypilot.config import Settings, get_settings
from querypilot.database.executor import QueryLimitError, execute_readonly
from querypilot.database.registry import DatabaseRegistry, RegistryError
from querypilot.database.schema import inspect_sqlite
from querypilot.logging import configure_logging
from querypilot.sql.guard import SqlRejected


class RegisterRequest(BaseModel):
    """注册一个位于配置数据目录内的 SQLite 文件。"""

    database_id: str
    path: Path


class QueryRequest(BaseModel):
    """指定已注册的数据库以及待校验、执行的 SQL。"""

    database_id: str
    sql: str = Field(min_length=1, max_length=50000)


def create_app(settings: Settings | None = None) -> FastAPI:
    """构造 API；允许测试注入独立的数据目录和资源上限。"""
    settings = settings or get_settings()
    registry = DatabaseRegistry(settings.data_dir)
    configure_logging()
    logger = logging.getLogger("querypilot.api")
    app = FastAPI(title="QueryPilot", version="0.1.0")

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        """给每个 HTTP 响应附上可用于排查的请求 ID。"""
        request_id = request.headers.get("x-request-id") or str(uuid4())
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        logger.info("http_request", extra={"request_id": request_id})
        return response

    @app.get("/health")
    def health():
        """返回进程健康状态；此接口不测试外部模型可用性。"""
        return {"status": "ok", "phase": 0}

    @app.get("/databases")
    def databases():
        """列出当前注册的数据库 ID 和方言。"""
        return [{"database_id": key, "dialect": "sqlite"} for key in sorted(registry.list())]

    @app.post("/databases", status_code=201)
    def register(body: RegisterRequest):
        """验证路径和 SQLite 文件，再持久化 database_id 映射。"""
        try:
            path = registry.register(body.database_id, body.path)
        except (RegistryError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"database_id": body.database_id, "dialect": "sqlite", "path": str(path)}

    @app.get("/databases/{database_id}/schema")
    def schema(database_id: str):
        """返回供用户查看及后续 Prompt 使用的表列信息。"""
        try:
            return {"database_id": database_id, "tables": inspect_sqlite(registry.get(database_id))}
        except (RegistryError, OSError) as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/queries")
    def query(body: QueryRequest):
        """通过唯一的受控执行入口运行 SQL，并把策略错误映射为 HTTP 错误。"""
        try:
            result = execute_readonly(registry.get(body.database_id), body.sql, database_id=body.database_id,
                                      max_rows=settings.max_rows, timeout_ms=settings.query_timeout_ms,
                                      max_response_bytes=settings.max_response_bytes)
            return result.__dict__
        except RegistryError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (SqlRejected, QueryLimitError) as exc:
            logger.warning("sql_rejected", extra={"database_id": body.database_id})
            raise HTTPException(400, str(exc)) from exc
        except sqlite3.DatabaseError as exc:
            raise HTTPException(400, "SQL execution failed") from exc

    return app


# uvicorn querypilot.api:app 使用该实例；测试则可调用 create_app 注入配置。
app = create_app()
