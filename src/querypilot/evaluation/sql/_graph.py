"""逐题运行单图的 Text2SQL 评测；与 direct-prompt baseline 分开保存。"""

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
import json
import threading
import time
from pathlib import Path

from querypilot.runtime.api import Settings
from querypilot.databases.api import execute_sql
from querypilot.sql_generation.api import PROMPT_VERSION, generate_sql
from ._compare import normalized_rows
from ._results import (
    CaseRecord, EvalReport, build_report, finish_case_record, new_case_record,
)
from querypilot.querying.api import (
    GRAPH_VERSION, ContextSource, QueryContext, SqlGenerator, StructureFormat, build_query_graph,
    query_result_row_count,
)
from querypilot.retrieval.api import (
    KnowledgeScope, RetrievalHit, RetrievalMethod, RetrievalRequest, RetrievalResult, hit_table,
    load_scope_chunks, load_table_chunks, retrieve_knowledge,
)


@dataclass
class _CaseRun:
    """一道题在工作线程里跑出来的原始结果，交给主线程比对和记录。

    token_usage 是生成这一步报告的两项用量（没走到生成时为 None）；result 是图的执行结果，
    gold_rows 是金标准 SQL 的结果行；题内出错时 error 是那个异常，
    出错之前已经拿到的部分照常保留。started 和 finished 是这道题在工作线程里的起止时刻。
    hit_tables 是 retrieval 来源下 Hit 所属的表（没走到检索或检索失败时为 None），只留表名不留 Hit。
    """

    started: float
    finished: float = 0.0
    predicted_sql: str | None = None
    token_usage: dict | None = None
    result: dict | None = None
    gold_rows: list | None = None
    error: Exception | None = None
    hit_tables: set[str] | None = None


def _run_case(graph, config, query_context: QueryContext, case: dict, settings: Settings, run_context) -> _CaseRun:
    """在工作线程里跑一道题：先走查询图，再执行金标准 SQL。题内的错误记在返回值里，不抛出。"""
    run = _CaseRun(started=time.monotonic())
    database_id = case["db_id"]
    try:
        answer: dict[str, object] = {}
        for part in graph.stream({"database_id": database_id, "question": case["question"]},
                                 config=config, context=query_context, stream_mode="updates", version="v2"):
            if part["type"] != "updates":
                continue
            for delta in part["data"].values():
                if not isinstance(delta, dict):
                    continue
                if "hits" in delta:
                    run.hit_tables = {hit_table(hit) for hit in delta["hits"]}
                if "generated_sql" in delta:
                    run.predicted_sql = delta["generated_sql"]
                    run.token_usage = {key: delta[key] for key in ("prompt_tokens", "completion_tokens")}
                if "sql" in delta:
                    answer["sql"] = delta["sql"]
                if "result" in delta:
                    answer["result"] = delta["result"]
        if set(answer) != {"sql", "result"}:
            raise RuntimeError("graph stream ended without a complete answer")
        run.result = answer["result"]
        run.gold_rows = execute_sql(database_id, case["SQL"], settings, run_context=run_context).rows
    except Exception as exc:
        run.error = exc
    run.finished = time.monotonic()
    return run


def _case_record(position: int, case: dict, run: _CaseRun, graph_run_id: str,
                 relevant_tables: list[str] | None = None) -> CaseRecord:
    """把一道题跑出来的原始结果比对、定类，写成逐题记录；在主线程里调用。

    图执行成功就算有效 SQL；之后金标准 SQL 出错的题仍记为 error，但保留已经拿到的预测、行数和用量。
    relevant_tables 不为 None 时（retrieval 来源）另记 tables_complete：Relevant Set 的表是否都在 Hit 所属的
    表里；检索失败的题没有 Hit，记为 False。
    """
    record = new_case_record(position, case, evidence_used=False, graph_run_id=graph_run_id)
    if relevant_tables is not None:
        record["tables_complete"] = run.hit_tables is not None and set(relevant_tables) <= run.hit_tables
    if run.predicted_sql is not None:
        record["predicted_sql"] = run.predicted_sql
        record.update(run.token_usage)
    if run.result is not None:
        record["valid_sql"] = True
        record["row_count"] = run.result["row_count"]
    if run.error is not None:
        # 带类别的错误（检索、知识读取）记类别，否则记异常类名。
        record["error_type"] = getattr(run.error, "error_type", None) or type(run.error).__name__
        record["error"] = f"{type(run.error).__name__}: {run.error}"
    else:
        record["execution_match"] = normalized_rows(run.result["rows"]) == normalized_rows(run.gold_rows)
    return finish_case_record(record, run.started, finished=run.finished)


def _split(records: list[CaseRecord]) -> dict:
    """一组记录按 tables_complete 分成拿全 / 没拿全，各给题数、执行匹配数和比例（没有题时比例为 None）。"""
    split = {}
    for name, complete in (("complete", True), ("incomplete", False)):
        group = [record for record in records if record["tables_complete"] is complete]
        matched = sum(record["execution_match"] for record in group)
        split[name] = {"cases": len(group), "matched": matched, "rate": matched / len(group) if group else None}
    return split


def _tables_complete_summary(records: list[CaseRecord]) -> dict:
    """retrieval 来源的"表是否拿全"汇总：整体一份，每个库一份（按库在题集里第一次出现的顺序）。"""
    databases = list(dict.fromkeys(record["database_id"] for record in records))
    return {"overall": _split(records),
            "by_database": {database: _split([r for r in records if r["database_id"] == database])
                            for database in databases}}


def run_graph(cases_path: Path, settings: Settings, *, generator: SqlGenerator = generate_sql,
              workers: int = 1, context_source: ContextSource = "schema",
              structure_format: StructureFormat = "columns",
              knowledge_scopes: dict[str, KnowledgeScope] | None = None,
              knowledge_reader: Callable[[KnowledgeScope], list[RetrievalHit]] = load_scope_chunks,
              method: RetrievalMethod = "hybrid", rerank: bool = True,
              retriever: Callable[[RetrievalRequest], RetrievalResult] = retrieve_knowledge,
              table_chunk_reader: Callable[[KnowledgeScope, list[str]], dict[str, str]] = load_table_chunks,
              relevant_tables: dict[int, list[str]] | None = None,
              run_context) -> EvalReport:
    """对一份题集逐题运行查询图并计分，返回报告。

    参数：
        cases_path：题集文件，JSON 列表，每题含 db_id、question 和金标准 SQL。
        settings：数据目录、模型和 SQL 执行上限。
        generator：SQL 生成函数；测试可注入固定结果。workers 大于 1 时会被多个线程同时调用。
        workers：同时在跑的题数，默认 1。工作线程里只跑查询图和金标准 SQL；结果比对、逐题记录
            和进度事件都在调用方的线程里。每题的 latency_ms 从这道题开始跑图量到金标准 SQL 执行完，
            不含排队和结果比对；大于 1 时会被同时进行的请求拉高，耗时只在线程数相同的运行之间比较。
        context_source：编译查询图时选的 Schema 来源，默认 schema；记进报告。
        structure_format：knowledge 来源里表结构的写法，编译查询图时传入。
        knowledge_scopes：每题调用查询图时作为运行时 context 传入的"db_id → Knowledge Scope"对照表；
            只有 knowledge 来源会用。
        knowledge_reader：knowledge 来源读出一个 Scope 全部 Chunk 的函数，默认读知识库。
        method、rerank、retriever、table_chunk_reader：retrieval 来源的检索配置、检索函数和表 Chunk 读取
            函数，编译查询图时传入；批量运行应传入共用客户端的检索函数（api.run_graph 传 shared_retriever）。
        relevant_tables：retrieval 来源下题号 → Relevant Set 的表，用来记逐题的 tables_complete；有题缺了时
            在开始前报 ValueError。
        run_context：外层运行上下文，事件和 SQL 审计都写进它；由调用方发布结果和关闭。
    返回：
        EvalReport。records 按题集顺序排列，与哪题先跑完无关。
    异常：
        题内的错误（模型失败、SQL 被拒、执行出错）记在那一题上，继续下一题。
        只有调用方的线程提交题目，处理完一道才提交下一道；所以它带着异常离开之后（它自己出错、
        Ctrl+C）不会再有新题开始，已经在跑的题（最多 workers 道）跑完后结果丢弃。
    副作用：
        每题提交时写 eval_case_started，处理完写 eval_case_finished（按完成顺序，completed_cases
        为已完成题数，case_index 为题集里的位置；workers=1 时就是题集顺序）。
    """
    case_bytes = cases_path.read_bytes()
    cases = json.loads(case_bytes)
    if not isinstance(cases, list):
        raise ValueError("evaluation cases must be a JSON list")
    batch_run_id = run_context.run_id
    # 记下每次检索实际用到的版本（方法、两个 Profile、检索配置版本），不同的才记；工作线程会同时写。
    retrieval_versions: list[dict] = []
    versions_lock = threading.Lock()
    base_retriever = retriever

    def recording_retriever(request: RetrievalRequest) -> RetrievalResult:
        """调用检索函数，把结果里的版本信息记进 retrieval_versions；不记 Hit 或查询原文。"""
        result = base_retriever(request)
        version = {"method": result.method, "embedding_profile_id": result.embedding_profile_id,
                   "rerank_profile_id": result.rerank_profile_id,
                   "retrieval_config_version": result.retrieval_config_version}
        with versions_lock:
            if version not in retrieval_versions:
                retrieval_versions.append(version)
        return result

    graph = build_query_graph(settings, generator=generator, run_context=run_context, context_source=context_source,
                              structure_format=structure_format, knowledge_reader=knowledge_reader,
                              retriever=recording_retriever, table_chunk_reader=table_chunk_reader,
                              method=method, rerank=rerank)
    query_context = QueryContext(knowledge_scopes=knowledge_scopes or {})
    # 缺 Relevant Set 的题不能按空集合判成"拿全"：开始前就拒绝。
    if context_source == "retrieval":
        missing = [case["question_id"] for case in cases if case["question_id"] not in (relevant_tables or {})]
        if missing:
            raise ValueError(f"no relevant set for questions {missing}")
    middleware = run_context.graph_middleware(row_count=query_result_row_count)
    progress = middleware.logger
    run_started = time.monotonic()
    progress.info("eval_started", extra={
        "run_id": batch_run_id, "component": "evaluation", "provider": "graph",
        "evidence_mode": "none", "total_cases": len(cases),
    })

    def case_fields(position: int) -> dict:
        """一道题的事件公共字段；position 是它在题集里从零开始的位置。"""
        case = cases[position]
        return {
            "run_id": batch_run_id, "graph_run_id": f"{batch_run_id}:case:{position + 1}",
            "component": "evaluation", "provider": "graph",
            "database_id": case["db_id"], "question_id": case.get("question_id"),
            "case_index": position + 1, "total_cases": len(cases),
        }

    # 每题一个位置：哪题先跑完都放回自己的位置，报告的顺序就与完成顺序无关。
    records: list[CaseRecord | None] = [None] * len(cases)
    waiting = iter(range(len(cases)))
    in_flight = {}
    success_count = 0
    failure_count = 0
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        def submit_next() -> None:
            """还有没提交的题就提交一道；主线程是唯一提交任务的地方。"""
            position = next(waiting, None)
            if position is None:
                return
            case = cases[position]
            fields = case_fields(position)
            progress.info("eval_case_started", extra=fields)
            config = middleware.config(
                run_id=fields["graph_run_id"], parent_run_id=batch_run_id, database_id=case["db_id"],
                question_id=case.get("question_id"), case_index=position + 1,
            )
            in_flight[pool.submit(_run_case, graph, config, query_context, case, settings, run_context)] = position

        for _ in range(workers):
            submit_next()
        while in_flight:
            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            # 先把这一批完成的结果都取出来：工作线程里有非题内的异常时在这里原样抛出，这一轮不再提交新题。
            finished = [(in_flight.pop(future), future.result()) for future in done]
            for position, run in finished:
                fields = case_fields(position)
                record = _case_record(
                    position, cases[position], run, fields["graph_run_id"],
                    relevant_tables=relevant_tables[cases[position]["question_id"]]
                    if context_source == "retrieval" else None)
                records[position] = record
                completed += 1
                success_count += int(record["execution_match"])
                failure_count += int(not record["execution_match"])
                finished_fields = {
                    **fields, "status": record["outcome"], "elapsed_ms": record["latency_ms"],
                    "completed_cases": completed, "success_count": success_count,
                    "failure_count": failure_count,
                    "eta_seconds": round((time.monotonic() - run_started) / completed * (len(cases) - completed), 1),
                }
                if "error_type" in record:
                    finished_fields["error_type"] = record["error_type"]
                progress.info("eval_case_finished", extra=finished_fields)
                # 这道题处理完了才提交下一道：主线程在这之前出错或被中断，就不会再有新题开始。
                submit_next()

    result = build_report(
        cases_path=cases_path, case_bytes=case_bytes, settings=settings,
        provider="graph", run_id=batch_run_id, records=records,
        evidence_mode="none", prompt_version=PROMPT_VERSION,
        graph_version=GRAPH_VERSION,
    )
    result["workers"] = workers
    result["context_source"] = context_source
    if context_source == "retrieval":
        result["retrieval"] = retrieval_versions
        result["tables_complete"] = _tables_complete_summary(records)
    summary = result["summary"]
    progress.info("eval_finished", extra={
        "run_id": batch_run_id, "component": "evaluation", "provider": "graph",
        "status": result["status"], "completed_cases": summary["total"],
        "total_cases": summary["total"], "success_count": summary["success"],
        "failure_count": summary["failure"],
        "elapsed_ms": round((time.monotonic() - run_started) * 1000, 3),
    })
    return result
