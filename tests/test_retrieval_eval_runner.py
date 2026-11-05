"""检索评测运行器：经公开入口 evaluate_retrieval 和假检索器观察报告，不连 PG。"""

from hashlib import sha256
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from querypilot.evaluation.api import RetrievalCase, RetrievalCaseSet, evaluate_retrieval
from querypilot.knowledge.api import KnowledgeScope
from querypilot.retrieval.api import KnowledgeRetrievalError, RetrievalHit, RetrievalResult
from querypilot.runtime.api import Settings

SCOPE = KnowledgeScope(corpus_id="a" * 64, database_id="demo", schema_version="b" * 64)


def _case(question_id: int, *, question: str = "How many items are active?", frozen: bool = False,
          db_id: str = "demo", relevant_tables: tuple[str, ...] = ("items",),
          relevant_columns: tuple[tuple[str, str], ...] = (("items", "label"),)) -> RetrievalCase:
    scope = SCOPE.model_copy(update={"database_id": db_id})
    return RetrievalCase(question_id=question_id, db_id=db_id, difficulty="simple", question=question,
                         frozen=frozen, scope=scope, relevant_tables=list(relevant_tables),
                         relevant_columns=[{"table_name": table, "column_name": column}
                                           for table, column in relevant_columns])


def _cases_file(tmp_path: Path, cases: list[RetrievalCase]) -> Path:
    path = tmp_path / "retrieval_cases.json"
    path.write_text(RetrievalCaseSet(cases=cases).model_dump_json(), encoding="utf-8")
    return path


def _hit(rank: int, knowledge_type: str = "column", table: str | None = "items",
         column: str | None = "label") -> RetrievalHit:
    return RetrievalHit(rank=rank, score=10.0 / rank, chunk_id=str(uuid5(NAMESPACE_URL, f"{table}.{column}.{rank}")), chunk_key=f"key-{rank}",
                        knowledge_type=knowledge_type, title="t", content="secret hit text",
                        content_hash="c" * 64, schema_name="main", table_name=table, column_name=column)


def _result(request, hits: list[RetrievalHit], elapsed_ms: float = 1.0) -> RetrievalResult:
    return RetrievalResult(request_id=str(uuid4()), outcome="matched" if hits else "no_match", scope=request.scope,
                           query_sha256=sha256(request.query.encode()).hexdigest(), top_k=request.top_k,
                           knowledge_types=request.knowledge_types, hit_count=len(hits), hits=hits,
                           elapsed_ms=elapsed_ms)


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, artifacts_dir=tmp_path / "artifacts")


def test_each_case_is_retrieved_once_and_recorded_without_question_text(tmp_path):
    cases_file = _cases_file(tmp_path, [_case(7, question="Which secret labels?"), _case(9, frozen=True)])
    requests = []

    def retriever(request):
        requests.append(request)
        return _result(request, [_hit(1)])

    report = evaluate_retrieval(cases_file, _settings(tmp_path), retriever=retriever)

    assert [(r.query, r.scope, r.top_k, r.knowledge_types) for r in requests] == [
        ("Which secret labels?", SCOPE, 20, ["table", "column", "relationship"]),
        ("How many items are active?", SCOPE, 20, ["table", "column", "relationship"]),
    ]
    assert [(r["question_id"], r["db_id"], r["difficulty"], r["frozen"], r["outcome"]) for r in report["records"]] == [
        (7, "demo", "simple", False, "ok"), (9, "demo", "simple", True, "ok"),
    ]
    assert [r["elapsed_ms"] for r in report["records"]] == [1.0, 1.0]
    assert report["config"] == {
        "dataset_sha256": sha256(cases_file.read_bytes()).hexdigest(),
        "scopes": {"demo": SCOPE.model_dump(mode="json")},
        "retrieval": [{"method": "lexical", "retrieval_config_version": "r3-bm25-v1"}],
        "top_k": 20,
        "table_ks": [5, 10],
        "column_k": 10,
        "relevant_set_source": "金标准 SQL 引用的表和列（代理标注）",
    }

    runs = list((tmp_path / "artifacts").glob("runs/evaluation/*/*"))
    assert len(runs) == 1
    saved = (runs[0] / "result.json").read_text(encoding="utf-8")
    assert json.loads(saved)["records"] == report["records"]
    assert "Which secret labels?" not in saved and "secret hit text" not in saved


def test_input_error_is_recorded_on_the_case_and_the_batch_continues(tmp_path):
    cases_file = _cases_file(tmp_path, [_case(1, question="??"), _case(2)])

    def retriever(request):
        if request.query == "??":
            raise KnowledgeRetrievalError("input_error")
        return _result(request, [_hit(1)])

    report = evaluate_retrieval(cases_file, _settings(tmp_path), retriever=retriever)
    assert [(r["question_id"], r["outcome"]) for r in report["records"]] == [(1, "input_error"), (2, "ok")]


@pytest.mark.parametrize("error", [KnowledgeRetrievalError("connection_failed"), RuntimeError("boom")])
def test_other_retrieval_errors_abort_the_batch(tmp_path, error):
    cases_file = _cases_file(tmp_path, [_case(1), _case(2)])
    calls = []

    def retriever(request):
        calls.append(request)
        raise error

    with pytest.raises(type(error)):
        evaluate_retrieval(cases_file, _settings(tmp_path), retriever=retriever)
    assert len(calls) == 1
    runs = list((tmp_path / "artifacts").glob("runs/evaluation/*/*"))
    record = json.loads((runs[0] / "run.json").read_text(encoding="utf-8"))
    assert record["status"] == "failed" and not (runs[0] / "result.json").exists()
    assert record["error_type"] == getattr(error, "error_type", type(error).__name__)


def test_summary_counts_cases_and_latency_overall_and_by_database(tmp_path):
    cases = [_case(1, question="a"), _case(2, question="b"), _case(3, question="??"),
             _case(4, question="c", db_id="other")]
    elapsed = {"a": 10.0, "b": 20.0, "c": 40.0}

    def retriever(request):
        if request.query == "??":
            raise KnowledgeRetrievalError("input_error")
        return _result(request, [_hit(1)], elapsed_ms=elapsed[request.query])

    report = evaluate_retrieval(_cases_file(tmp_path, cases), _settings(tmp_path), retriever=retriever)

    assert report["records"][2]["elapsed_ms"] is None
    # 计分规则的汇总在 scores 下，由计分测试覆盖；这里只看运行器自己的字段和分组。
    runner_fields = {group: {key: value for key, value in summary.items() if key != "scores"}
                     for group, summary in [("overall", report["summary"]["overall"]),
                                            *report["summary"]["by_database"].items()]}
    assert runner_fields == {
        "overall": {"cases": 4, "input_error_cases": 1, "elapsed_ms_p50": 20.0, "elapsed_ms_p95": 40.0},
        "demo": {"cases": 3, "input_error_cases": 1, "elapsed_ms_p50": 15.0, "elapsed_ms_p95": 20.0},
        "other": {"cases": 1, "input_error_cases": 0, "elapsed_ms_p50": 40.0, "elapsed_ms_p95": 40.0},
    }
    assert list(report["summary"]["by_database"]) == ["demo", "other"]


def test_cli_runs_one_recorded_evaluation_and_prints_locations(tmp_path, monkeypatch, capsys):
    from querypilot.entrypoints import cli
    from querypilot.evaluation.retrieval import _runner

    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(_runner, "_default_retriever", lambda: lambda request: _result(request, [_hit(1)]))
    cases_file = _cases_file(tmp_path, [_case(1, question="PRIVATE RETRIEVAL QUESTION")])

    assert cli.main(["eval", "retrieval", "--cases", str(cases_file)]) == 0

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    runs = list(settings.artifacts_dir.glob("runs/evaluation/*/*"))
    assert len(runs) == 1
    record = json.loads((runs[0] / "run.json").read_text(encoding="utf-8"))
    config = json.loads((runs[0] / "config.json").read_text(encoding="utf-8"))
    assert record["status"] == "completed" and config["parameters"]["mode"] == "retrieval"
    assert Path(output["result_file"]) == runs[0] / "result.json"
    assert output["summary"]["overall"]["cases"] == 1
    safe = captured.out + captured.err + (runs[0] / "events.jsonl").read_text(encoding="utf-8")
    assert "PRIVATE RETRIEVAL QUESTION" not in safe
