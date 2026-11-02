"""原单图输入、输出与状态；不包含路径或运行上下文。"""

from typing import Any, NotRequired, TypedDict

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


