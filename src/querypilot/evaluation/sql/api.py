"""SQL 评测完整操作：明确选择模式，保留领域报告，不包含模型策略。"""

from collections.abc import Callable
from hashlib import sha256
import json
from pathlib import Path

from querypilot.querying.api import (
    GRAPH_VERSION, RETRIEVAL_TOP_K, SCHEMA_RENDER_VERSION, ContextSource, StructureFormat,
)
from querypilot.retrieval.api import (
    KnowledgeScope, RetrievalHit, RetrievalMethod, RetrievalRequest, RetrievalResult, load_scope_chunks,
    load_table_chunks, shared_retriever,
)
from querypilot.runtime.api import Settings, sanitize_private, task_run
from querypilot.sql_generation.api import PROMPT_VERSION, SqlGenerator, generate_sql
from .models import CaseRecord, CaseOutcome, EvalReport, EvalSummary
from ._cases import SqlCaseSetError, select_database_cases
from ._reports import export_public, read_report


KNOWLEDGE_SOURCES = ("knowledge", "retrieval")
"""读知识库的 Context Source：需要各库的 Knowledge Scope，记结构写法和渲染版本。"""


def _knowledge_fields(context_source: ContextSource | None, structure_format: StructureFormat,
                      knowledge_scopes: dict[str, KnowledgeScope] | None, method: RetrievalMethod,
                      rerank: bool) -> dict:
    """读知识库的来源才记的配置：结构写法、渲染版本和各库的 Scope；retrieval 另记检索方法、是否重排和
    top_k。其他来源返回空字典。"""
    if context_source not in KNOWLEDGE_SOURCES:
        return {}
    fields = {"structure_format": structure_format, "schema_render_version": SCHEMA_RENDER_VERSION,
              "knowledge_scopes": {database_id: scope.model_dump() for database_id, scope in knowledge_scopes.items()}}
    if context_source == "retrieval":
        fields.update(method=method, rerank=rerank, top_k=RETRIEVAL_TOP_K)
    return fields


def sql_run_config(cases_path: Path, settings: Settings, provider: str, evidence_mode: str, *,
                   workers: int | None = None, context_source: ContextSource | None = None,
                   structure_format: StructureFormat = "columns",
                   knowledge_scopes: dict[str, KnowledgeScope] | None = None,
                   method: RetrievalMethod = "hybrid", rerank: bool = True,
                   relevant_tables: dict[int, list[str]] | None = None) -> dict:
    """SQL 评测写进运行配置的参数；命令行和 run_direct/run_graph 用同一份。

    参数：cases_path 为题集路径，settings 为运行配置，provider 和 evidence_mode 为评测模式；
        workers 和 context_source 只有图评测传（direct 评测不传，配置里也就没有这两项）；
        structure_format 和 knowledge_scopes 只在 context_source 为 knowledge 或 retrieval 时记下，另加渲染
        版本；method、rerank 只在 retrieval 时记下，另加 top_k。relevant_tables（题号 → Relevant Set 的表）
        只用来检查 retrieval 的每道题都有 Relevant Set，不写进配置。
    返回：不含题目、SQL 或完整设置的配置字典；读取题集计算摘要，I/O 错误原样传播。
    异常：ValueError：knowledge 或 retrieval 来源下对照表缺了题集用到的库，或 retrieval 来源下有题号在
        relevant_tables 里找不到。命令行和 run_graph 都在建立运行记录之前调用这里，所以缺了时不会留下运行记录。
    """
    case_bytes = cases_path.read_bytes()
    if context_source in KNOWLEDGE_SOURCES:
        missing = sorted({case["db_id"] for case in json.loads(case_bytes)} - set(knowledge_scopes or {}))
        if missing:
            raise ValueError(f"no knowledge scope for databases {missing}")
    if context_source == "retrieval":
        missing_cases = [case["question_id"] for case in json.loads(case_bytes)
                         if case["question_id"] not in (relevant_tables or {})]
        if missing_cases:
            raise ValueError(f"no relevant set for questions {missing_cases}")
    return {"mode": "sql", "dataset_sha256": sha256(case_bytes).hexdigest(), "provider": provider,
            "evidence_mode": evidence_mode if provider != "fixture" else None,
            "prompt_version": PROMPT_VERSION if provider != "fixture" else None,
            "graph_version": GRAPH_VERSION if provider == "graph" else None,
            "model": settings.model_name if provider != "fixture" else None,
            "model_base_url": settings.model_base_url if provider != "fixture" else None,
            "model_temperature": settings.model_temperature if provider != "fixture" else None,
            "max_rows": settings.max_rows, "query_timeout_ms": settings.query_timeout_ms,
            "max_response_bytes": settings.max_response_bytes,
            **({} if workers is None else {"workers": workers}),
            **({} if context_source is None else {"context_source": context_source}),
            **_knowledge_fields(context_source, structure_format, knowledge_scopes, method, rerank)}


def run_direct(cases_path: Path, settings: Settings, *, provider: str = "model",
               evidence_mode: str = "none", generator: SqlGenerator | None = None,
               run_context=None) -> EvalReport:
    """完整运行 direct 评测并保存私有报告。

    参数：题集、配置、fixture/model、none/oracle、四参生成器、可选外层上下文。
    返回：原 EvalReport；模型异常按原规则逐题记录，查库失败中止批次。
    副作用：只读数据库；model 可调用模型。顶级运行创建并关闭，嵌套只复用。
    """
    from ._direct import run_direct as perform

    if provider not in {"model", "fixture"} or evidence_mode not in {"none", "oracle"}:
        raise ValueError("invalid evaluation mode")
    owned = run_context is None
    with task_run("evaluation", settings, run_context=run_context, command="direct", private=True,
                  config=sql_run_config(cases_path, settings, provider, evidence_mode)) as context:
        result = perform(cases_path, settings, provider=provider, evidence_mode=evidence_mode,
                         generator=generator or generate_sql, run_context=context)
        secrets = (settings.model_api_key,) if settings.model_api_key != "local" else ()
        result = sanitize_private(result, secrets=secrets)
        if owned:
            context.complete(result)
        return result


def run_graph(cases_path: Path, settings: Settings, *, generator: SqlGenerator | None = None,
              workers: int = 1, context_source: ContextSource = "schema",
              structure_format: StructureFormat = "columns",
              knowledge_scopes: dict[str, KnowledgeScope] | None = None,
              knowledge_reader: Callable[[KnowledgeScope], list[RetrievalHit]] | None = None,
              method: RetrievalMethod = "hybrid", rerank: bool = True,
              retriever: Callable[[RetrievalRequest], RetrievalResult] | None = None,
              table_chunk_reader: Callable[[KnowledgeScope, list[str]], dict[str, str]] | None = None,
              relevant_tables: dict[int, list[str]] | None = None,
              run_context=None) -> EvalReport:
    """完整运行单图评测并保存私有报告。

    参数：题集、配置、固定或真实生成器、同时在跑的题数 workers（默认 1）、编译查询图用的
        context_source（默认 schema）和 structure_format（默认 columns，只对 knowledge 有效），
        knowledge / retrieval 来源要的"db_id → Knowledge Scope"对照表和知识读取函数（默认读知识库），
        retrieval 来源的检索方法、是否重排（默认 hybrid + 重排）、检索函数（默认整次运行共用一组 Embedder
        和 Reranker）、表 Chunk 读取函数和每道题 Relevant Set 的表（题号 → 表），及可选外层上下文。
    返回：EvalReport，records 按题集顺序；题内的错误记在那一题上并继续，一道预测都没有时两个比率为 None。
        knowledge / retrieval 来源另记结构写法、渲染版本和各库的 Scope；retrieval 另记 method、rerank、
        top_k、实际用到的检索版本（retrieval），以及逐题和汇总的"表是否拿全"（tables_complete）。
    异常：ValueError：knowledge / retrieval 来源下对照表缺了题集用到的库，或 retrieval 来源下有题没有
        Relevant Set；在建立运行记录之前报出（见 sql_run_config）。
    副作用：读取 SQLite，可调用模型，knowledge 来源每题读一次知识库；一个批次一份运行，子题不另建根。
    """
    from ._graph import run_graph as perform

    owned = run_context is None
    with task_run("evaluation", settings, run_context=run_context, command="graph", private=True,
                  config=sql_run_config(cases_path, settings, "graph", "none", workers=workers,
                                        context_source=context_source, structure_format=structure_format,
                                        knowledge_scopes=knowledge_scopes, method=method, rerank=rerank,
                                        relevant_tables=relevant_tables)) as context:
        # 只有 retrieval 才建检索客户端：其他来源不需要 Embedding / Rerank 配置。
        if context_source == "retrieval" and retriever is None:
            retriever = shared_retriever(method, rerank)
        result = perform(cases_path, settings, generator=generator or generate_sql, workers=workers,
                         context_source=context_source, structure_format=structure_format,
                         knowledge_scopes=knowledge_scopes or {},
                         knowledge_reader=knowledge_reader or load_scope_chunks, method=method, rerank=rerank,
                         retriever=retriever, table_chunk_reader=table_chunk_reader or load_table_chunks,
                         relevant_tables=relevant_tables, run_context=context)
        result.update(_knowledge_fields(context_source, structure_format, knowledge_scopes, method, rerank))
        secrets = (settings.model_api_key,) if settings.model_api_key != "local" else ()
        result = sanitize_private(result, secrets=secrets)
        if owned:
            context.complete(result)
        return result


__all__ = [
    "run_direct", "run_graph", "sql_run_config", "read_report", "export_public", "select_database_cases", "SqlCaseSetError",
    "CaseRecord", "CaseOutcome", "EvalReport", "EvalSummary",
]
