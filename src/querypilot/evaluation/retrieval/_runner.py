"""检索评测运行器：逐题检索，交给计分规则，组装报告。"""

from collections.abc import Callable
from hashlib import sha256
from math import ceil
from pathlib import Path
from statistics import median

from querypilot.retrieval.api import KnowledgeRetrievalError, RetrievalRequest, RetrievalResult, retrieve_knowledge
from querypilot.runtime.api import Settings, task_run

from . import _scoring
from .models import RetrievalCaseSet

TOP_K = 20
RELEVANT_SET_SOURCE = "金标准 SQL 引用的表和列（代理标注）"

Retriever = Callable[[RetrievalRequest], RetrievalResult]


def _default_retriever() -> Retriever:
    """用 .env 中的知识库只读配置调用 retrieve_knowledge。"""
    from querypilot.knowledge.api import KnowledgeStorageSettings

    storage = KnowledgeStorageSettings()
    return lambda request: retrieve_knowledge(request, storage)


def _summarize(records: list[dict]) -> dict:
    """一组逐题结果的汇总：题数、input_error 题数、检索耗时 P50/P95，再加计分规则的汇总。

    P95 用最近秩法（排序后第 ceil(0.95n) 个）；没有耗时可算时为空。
    """
    elapsed = sorted(r["elapsed_ms"] for r in records if r["elapsed_ms"] is not None)
    return {
        "cases": len(records),
        "input_error_cases": sum(r["outcome"] == "input_error" for r in records),
        "elapsed_ms_p50": median(elapsed) if elapsed else None,
        "elapsed_ms_p95": elapsed[ceil(0.95 * len(elapsed)) - 1] if elapsed else None,
        **_scoring.summarize(records),
    }


def evaluate_retrieval(cases_file: Path, settings: Settings, *, retriever: Retriever | None = None,
                       run_context=None) -> dict:
    """对一份检索题集跑一次检索评测，返回并保存报告。

    参数：
        cases_file：prepare_retrieval_cases 生成的题集文件。
        settings：运行配置（运行记录目录等）。
        retriever：检索函数，输入 RetrievalRequest，返回 RetrievalResult；默认用知识库只读配置
            调用 retrieve_knowledge。测试可替换成假检索器。
        run_context：外层运行上下文；为空时自建一份 evaluation 运行记录。
    返回：
        报告字典：config（题集 hash、各库 Knowledge Scope、检索配置、K）、records（逐题结果）
        和 summary（整体和各库的汇总）。
        报告不含问题原文、SQL 和 Hit 正文。
    副作用：
        每题调用一次检索器；写出运行记录和 result.json。
    """
    case_set = RetrievalCaseSet.model_validate_json(cases_file.read_bytes())
    retriever = retriever or _default_retriever()
    dataset_sha256 = sha256(cases_file.read_bytes()).hexdigest()
    owned = run_context is None
    with task_run("evaluation", settings, run_context=run_context, command="retrieval",
                  config={"mode": "retrieval", "dataset_sha256": dataset_sha256}) as context:
        context.event("eval_started", total_cases=len(case_set.cases))
        records, versions = [], []
        for index, case in enumerate(case_set.cases, start=1):
            request = RetrievalRequest(query=case.question, scope=case.scope, top_k=TOP_K)
            try:
                result = retriever(request)
            except KnowledgeRetrievalError as exc:
                # 查询没有有效词只影响这道题：按空的 Hit 列表计分，仍计入分母。其他检索错误中止整批。
                if exc.error_type != "input_error":
                    raise
                outcome, hits, elapsed_ms = "input_error", [], None
            else:
                # 耗时取检索结果自己测的值，只算检索本身，不含评测的开销。
                outcome, hits, elapsed_ms = "ok", result.hits, result.elapsed_ms
                version = {"method": result.method, "retrieval_config_version": result.retrieval_config_version}
                if version not in versions:
                    versions.append(version)
            records.append({
                "question_id": case.question_id, "db_id": case.db_id, "difficulty": case.difficulty,
                "frozen": case.frozen, "outcome": outcome, "elapsed_ms": elapsed_ms,
                **_scoring.score_case(case, hits),
            })
            context.event("eval_case_finished", question_id=case.question_id, case_index=index,
                          total_cases=len(case_set.cases), database_id=case.db_id, elapsed_ms=elapsed_ms,
                          status=outcome)
        report = {
            "report_version": "querypilot-retrieval-eval-v1",
            "config": {
                "dataset_sha256": dataset_sha256,
                "scopes": {case.db_id: case.scope.model_dump(mode="json") for case in case_set.cases},
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
