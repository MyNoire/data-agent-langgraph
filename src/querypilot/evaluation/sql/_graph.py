"""逐题运行单图的 Text2SQL 评测；与 direct-prompt baseline 分开保存。"""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
import json
import time
from pathlib import Path

from querypilot.runtime.api import Settings
from querypilot.databases.api import execute_sql
from querypilot.sql_generation.api import PROMPT_VERSION, generate_sql
from ._compare import normalized_rows
from ._results import (
    CaseRecord, EvalReport, build_report, finish_case_record, new_case_record,
)
from querypilot.querying.api import SqlGenerator, build_query_graph, query_result_row_count


GRAPH_VERSION = "single-v1"


@dataclass
class _CaseRun:
    """一道题在工作线程里跑出来的原始结果，交给主线程比对和记录。

    token_usage 是生成这一步报告的两项用量（没走到生成时为 None）；result 是图的执行结果，
    gold_rows 是金标准 SQL 的结果行；题内出错时 error 是那个异常，
    出错之前已经拿到的部分照常保留。started 和 finished 是这道题在工作线程里的起止时刻。
    """

    started: float
    finished: float = 0.0
    predicted_sql: str | None = None
    token_usage: dict | None = None
    result: dict | None = None
    gold_rows: list | None = None
    error: Exception | None = None


def _run_case(graph, config, case: dict, settings: Settings, run_context) -> _CaseRun:
    """在工作线程里跑一道题：先走查询图，再执行金标准 SQL。题内的错误记在返回值里，不抛出。"""
    run = _CaseRun(started=time.monotonic())
    database_id = case["db_id"]
    try:
        answer: dict[str, object] = {}
        for part in graph.stream({"database_id": database_id, "question": case["question"]},
                                 config=config, stream_mode="updates", version="v2"):
            if part["type"] != "updates":
                continue
            for delta in part["data"].values():
                if not isinstance(delta, dict):
                    continue
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


def run_graph(cases_path: Path, settings: Settings, *, generator: SqlGenerator = generate_sql,
              workers: int = 1, run_context) -> EvalReport:
    """对一份题集逐题运行查询图并计分，返回报告。

    参数：
        cases_path：题集文件，JSON 列表，每题含 db_id、question 和金标准 SQL。
        settings：数据目录、模型和 SQL 执行上限。
        generator：SQL 生成函数；测试可注入固定结果。workers 大于 1 时会被多个线程同时调用。
        workers：同时在跑的题数，默认 1。工作线程里只跑查询图和金标准 SQL；结果比对、逐题记录
            和进度事件都在调用方的线程里。大于 1 时每题的 latency_ms 会被同时进行的请求拉高，
            耗时只在线程数相同的运行之间比较。
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
    graph = build_query_graph(settings, generator=generator, run_context=run_context)
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
            in_flight[pool.submit(_run_case, graph, config, case, settings, run_context)] = position

        for _ in range(workers):
            submit_next()
        while in_flight:
            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            finished = [(in_flight.pop(future), future.result()) for future in done]
            for position, run in finished:
                fields = case_fields(position)
                record = new_case_record(position, cases[position], evidence_used=False,
                                         graph_run_id=fields["graph_run_id"])
                if run.predicted_sql is not None:
                    record["predicted_sql"] = run.predicted_sql
                    record.update(run.token_usage)
                if run.result is not None:
                    record["valid_sql"] = True
                    record["row_count"] = run.result["row_count"]
                if run.error is not None:
                    record["error_type"] = type(run.error).__name__
                    record["error"] = f"{type(run.error).__name__}: {run.error}"
                else:
                    record["execution_match"] = normalized_rows(run.result["rows"]) == normalized_rows(run.gold_rows)
                finish_case_record(record, run.started, finished=run.finished)
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
    summary = result["summary"]
    progress.info("eval_finished", extra={
        "run_id": batch_run_id, "component": "evaluation", "provider": "graph",
        "status": result["status"], "completed_cases": summary["total"],
        "total_cases": summary["total"], "success_count": summary["success"],
        "failure_count": summary["failure"],
        "elapsed_ms": round((time.monotonic() - run_started) * 1000, 3),
    })
    return result
