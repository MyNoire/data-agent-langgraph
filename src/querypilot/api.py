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
    """构造 FastAPI 应用及其数据库注册表。

    参数:
        settings: 可选运行配置；为空时从环境变量和 .env 读取。
    返回:
        已注册健康检查、数据库和查询接口的 FastAPI 实例。
    """
    settings = settings or get_settings()
    registry = DatabaseRegistry(settings.data_dir)
    configure_logging()
    logger = logging.getLogger("querypilot.api")
    app = FastAPI(title="QueryPilot", version="0.1.0")

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        """记录 HTTP 请求并给响应附加请求 ID。

        参数:
            request: 当前 FastAPI 请求。
            call_next: 调用后续中间件或路由的异步函数。
        返回:
            增加 x-request-id 响应头的 HTTP Response。
        """
        request_id = request.headers.get("x-request-id") or str(uuid4())
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        logger.info("http_request", extra={"request_id": request_id})
        return response

    @app.get("/health")
    def health():
        """返回进程健康状态。

        参数:
            无。
        返回:
            包含 status 和 phase 的 JSON 对象；不检查外部模型。
        """
        return {"status": "ok", "phase": 0}

    @app.get("/databases")
    def databases():
        """列出当前注册的 SQLite 数据库。

        参数:
            无。
        返回:
            由 database_id 和 dialect 组成的对象列表。
        """
        return [{"database_id": key, "dialect": "sqlite"} for key in sorted(registry.list())]

    @app.post("/databases", status_code=201)
    def register(body: RegisterRequest):
        """验证并登记一个位于数据目录内的 SQLite 文件。

        参数:
            body: 包含 database_id 和文件路径的请求体。
        返回:
            已登记的数据库 ID、方言与绝对路径。
        异常:
            HTTPException(400): ID、路径或 SQLite 文件无效。
        """
        try:
            path = registry.register(body.database_id, body.path)
        except (RegistryError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"database_id": body.database_id, "dialect": "sqlite", "path": str(path)}

    @app.get("/databases/{database_id}/schema")
    def schema(database_id: str):
        """读取指定数据库的表名与列名。

        参数:
            database_id: 已登记的数据库 ID。
        返回:
            包含 database_id 和 tables 映射的 JSON 对象。
        异常:
            HTTPException(404): 数据库未知或文件不可访问。
        """
        try:
            return {"database_id": database_id, "tables": inspect_sqlite(registry.get(database_id))}
        except (RegistryError, OSError) as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/queries")
    def query(body: QueryRequest):
        """通过受控的 SQLite 只读执行入口运行 SQL。

        参数:
            body: 包含 database_id 和 SQL 的请求体。
        返回:
            查询 SQL、列名、结果行、行数与耗时。
        异常:
            HTTPException(404): 数据库未知。
            HTTPException(400): SQL 被拒绝、超限或执行失败。
        """
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
