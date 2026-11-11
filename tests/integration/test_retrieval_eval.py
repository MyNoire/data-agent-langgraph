"""检索评测端到端：真实 PG 和真实 retrieve_knowledge 经 evaluate_retrieval 跑出报告。

复用 test_knowledge_retrieval 的 retrieval_source：导入本次唯一的原创 Corpus，结束后按身份精确删除。
"""

from querypilot.evaluation.api import RetrievalCase, RetrievalCaseSet, evaluate_retrieval
from querypilot.retrieval.api import retrieve_knowledge
from querypilot.runtime.api import Settings
from _bm25_fixtures import live_bm25_settings, unchanged_live_knowledge  # noqa: F401  （pytest 夹具）
from test_knowledge_retrieval import retrieval_source  # noqa: F401  （pytest 夹具）


def test_real_retriever_runs_through_evaluation(retrieval_source, tmp_path):  # noqa: F811
    storage, _, scope = retrieval_source
    cases = [
        RetrievalCase(question_id=number, db_id=scope.database_id, difficulty="simple", question=question,
                      frozen=False, scope=scope, relevant_tables=["items"],
                      relevant_columns=[{"table_name": "items", "column_name": "label"}])
        for number, question in ((1, "activation label"), (2, "请问的谢谢"), (3, "qpnomatch9f8e7d6c"))
    ]
    cases_file = tmp_path / "retrieval_cases.json"
    cases_file.write_text(RetrievalCaseSet(cases=cases).model_dump_json(), encoding="utf-8")

    report = evaluate_retrieval(cases_file, Settings(_env_file=None, artifacts_dir=tmp_path / "artifacts"),
                                retriever=lambda request: retrieve_knowledge(request, storage))

    assert [(r["question_id"], r["outcome"]) for r in report["records"]] == [
        (1, "ok"), (2, "input_error"), (3, "ok"),
    ]
    assert report["config"]["retrieval"] == [
        {"method": "lexical", "embedding_profile_id": None, "rerank_profile_id": None,
         "retrieval_config_version": "r3-bm25-v1"},
    ]
    assert report["summary"]["overall"]["cases"] == 3
    assert report["summary"]["overall"]["input_error_cases"] == 1
