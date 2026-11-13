"""检索评测运行器：用线程池逐题检索，交给计分规则，组装报告。"""

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from hashlib import sha256
from math import ceil
from pathlib import Path
from statistics import median

from querypilot.knowledge.api import EmbeddingSettings, KnowledgeStorageSettings, OpenAIEmbedder
from querypilot.retrieval.api import (
    KnowledgeRetrievalError, RerankSettings, RetrievalMethod, RetrievalRequest, RetrievalResult,
    SiliconFlowReranker, retrieve_knowledge,
)
from querypilot.runtime.api import Settings, task_run

from . import _scoring
from .models import RetrievalCaseSet

# 检索预算：每题检索一次取这么多个 Hit，所有指标都从这份排序算出。
TOP_K = 20
RELEVANT_SET_SOURCE = "金标准 SQL 引用的表和列（代理标注）"

Retriever = Callable[[RetrievalRequest], RetrievalResult]


def retrieval_run_config(cases_file: Path, method: RetrievalMethod = "lexical", rerank: bool = False,
                         workers: int = 1) -> dict:
    """检索评测运行记录的安全配置：评测类别、题集 hash、检索方法、是否重排和线程数。CLI 建外层运行时也用这一份。"""
    return {"mode": "retrieval", "dataset_sha256": sha256(cases_file.read_bytes()).hexdigest(), "method": method,
            "rerank": rerank, "workers": workers}


def _default_retriever(method: RetrievalMethod, rerank: bool) -> Retriever:
    """用 .env 中的知识库只读配置调用 retrieve_knowledge。

    vector 和 hybrid 在这里按 QUERYPILOT_EMBEDDING_* 创建一个 Embedder，所有题共用同一个客户端；
    lexical 不创建，不需要 Embedding 配置。开启重排时按 QUERYPILOT_RERANK_* 创建一个 Reranker，
    所有题共用；不开时不创建，不需要 Reranker 配置。
    """
    storage = KnowledgeStorageSettings()
    embedder = None if method == "lexical" else OpenAIEmbedder(EmbeddingSettings())
    reranker = SiliconFlowReranker(RerankSettings()) if rerank else None
    return lambda request: retrieve_knowledge(request, storage, embedder, reranker)


def _summarize(records: list[dict]) -> dict:
    """一组逐题结果的汇总：题数、input_error 题数、检索耗时 P50/P95，以及计分规则的汇总 scores。

    耗时只统计有检索结果的题（不含 input_error 题）。P95 用最近秩法（排序后第 ceil(0.95n) 个）；
    没有耗时可算时为空。
    """
    elapsed = sorted(r["elapsed_ms"] for r in records if r["elapsed_ms"] is not None)
    return {
        "cases": len(records),
        "input_error_cases": sum(r["outcome"] == "input_error" for r in records),
        "elapsed_ms_p50": median(elapsed) if elapsed else None,
        "elapsed_ms_p95": elapsed[ceil(0.95 * len(elapsed)) - 1] if elapsed else None,
        "scores": _scoring.summarize([r["scores"] for r in records]),
    }


def _retrieve_case(retriever: Retriever, request: RetrievalRequest) -> RetrievalResult | None:
    """在工作线程里检索一道题。查询没有有效词（input_error）时返回 None，其他错误原样抛出。"""
    try:
        return retriever(request)
    except KnowledgeRetrievalError as exc:
        if exc.error_type != "input_error":
            raise
        return None


def evaluate_retrieval(cases_file: Path, settings: Settings, *, method: RetrievalMethod = "lexical",
                       rerank: bool = False, workers: int = 1, retriever: Retriever | None = None,
                       run_context=None) -> dict:
    """对一份检索题集跑一次检索评测，返回并保存报告。

    参数：
        cases_file：prepare_retrieval_cases 生成的题集文件。
        settings：运行配置（运行记录目录等）。
        method：每题使用的检索方法，默认 lexical。vector 和 hybrid 会调用 Embedding 服务，产生费用。
        rerank：每题检索后是否重排，默认关闭，可与任何 method 组合。开启后每题调用一次 Reranker 服务，
            产生费用；top_k 等于重排的候选数，所以重排只改变这 20 个 Hit 的顺序。
        workers：同时检索的线程数，默认 1。只有检索在工作线程里做，计分、逐题记录和进度事件都在
            调用方的线程里。大于 1 时每题的 elapsed_ms 会被同时进行的请求拉高，耗时只在 workers=1 的
            运行之间比较。
        retriever：检索函数，输入 RetrievalRequest，返回 RetrievalResult；默认用知识库只读配置
            调用 retrieve_knowledge。测试可替换成假检索器。workers 大于 1 时会被多个线程同时调用。
        run_context：外层运行上下文；为空时自建一份 evaluation 运行记录。
    返回：
        报告字典：config（题集 hash、各库 Knowledge Scope、请求的检索方法、是否重排和线程数，以及结果实际
        用到的方法、Embedding Profile、Rerank Profile 和配置版本、K）、records（逐题结果，
        计分规则的字段在 scores 下）和 summary（整体和各库的汇总）。
        records 和 config 里的检索配置列表按题集顺序排列，与哪题先检索完无关。
        报告不含问题原文、SQL 和 Hit 正文。
    异常：
        查询没有有效词（input_error）只记在那一题上。其他错误中止整批并原样抛出，不写 result.json。
        只有调用方的线程提交题目，同时在检索中的题不超过 workers 道，处理完一道才提交下一道；
        所以它带着异常离开之后（某题的错误、它自己出错、Ctrl+C）不会再有新题开始，已经在检索中
        的题（最多 workers 道）跑完后结果丢弃。
    副作用：
        每题至多调用一次检索器；每题检索完成时写一条 eval_case_finished 事件（按完成顺序，
        completed_cases 为已完成题数，case_index 为题集里的位置；同一时刻完成的几道题之间
        顺序不定，workers=1 时就是题集顺序）；写出运行记录和 result.json。
    """
    raw = cases_file.read_bytes()
    case_set = RetrievalCaseSet.model_validate_json(raw)
    dataset_sha256 = sha256(raw).hexdigest()
    retriever = retriever or _default_retriever(method, rerank)
    owned = run_context is None
    with task_run("evaluation", settings, run_context=run_context, command="retrieval",
                  config=retrieval_run_config(cases_file, method, rerank, workers)) as context:
        context.event("eval_started", total_cases=len(case_set.cases))
        cases = case_set.cases
        # 每题一个位置：哪题先检索完都放回自己的位置，报告的顺序就与完成顺序无关
        records: list[dict | None] = [None] * len(cases)
        case_versions: list[dict | None] = [None] * len(cases)
        waiting = iter(enumerate(cases))
        in_flight = {}
        completed = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            def submit_next() -> None:
                """还有没提交的题就提交一道；主线程是唯一提交任务的地方。"""
                queued = next(waiting, None)
                if queued is not None:
                    position, case = queued
                    request = RetrievalRequest(query=case.question, scope=case.scope, method=method, rerank=rerank,
                                               top_k=TOP_K)
                    in_flight[pool.submit(_retrieve_case, retriever, request)] = position

            for _ in range(workers):
                submit_next()
            while in_flight:
                done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                # 先把这一批完成的结果都取出来：有致命错误就在这里原样抛出，这一轮不再提交新题。
                finished = [(in_flight.pop(future), future.result()) for future in done]
                for position, result in finished:
                    case = cases[position]
                    outcome = "ok" if result is not None else "input_error"
                    if result is not None:
                        case_versions[position] = {
                            "method": result.method, "embedding_profile_id": result.embedding_profile_id,
                            "rerank_profile_id": result.rerank_profile_id,
                            "retrieval_config_version": result.retrieval_config_version
                        }
                    # input_error 的题按空的 Hit 列表计分，仍计入分母。耗时取检索结果自己测的值，不含评测的开销
                    elapsed_ms = result.elapsed_ms if result is not None else None
                    records[position] = {
                        "question_id": case.question_id, "db_id": case.db_id, "difficulty": case.difficulty,
                        "frozen": case.frozen, "outcome": outcome, "elapsed_ms": elapsed_ms,
                        "scores": _scoring.score_case(case.relevant_tables, case.relevant_columns,
                                                      result.hits if result is not None else []),
                    }
                    completed += 1
                    context.event("eval_case_finished", question_id=case.question_id, case_index=position + 1,
                                  completed_cases=completed, total_cases=len(cases), database_id=case.db_id,
                                  elapsed_ms=elapsed_ms, status=outcome)
                    # 这道题处理完了才提交下一道：主线程在这之前出错或被中断，就不会再有新题开始。
                    submit_next()
        versions = []
        for version in case_versions:
            if version is not None and version not in versions:
                versions.append(version)
        report = {
            "report_version": "querypilot-retrieval-eval-v1",
            "config": {
                "dataset_sha256": dataset_sha256,
                "scopes": {case.db_id: case.scope.model_dump(mode="json") for case in case_set.cases},
                "method": method,
                "rerank": rerank,
                "workers": workers,
                "retrieval": versions,
                "top_k": TOP_K,
                "table_ks": list(_scoring.TABLE_KS),
                "column_k": _scoring.COLUMN_K,
                "relevant_set_source": RELEVANT_SET_SOURCE,
            },
            "records": records,
            "summary": {
                "overall": _summarize(records),
                "by_database": {db_id: _summarize([r for r in records if r["db_id"] == db_id])
                                for db_id in dict.fromkeys(r["db_id"] for r in records)},
            },
        }
        context.event("eval_finished", total_cases=len(case_set.cases))
        if owned:
            context.complete(report)
        return report
