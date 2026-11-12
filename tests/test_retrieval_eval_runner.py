"""检索评测运行器：经公开入口 evaluate_retrieval 和假检索器观察报告，不连 PG。"""

from hashlib import sha256
import json
from pathlib import Path
import threading
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from pydantic import ValidationError

from querypilot.evaluation.api import (
    RetrievalCase, RetrievalCaseSet, evaluate_retrieval, retrieval_run_config,
)
from querypilot.knowledge.api import KnowledgeScope
from querypilot.retrieval.api import KnowledgeRetrievalError, RetrievalHit, RetrievalResult
from querypilot.runtime.api import Settings, task_run

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
    """按请求的方法返回结果；vector 和 hybrid 带上假的 Embedding Profile，开启重排时带上假的 Rerank Profile。"""
    profile = None if request.method == "lexical" else "fake:3:test-v1"
    rerank_profile = "fake-rerank:test-v1" if request.rerank else None
    return RetrievalResult(request_id=str(uuid4()), outcome="matched" if hits else "no_match", scope=request.scope,
                           query_sha256=sha256(request.query.encode()).hexdigest(), top_k=request.top_k,
                           knowledge_types=request.knowledge_types, hit_count=len(hits), hits=hits,
                           elapsed_ms=elapsed_ms, method=request.method, embedding_profile_id=profile,
                           rerank_profile_id=rerank_profile)


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
        "method": "lexical",
        "rerank": False,
        "workers": 1,
        "retrieval": [{"method": "lexical", "embedding_profile_id": None, "rerank_profile_id": None,
                       "retrieval_config_version": "r3-bm25-v1"}],
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


def test_method_reaches_every_request_the_run_config_and_the_report(tmp_path):
    """输入：method=hybrid；输出：每个请求都是 hybrid，运行配置和报告记下方法、Profile 和配置版本。"""
    cases_file = _cases_file(tmp_path, [_case(1), _case(2)])
    requests = []

    def retriever(request):
        requests.append(request)
        return _result(request, [_hit(1)])

    report = evaluate_retrieval(cases_file, _settings(tmp_path), method="hybrid", retriever=retriever)

    assert [request.method for request in requests] == ["hybrid", "hybrid"]
    assert report["config"]["method"] == "hybrid"
    assert report["config"]["retrieval"] == [
        {"method": "hybrid", "embedding_profile_id": "fake:3:test-v1", "rerank_profile_id": None,
         "retrieval_config_version": "r4-hybrid-v1"},
    ]
    runs = list((tmp_path / "artifacts").glob("runs/evaluation/*/*"))
    config = json.loads((runs[0] / "config.json").read_text(encoding="utf-8"))
    assert config["parameters"]["method"] == "hybrid"


def test_rerank_reaches_every_request_the_run_config_and_the_report(tmp_path):
    """输入：method=hybrid，rerank=True；输出：每个请求都开启重排，top_k 仍是 20；运行配置记下开启了重排，
    报告记下请求的开关，以及结果实际用到的 Rerank Profile 和带重排的配置版本。"""
    cases_file = _cases_file(tmp_path, [_case(1), _case(2)])
    requests = []

    def retriever(request):
        requests.append(request)
        return _result(request, [_hit(1)])

    report = evaluate_retrieval(cases_file, _settings(tmp_path), method="hybrid", rerank=True, retriever=retriever)

    assert [(request.method, request.rerank, request.top_k) for request in requests] == [("hybrid", True, 20)] * 2
    assert (report["config"]["method"], report["config"]["rerank"]) == ("hybrid", True)
    assert report["config"]["retrieval"] == [
        {"method": "hybrid", "embedding_profile_id": "fake:3:test-v1", "rerank_profile_id": "fake-rerank:test-v1",
         "retrieval_config_version": "r4-hybrid-v1+r4b-rerank-v1"},
    ]
    runs = list((tmp_path / "artifacts").glob("runs/evaluation/*/*"))
    config = json.loads((runs[0] / "config.json").read_text(encoding="utf-8"))
    assert (config["parameters"]["method"], config["parameters"]["rerank"]) == ("hybrid", True)


def test_workers_is_recorded_in_the_run_config_and_the_report_and_defaults_to_one(tmp_path):
    """输入：同一份题集跑两次，一次不传 workers，一次 workers=3；输出：运行配置和报告的 config 分别记下 1 和 3。"""
    cases_file = _cases_file(tmp_path, [_case(1), _case(2), _case(3)])
    recorded = []
    for name, kwargs in (("default", {}), ("three", {"workers": 3})):
        settings = Settings(_env_file=None, artifacts_dir=tmp_path / name)
        report = evaluate_retrieval(cases_file, settings, retriever=lambda request: _result(request, [_hit(1)]),
                                    **kwargs)
        runs = list((tmp_path / name).glob("runs/evaluation/*/*"))
        config = json.loads((runs[0] / "config.json").read_text(encoding="utf-8"))
        recorded.append((report["config"]["workers"], config["parameters"]["workers"]))
    assert recorded == [(1, 1), (3, 3)]


def test_default_retriever_needs_reranker_settings_only_when_rerank_is_on(tmp_path, monkeypatch):
    """输入：默认检索器，没有 QUERYPILOT_RERANK_* 配置，题集为空（不会发生检索，不连数据库）。

    输出：不开重排时照常跑完；开启重排时在第一题之前就因为缺配置失败，说明 Reranker 在运行开始时创建。
    """
    for name in ("BASE_URL", "MODEL", "API_KEY"):
        monkeypatch.delenv(f"QUERYPILOT_RERANK_{name}", raising=False)
    monkeypatch.chdir(tmp_path)                       # 不读仓库的 .env
    cases_file = _cases_file(tmp_path, [])
    assert evaluate_retrieval(cases_file, _settings(tmp_path))["summary"]["overall"]["cases"] == 0
    with pytest.raises(ValidationError, match="RerankSettings"):
        evaluate_retrieval(cases_file, _settings(tmp_path), rerank=True)


def test_default_retriever_shares_one_reranker_across_cases(tmp_path, monkeypatch):
    """输入：默认检索器，开启重排，两道题；retrieve_knowledge 换成假的，不连数据库也不联网。

    输出：两次检索收到的是同一个按 QUERYPILOT_RERANK_* 创建的 Reranker。
    """
    from querypilot.evaluation.retrieval import _runner
    monkeypatch.setenv("QUERYPILOT_RERANK_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("QUERYPILOT_RERANK_MODEL", "Qwen/Qwen3-Reranker-8B")
    monkeypatch.setenv("QUERYPILOT_RERANK_API_KEY", "sk-not-a-real-key")
    rerankers = []

    def fake_retrieve(request, settings, embedder=None, reranker=None):
        rerankers.append(reranker)
        return _result(request, [_hit(1)])

    monkeypatch.setattr(_runner, "retrieve_knowledge", fake_retrieve)
    evaluate_retrieval(_cases_file(tmp_path, [_case(1), _case(2)]), _settings(tmp_path), rerank=True)
    assert len(rerankers) == 2 and rerankers[0] is rerankers[1]
    assert rerankers[0].profile_id.startswith("siliconflow:Qwen/Qwen3-Reranker-8B:")


def test_workers_cases_are_in_retrieval_at_the_same_time(tmp_path):
    """输入：3 道题，workers=3；假检索器里有一道关卡，要 3 个线程都到了才一起放行。

    输出：3 道题都检索完成。一题一题串行跑时，第 1 题永远等不到另外两题，会在关卡上超时，整批失败。
    """
    barrier = threading.Barrier(3, timeout=2)

    def retriever(request):
        barrier.wait()
        return _result(request, [_hit(1)])

    report = evaluate_retrieval(_cases_file(tmp_path, [_case(1), _case(2), _case(3)]), _settings(tmp_path),
                                workers=3, retriever=retriever)

    assert [r["outcome"] for r in report["records"]] == ["ok", "ok", "ok"]


class _EventWatcher:
    """接在运行上的进度流：记下写出的每条事件；看到指定题目的完成事件时点亮一个信号。"""

    def __init__(self, question_id: int, reported: threading.Event):
        self.events, self._question_id, self._reported = [], question_id, reported

    def write(self, text: str) -> None:
        event = json.loads(text)
        self.events.append(event)
        if event["event"] == "eval_case_finished" and event["question_id"] == self._question_id:
            self._reported.set()

    def flush(self) -> None:
        pass


def test_report_keeps_case_order_while_events_follow_completion_order(tmp_path):
    """输入：两道题，workers=2；第 1 题要等主线程写出第 2 题的完成事件之后才返回，所以第 2 题一定先完成。

    输出：报告的 records 和 config.retrieval 仍按题集顺序；eval_case_finished 按完成顺序写出，
    completed_cases 从 1 递增，case_index 是这题在题集里的位置。
    """
    second_reported = threading.Event()
    watcher = _EventWatcher(2, second_reported)

    def retriever(request):
        if request.query == "first":
            assert second_reported.wait(5)
            return _result(request, [_hit(1)], elapsed_ms=10.0).model_copy(update={"embedding_profile_id": "fake:a"})
        return _result(request, [_hit(1)], elapsed_ms=20.0).model_copy(update={"embedding_profile_id": "fake:b"})

    cases_file = _cases_file(tmp_path, [_case(1, question="first"), _case(2, question="second")])
    settings = _settings(tmp_path)
    with task_run("evaluation", settings, command="retrieval",
                  config=retrieval_run_config(cases_file, "vector", False, 2)) as context:
        context.attach_stream(watcher)
        report = evaluate_retrieval(cases_file, settings, method="vector", workers=2, retriever=retriever,
                                    run_context=context)
        context.complete(report)

    assert [(r["question_id"], r["elapsed_ms"]) for r in report["records"]] == [(1, 10.0), (2, 20.0)]
    assert [v["embedding_profile_id"] for v in report["config"]["retrieval"]] == ["fake:a", "fake:b"]
    finished = [e for e in watcher.events if e["event"] == "eval_case_finished"]
    assert [(e["question_id"], e["case_index"], e["completed_cases"]) for e in finished] == [(2, 2, 1), (1, 1, 2)]
    assert "first" not in json.dumps(watcher.events)


def test_input_error_only_affects_its_own_case_when_cases_run_concurrently(tmp_path):
    """输入：3 道题同时在检索中（关卡保证），其中第 2 题没有有效词。

    输出：第 2 题记为 input_error，另外两题照常完成，整批不中止。
    """
    barrier = threading.Barrier(3, timeout=5)

    def retriever(request):
        barrier.wait()
        if request.query == "??":
            raise KnowledgeRetrievalError("input_error")
        return _result(request, [_hit(1)])

    cases = [_case(1), _case(2, question="??"), _case(3)]
    report = evaluate_retrieval(_cases_file(tmp_path, cases), _settings(tmp_path), workers=3, retriever=retriever)

    assert [(r["question_id"], r["outcome"]) for r in report["records"]] == [
        (1, "ok"), (2, "input_error"), (3, "ok")]


def test_fatal_error_lets_the_in_flight_case_finish_then_raises_the_original_error(tmp_path):
    """输入：两道题同时在检索中（关卡保证），workers=2；第 1 题重排失败，第 2 题在它失败之后才返回。

    输出：第 2 题跑完了，但结果被丢弃；抛出的是第 1 题的原始错误，运行记为 failed，没有 result.json。
    """
    barrier = threading.Barrier(2, timeout=5)
    first_failing = threading.Event()
    returned = []

    def retriever(request):
        barrier.wait()
        if request.query == "bad":
            first_failing.set()
            raise KnowledgeRetrievalError("rerank_error")
        assert first_failing.wait(5)
        returned.append(request.query)
        return _result(request, [_hit(1)])

    cases = [_case(1, question="bad"), _case(2, question="good")]
    with pytest.raises(KnowledgeRetrievalError) as caught:
        evaluate_retrieval(_cases_file(tmp_path, cases), _settings(tmp_path), workers=2, retriever=retriever)

    assert caught.value.error_type == "rerank_error" and returned == ["good"]
    runs = list((tmp_path / "artifacts").glob("runs/evaluation/*/*"))
    record = json.loads((runs[0] / "run.json").read_text(encoding="utf-8"))
    assert (record["status"], record["error_type"]) == ("failed", "rerank_error")
    assert not (runs[0] / "result.json").exists()


def test_main_thread_failure_keeps_queued_cases_from_starting(tmp_path):
    """输入：workers=1，5 道题；主线程在写第 1 题的完成事件时出错。

    这时第 2 题可能已经在检索中。测试没有办法知道停止标志是哪一刻置位的，所以让第 2 题之后的检索
    各停留最多 0.5 秒，给主线程留出置位的时间；这是本文件里唯一依赖时间的地方。
    输出：抛出主线程的错误；第 3–5 题没有被调用。
    """
    calls = []

    def retriever(request):
        calls.append(request.query)
        if request.query != "q1":
            threading.Event().wait(0.5)
        return _result(request, [_hit(1)])

    def broken_event(event, **fields):
        if event == "eval_case_finished":
            raise RuntimeError("main thread failed")

    cases_file = _cases_file(tmp_path, [_case(i, question=f"q{i}") for i in range(1, 6)])
    settings = _settings(tmp_path)
    with pytest.raises(RuntimeError, match="main thread failed"):
        with task_run("evaluation", settings, command="retrieval",
                      config=retrieval_run_config(cases_file)) as context:
            context.event = broken_event
            evaluate_retrieval(cases_file, settings, retriever=retriever, run_context=context)

    assert calls in (["q1"], ["q1", "q2"])


def test_input_error_is_recorded_on_the_case_and_the_batch_continues(tmp_path):
    cases_file = _cases_file(tmp_path, [_case(1, question="??"), _case(2)])

    def retriever(request):
        if request.query == "??":
            raise KnowledgeRetrievalError("input_error")
        return _result(request, [_hit(1)])

    report = evaluate_retrieval(cases_file, _settings(tmp_path), retriever=retriever)
    assert [(r["question_id"], r["outcome"]) for r in report["records"]] == [(1, "input_error"), (2, "ok")]


@pytest.mark.parametrize("error", [KnowledgeRetrievalError("connection_failed"),
                                   KnowledgeRetrievalError("rerank_error"), RuntimeError("boom")])
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
    built_for, requests = [], []

    def default_retriever(method, rerank):
        built_for.append((method, rerank))
        return lambda request: requests.append(request) or _result(request, [_hit(1)])

    monkeypatch.setattr(_runner, "_default_retriever", default_retriever)
    cases_file = _cases_file(tmp_path, [_case(1, question="PRIVATE RETRIEVAL QUESTION")])

    assert cli.main(["eval", "retrieval", "--cases", str(cases_file), "--method", "vector"]) == 0
    assert built_for == [("vector", False)]
    assert [(request.method, request.rerank) for request in requests] == [("vector", False)]

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    runs = list(settings.artifacts_dir.glob("runs/evaluation/*/*"))
    assert len(runs) == 1
    record = json.loads((runs[0] / "run.json").read_text(encoding="utf-8"))
    config = json.loads((runs[0] / "config.json").read_text(encoding="utf-8"))
    assert record["status"] == "completed" and config["parameters"]["mode"] == "retrieval"
    assert (config["parameters"]["method"], config["parameters"]["rerank"]) == ("vector", False)
    assert Path(output["result_file"]) == runs[0] / "result.json"
    assert output["summary"]["overall"]["cases"] == 1
    safe = captured.out + captured.err + (runs[0] / "events.jsonl").read_text(encoding="utf-8")
    assert "PRIVATE RETRIEVAL QUESTION" not in safe


def test_cli_rerank_flag_turns_reranking_on_for_the_whole_run(tmp_path, monkeypatch, capsys):
    """输入：eval retrieval --rerank（方法用默认的 lexical）；输出：默认检索器按"开启重排"创建一次，
    每个请求都开启重排，运行配置和结果文件都记下。"""
    from querypilot.entrypoints import cli
    from querypilot.evaluation.retrieval import _runner

    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    built_for, requests = [], []

    def default_retriever(method, rerank):
        built_for.append((method, rerank))
        return lambda request: requests.append(request) or _result(request, [_hit(1)])

    monkeypatch.setattr(_runner, "_default_retriever", default_retriever)
    cases_file = _cases_file(tmp_path, [_case(1), _case(2)])

    assert cli.main(["eval", "retrieval", "--cases", str(cases_file), "--rerank"]) == 0
    assert built_for == [("lexical", True)]
    assert [(request.method, request.rerank) for request in requests] == [("lexical", True)] * 2

    output = json.loads(capsys.readouterr().out)
    runs = list(settings.artifacts_dir.glob("runs/evaluation/*/*"))
    config = json.loads((runs[0] / "config.json").read_text(encoding="utf-8"))
    assert (config["parameters"]["method"], config["parameters"]["rerank"]) == ("lexical", True)
    report = json.loads(Path(output["result_file"]).read_text(encoding="utf-8"))
    assert report["config"]["retrieval"] == [
        {"method": "lexical", "embedding_profile_id": None, "rerank_profile_id": "fake-rerank:test-v1",
         "retrieval_config_version": "r3-bm25-v1+r4b-rerank-v1"},
    ]


def test_cli_workers_flag_reaches_the_runner_and_the_run_config(tmp_path, monkeypatch, capsys):
    """输入：eval retrieval --workers 4；输出：运行配置和结果文件的 config 都记下 4。"""
    from querypilot.entrypoints import cli
    from querypilot.evaluation.retrieval import _runner

    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(_runner, "_default_retriever",
                        lambda method, rerank: lambda request: _result(request, [_hit(1)]))
    cases_file = _cases_file(tmp_path, [_case(1), _case(2)])

    assert cli.main(["eval", "retrieval", "--cases", str(cases_file), "--workers", "4"]) == 0

    output = json.loads(capsys.readouterr().out)
    runs = list(settings.artifacts_dir.glob("runs/evaluation/*/*"))
    config = json.loads((runs[0] / "config.json").read_text(encoding="utf-8"))
    report = json.loads(Path(output["result_file"]).read_text(encoding="utf-8"))
    assert (config["parameters"]["workers"], report["config"]["workers"]) == (4, 4)
