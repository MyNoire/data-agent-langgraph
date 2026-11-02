"""自然语言单图的完整操作和框架集成入口。"""

from querypilot.runtime.api import Settings
from querypilot.sql_generation.api import SqlGenerator, generate_sql
from .models import QueryInput, QueryOutput, QueryState
from ._single_graph import build_query_graph, query_result_row_count


def ask(database_id: str, question: str, settings: Settings, *, generator: SqlGenerator = generate_sql,
        run_context=None) -> QueryOutput:
    """完成一次原单图查询。

    参数：已登记源、问题、配置，可注入四参生成器及已有上下文。
    返回：原 sql/result 输出；模型/登记/SQL 安全错误原样传播。
    副作用：模型生成和只读 SQLite；不检索、不修复、不创建额外 Agent。
    """
    from querypilot.runtime.api import task_run

    owned = run_context is None
    with task_run("query", settings, run_context=run_context, command="ask", private=True,
                  config={"graph_version": "single-v1", "prompt_version": "direct-v2",
                          "max_rows": settings.max_rows, "query_timeout_ms": settings.query_timeout_ms,
                          "max_response_bytes": settings.max_response_bytes}) as context:
        graph = build_query_graph(settings, generator=generator, run_context=context)
        logs = context.graph_middleware(row_count=query_result_row_count)
        result = graph.invoke({"database_id": database_id, "question": question},
                              config=logs.config(run_id=context.run_id, database_id=database_id))
        if owned:
            context.complete(result)
        return result


__all__ = ["ask", "build_query_graph", "QueryInput", "QueryOutput", "QueryState", "SqlGenerator", "query_result_row_count"]
