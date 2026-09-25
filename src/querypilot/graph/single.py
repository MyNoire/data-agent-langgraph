"""A single, linear Text2SQL graph with an explicit database ID.

This first graph makes state transitions observable while reusing the Phase 0
model adapter, AST guard, and read-only executor. It has no routing, retry loop,
checkpoint, or long-term memory yet.
"""

from dataclasses import asdict
from typing import Any, Callable, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph

from querypilot.config import Settings
from querypilot.database.executor import execute_readonly
from querypilot.database.registry import DatabaseRegistry
from querypilot.database.schema import format_schema, inspect_sqlite
from querypilot.evals.baseline import generate_sql
from querypilot.sql.guard import guard_sql


class QueryInput(TypedDict):
    """Caller-supplied values; database selection is still explicit."""

    database_id: str
    question: str


class QueryOutput(TypedDict):
    """Public graph result after SQL validation and execution."""

    sql: str
    result: dict[str, Any]


class QueryState(QueryInput):
    """Intermediate values handed from one node to the next."""

    schema: NotRequired[str]
    allowed_tables: NotRequired[list[str]]
    generated_sql: NotRequired[str]
    sql: NotRequired[str]
    result: NotRequired[dict[str, Any]]


SqlGenerator = Callable[[str, str, str, Settings], str]


def build_single_query_graph(settings: Settings, *, generator: SqlGenerator = generate_sql):
    """Compile Schema → model → guard → read-only execution.

    The generator argument allows tests to provide a deterministic model result.
    Credentials and database paths remain in the closure, outside graph state.
    """
    registry = DatabaseRegistry(settings.data_dir)

    def load_schema(state: QueryState) -> dict[str, Any]:
        """Resolve the registered database and expose its current table names."""
        schema = inspect_sqlite(registry.get(state["database_id"]))
        return {"schema": format_schema(schema), "allowed_tables": list(schema)}

    def generate(state: QueryState) -> dict[str, str]:
        """Reuse the Phase 0 prompt/model adapter without oracle evidence."""
        return {"generated_sql": generator(state["question"], state["schema"], "", settings)}

    def validate(state: QueryState) -> dict[str, str]:
        """Reject unsafe SQL before it reaches the execution node."""
        return {"sql": guard_sql(state["generated_sql"], set(state["allowed_tables"]), settings.max_rows)}

    def execute(state: QueryState) -> dict[str, dict[str, Any]]:
        """Run through the existing SQLite read-only boundary."""
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
    return builder.compile()
