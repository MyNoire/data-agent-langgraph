"""检索评测运行器：逐题检索，交给计分规则，组装报告。"""

from collections.abc import Callable
from hashlib import sha256
from math import ceil
from pathlib import Path
from statistics import median

from querypilot.knowledge.api import EmbeddingSettings, KnowledgeStorageSettings, OpenAIEmbedder
from querypilot.retrieval.api import (
    KnowledgeRetrievalError, RetrievalMethod, RetrievalRequest, RetrievalResult, retrieve_knowledge,
)
from querypilot.runtime.api import Settings, task_run

from . import _scoring
from .models import RetrievalCaseSet

# 检索预算：每题检索一次取这么多个 Hit，所有指标都从这份排序算出。
TOP_K = 20
RELEVANT_SET_SOURCE = "金标准 SQL 引用的表和列（代理标注）"

Retriever = Callable[[RetrievalRequest], RetrievalResult]


def retrieval_run_config(cases_file: Path, method: RetrievalMethod = "lexical") -> dict:
    """检索评测运行记录的安全配置：评测类别、题集 hash 和检索方法。CLI 建外层运行时也用这一份。"""
    return {"mode": "retrieval", "dataset_sha256": sha256(cases_file.read_bytes()).hexdigest(), "method": method}


def _default_retriever(method: RetrievalMethod) -> Retriever:
    """用 .env 中的知识库只读配置调用 retrieve_knowledge。

    vector 和 hybrid 在这里按 QUERYPILOT_EMBEDDING_* 创建一个 Embedder，所有题共用同一个客户端；
    lexical 不创建，不需要 Embedding 配置。
    """
    storage = KnowledgeStorageSettings()
    embedder = None if method == "lexical" else OpenAIEmbedder(EmbeddingSettings())
    return lambda request: retrieve_knowledge(request, storage, embedder)


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


def evaluate_retrieval(cases_file: Path, settings: Settings, *, method: RetrievalMethod = "lexical",
                       retriever: Retriever | None = None, run_context=None) -> dict:
    """对一份检索题集跑一次检索评测，返回并保存报告。

    参数：
        cases_file：prepare_retrieval_cases 生成的题集文件。
        settings：运行配置（运行记录目录等）。
        method：每题使用的检索方法，默认 lexical。vector 和 hybrid 会调用 Embedding 服务，产生费用。
        retriever：检索函数，输入 RetrievalRequest，返回 RetrievalResult；默认用知识库只读配置
            调用 retrieve_knowledge。测试可替换成假检索器。
        run_context：外层运行上下文；为空时自建一份 evaluation 运行记录。
    返回：
        报告字典：config（题集 hash、各库 Knowledge Scope、检索方法，以及结果实际用到的方法、
        Embedding Profile 和配置版本、K）、records（逐题结果，
        计分规则的字段在 scores 下）和 summary（整体和各库的汇总）。
        报告不含问题原文、SQL 和 Hit 正文。
    副作用：
        每题调用一次检索器；写出运行记录和 result.json。
    """
    raw = cases_file.read_bytes()
    case_set = RetrievalCaseSet.model_validate_json(raw)
    dataset_sha256 = sha256(raw).hexdigest()
    retriever = retriever or _default_retriever(method)
    owned = run_context is None
    with task_run("evaluation", settings, run_context=run_context, command="retrieval",
                  config={"mode": "retrieval", "dataset_sha256": dataset_sha256, "method": method}) as context:
        context.event("eval_started", total_cases=len(case_set.cases))
        records, versions = [], []
        for index, case in enumerate(case_set.cases, start=1):
            request = RetrievalRequest(query=case.question, scope=case.scope, method=method, top_k=TOP_K)
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
                version = {"method": result.method, "embedding_profile_id": result.embedding_profile_id,
                           "retrieval_config_version": result.retrieval_config_version}
                if version not in versions:
                    versions.append(version)
            records.append({
                "question_id": case.question_id, "db_id": case.db_id, "difficulty": case.difficulty,
                "frozen": case.frozen, "outcome": outcome, "elapsed_ms": elapsed_ms,
                "scores": _scoring.score_case(case.relevant_tables, case.relevant_columns, hits),
            })
            context.event("eval_case_finished", question_id=case.question_id, case_index=index,
                          total_cases=len(case_set.cases), database_id=case.db_id, elapsed_ms=elapsed_ms,
                          status=outcome)
        report = {
            "report_version": "querypilot-retrieval-eval-v1",
            "config": {
                "dataset_sha256": dataset_sha256,
                "scopes": {case.db_id: case.scope.model_dump(mode="json") for case in case_set.cases},
                "method": method,
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
