"""单图的输入、输出、状态和运行时 context。"""

from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict

from querypilot.retrieval.api import KnowledgeScope, RetrievalHit


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
    prompt_tokens: NotRequired[int | None]
    completion_tokens: NotRequired[int | None]
    sql: NotRequired[str]
    result: NotRequired[dict[str, Any]]
    table_ddl: NotRequired[dict[str, str]]
    hits: NotRequired[list[RetrievalHit]]
    table_chunks: NotRequired[dict[str, str]]


@dataclass(frozen=True)
class QueryContext:
    """每次调用时传入的运行时 context，不进图状态和输出。

    knowledge_scopes 是"database_id → Knowledge Scope"对照表，knowledge 来源按当前库查表；
    Scope 只是数据，不是授权依据。
    """

    knowledge_scopes: dict[str, KnowledgeScope]

