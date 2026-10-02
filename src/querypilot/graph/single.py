"""要求显式指定数据库的单条线性 Text2SQL 工作流。

此图复用 Phase 0 的模型适配器、AST 校验器和只读执行器。
目前不含选库、重试、checkpoint 或长期记忆。
"""

from dataclasses import asdict
import logging
import time
from typing import Any, Callable, NotRequired, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from querypilot.config import Settings
from querypilot.database.executor import execute_readonly
from querypilot.database.registry import DatabaseRegistry
from querypilot.database.schema import format_schema, inspect_sqlite
from querypilot.evals.baseline import generate_sql
from querypilot.sql.guard import guard_sql


class QueryInput(TypedDict):
    """图的输入：调用者提供数据库 ID 和自然语言问题。"""

    database_id: str
    question: str


class QueryOutput(TypedDict):
    """图的输出：校验后的 SQL 与只读执行结果。"""

    sql: str
    result: dict[str, Any]


class QueryState(QueryInput):
    """节点之间传递的状态；可选字段由对应节点逐步写入。"""

    schema: NotRequired[str]
    allowed_tables: NotRequired[list[str]]
    generated_sql: NotRequired[str]
    sql: NotRequired[str]
    result: NotRequired[dict[str, Any]]


# 生成器参数依次为问题、Schema、证据、运行配置；返回候选 SQL。
SqlGenerator = Callable[[str, str, str, Settings], str]

def query_result_row_count(output: Any) -> int | None:
    """从 QueryPilot 输出中只提取安全的行数。

    参数:
        output: 节点增量或图最终输出，也可能不是字典。
    返回:
        result.row_count 为非负整数时返回该值，否则返回 None。
        不修改 output，不返回 SQL、列名或结果行。
    """
    result = output.get("result") if isinstance(output, dict) else None
    count = result.get("row_count") if isinstance(result, dict) else None
    return count if type(count) is int and count >= 0 else None

def build_single_query_graph(settings: Settings, *, generator: SqlGenerator = generate_sql) -> CompiledStateGraph:
    """编译 Schema → 模型 → SQL 校验 → 只读执行的线性图。

    参数:
        settings: 数据目录、模型连接信息及 SQL 执行上限。
        generator: SQL 生成函数；默认调用真实模型，测试可注入固定结果。
    返回:
        可调用的已编译 LangGraph；输入为 QueryInput，输出为 QueryOutput。
    说明:
        凭据和数据库路径留在节点闭包内，不写入图状态。
    """
    registry = DatabaseRegistry(settings.data_dir)

    def load_schema(state: QueryState) -> dict[str, Any]:
        """读取已登记数据库的当前表列信息。

        参数:
            state: 至少包含 database_id 的图状态。
        返回:
            含 schema 文本及 allowed_tables 表名列表的状态增量。
        异常:
            RegistryError: database_id 未登记或登记路径无效。
        """
        schema = inspect_sqlite(registry.get(state["database_id"]))
        return {"schema": format_schema(schema), "allowed_tables": list(schema)}

    def generate(state: QueryState) -> dict[str, str]:
        """根据问题和 Schema 生成候选 SQL，不注入 oracle evidence。

        参数:
            state: 含 question 和 load_schema 产出的 schema 的图状态。
        返回:
            含 generated_sql 候选语句的状态增量。
        异常:
            模型调用失败时向上抛出适配器异常，本阶段不重试。
        """
        return {"generated_sql": generator(state["question"], state["schema"], "", settings)}

    def validate(state: QueryState) -> dict[str, str]:
        """校验候选 SQL 的 AST 并施加行数上限。

        参数:
            state: 含 generated_sql 和 allowed_tables 的图状态。
        返回:
            含 sql 字段的状态增量；该字段是允许执行的受限 SQL。
        异常:
            SqlRejected: 语句不是受允许的单条只读查询。
        """
        return {"sql": guard_sql(state["generated_sql"], set(state["allowed_tables"]), settings.max_rows)}

    def execute(state: QueryState) -> dict[str, dict[str, Any]]:
        """使用 Phase 0 只读执行器运行通过校验的 SQL。

        参数:
            state: 含 database_id 和校验后 sql 的图状态。
        返回:
            含 result 的状态增量；result 包含 SQL、列、行数和耗时。
        异常:
            QueryLimitError: 查询超时或响应超限。
            sqlite3.DatabaseError: SQLite 无法执行该查询。
        """
        result = execute_readonly(
            registry.get(state["database_id"]),
            state["sql"],
            database_id=state["database_id"],
            max_rows=settings.max_rows,
            timeout_ms=settings.query_timeout_ms,
            max_response_bytes=settings.max_response_bytes,
        )
        return {"result": asdict(result)}

    builder = StateGraph(QueryState, input_schema=QueryInput, output_schema=QueryOutput)
    builder.add_node("load_schema", load_schema)
    builder.add_node("generate_sql", generate)
    builder.add_node("validate_sql", validate)
    builder.add_node("execute_sql", execute)
    builder.add_edge(START, "load_schema")
    builder.add_edge("load_schema", "generate_sql")
    builder.add_edge("generate_sql", "validate_sql")
    builder.add_edge("validate_sql", "execute_sql")
    builder.add_edge("execute_sql", END)
    return builder.compile(name="querypilot_single")
