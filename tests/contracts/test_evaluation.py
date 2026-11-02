"""从公开评测入口检查批次行为、旧报告与新目录，不调用模型。"""

import json
import sqlite3
from importlib import import_module
from types import SimpleNamespace

import pytest

from querypilot.databases.api import DatabaseRegistry, RegistryError
from querypilot.evaluation.api import export_public, read_report, run_direct, run_graph
from querypilot.runtime.api import Settings, task_run


def _cases(tmp_path, *, missing=False, empty=False):
    """创建原创数据库和题集，返回路径与完全隔离的运行配置。"""
    data = tmp_path / "data"
    data.mkdir()
    database = data / "tiny.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE items(id INTEGER)")
        connection.executemany("INSERT INTO items VALUES (?)", [(1,), (1,), (2,)])
    DatabaseRegistry(data).register("tiny", database)
    cases = [] if empty else [
        {"question_id": 1, "db_id": "missing" if missing else "tiny", "question": "count", "SQL": "SELECT COUNT(*) FROM items"},
        {"question_id": 2, "db_id": "tiny", "question": "list", "SQL": "SELECT id FROM items"},
    ]
    source = tmp_path / "cases.json"
    source.write_text(json.dumps(cases), encoding="utf-8")
    return source, Settings(_env_file=None, data_dir=data, artifacts_dir=tmp_path / "runs")


def test_sql_root_exports_are_identical_to_the_implementation():
    """无参数；返回无；根出口的八项 SQL 对象须与实现相同，且不暴露生成器替换变量。"""
    from querypilot.evaluation import api as evaluation_api
    from querypilot.evaluation.sql import api as sql_api

    names = ("run_direct", "run_graph", "read_report", "export_public",
             "CaseRecord", "CaseOutcome", "EvalReport", "EvalSummary")
    assert set(sql_api.__all__) == set(names)
    assert all(getattr(evaluation_api, name) is getattr(sql_api, name) for name in names)
    assert not hasattr(evaluation_api, "generate_sql")


@pytest.mark.parametrize("mode", ["direct", "graph"])
def test_sql_library_run_records_category_once(tmp_path, mode):
    """参数为隔离目录和模式；返回无；直接库调用必须只创建一份带 SQL 类别的完整记录。"""
    cases, settings = _cases(tmp_path)
    report = (run_direct(cases, settings, provider="fixture") if mode == "direct" else
              run_graph(cases, settings, generator=lambda *_: "SELECT id FROM items"))
    runs = list(settings.artifacts_dir.glob("runs/*/*/*"))
    assert len(runs) == 1
    run_dir = runs[0]
    record = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    assert record["operation"] == "evaluation" and record["command"] == mode
    assert record["status"] == "completed" and record["run_id"] == report["run_id"]
    assert record["result_path"] == "private/result.json"
    assert config["parameters"]["mode"] == "sql"
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(event["event"] == "run_started" for event in events) == 1
    assert sum(event["event"] == "run_finished" for event in events) == 1
    assert read_report(run_dir) == report


@pytest.mark.parametrize("mode", ["direct", "graph"])
def test_borrowed_sql_context_does_not_rewrite_or_finish_outer_run(tmp_path, mode):
    """参数为隔离目录和模式；返回无；借用者不补写配置、发布结果或关闭外层资源。"""
    cases, settings = _cases(tmp_path)
    with task_run("evaluation", settings, command="outer", private=True,
                  config={"mode": "caller-mode"}) as context:
        config_path = context.run_dir / "config.json"
        before = config_path.read_bytes()
        report = (run_direct(cases, settings, provider="fixture", run_context=context) if mode == "direct" else
                  run_graph(cases, settings, generator=lambda *_: "SELECT id FROM items", run_context=context))
        assert config_path.read_bytes() == before
        assert not (context.run_dir / "private/result.json").exists()
        record = json.loads((context.run_dir / "run.json").read_text(encoding="utf-8"))
        assert record["status"] == "started" and record["command"] == "outer"
        assert report["run_id"] == context.run_id
        context.event("outer_probe")
        context.complete(report)
    assert list(settings.artifacts_dir.glob("runs/*/*/*")) == [context.run_dir]
    assert config_path.read_bytes() == before
    assert read_report(context.run_dir) == report


def test_direct_aborts_unknown_database_but_graph_records_and_continues(tmp_path):
    """相同缺库题集保持原 direct 中止、graph 逐题继续的差异。"""
    cases, settings = _cases(tmp_path, missing=True)
    with pytest.raises(RegistryError):
        run_direct(cases, settings, provider="fixture")
    result = run_graph(cases, settings, generator=lambda *_: "SELECT id FROM items")
    assert [item["outcome"] for item in result["records"]] == ["error", "matched"]
    assert result["summary"]["total"] == 2
    assert result["execution_accuracy"] == .5


@pytest.mark.parametrize("mode", ["direct", "graph"])
def test_empty_report_keeps_unavailable_rates(tmp_path, mode):
    """空题集没有分母，不能报告零准确率或有效SQL率。"""
    cases, settings = _cases(tmp_path, empty=True)
    report = run_direct(cases, settings, provider="fixture") if mode == "direct" else run_graph(cases, settings)
    assert report["count"] == 0
    assert report["execution_accuracy"] is None and report["valid_sql_rate"] is None
    run_dir = next(settings.artifacts_dir.glob("runs/evaluation/*/*"))
    assert read_report(run_dir) == report


def test_order_is_ignored_but_duplicate_rows_are_retained(tmp_path):
    """公开评测结果保留重复行；只改变行序仍匹配，去重则不匹配。"""
    cases, settings = _cases(tmp_path)
    payload = json.loads(cases.read_text(encoding="utf-8"))[1:]
    cases.write_text(json.dumps(payload), encoding="utf-8")
    ordered = run_direct(cases, settings, generator=lambda *_: "SELECT id FROM items ORDER BY id DESC")
    distinct = run_direct(cases, settings, generator=lambda *_: "SELECT DISTINCT id FROM items")
    assert ordered["records"][0]["outcome"] == "matched"
    assert distinct["records"][0]["outcome"] == "mismatched"


@pytest.mark.parametrize("mode", ["direct", "graph"])
def test_column_positions_remain_significant(tmp_path, mode):
    """参数为隔离目录与模式；返回无；交换列值必须得到不匹配记录。"""
    cases, settings = _cases(tmp_path)
    payload = [{"db_id": "tiny", "question": "columns", "SQL": "SELECT 1, 2"}]
    cases.write_text(json.dumps(payload), encoding="utf-8")
    operation = run_direct if mode == "direct" else run_graph
    report = operation(cases, settings, generator=lambda *_: "SELECT 2, 1")
    assert report["records"][0]["valid_sql"] is True
    assert report["records"][0]["outcome"] == "mismatched"
    assert report["execution_accuracy"] == 0


def test_schema_failure_aborts_direct_but_graph_continues(tmp_path):
    """参数为隔离目录；返回无；真实损坏库在结构读取阶段失败且保留模式差异。"""
    cases, settings = _cases(tmp_path)
    broken = settings.data_dir / "broken.sqlite"
    with sqlite3.connect(broken) as connection:
        connection.execute("CREATE TABLE broken(id INTEGER)")
    connection.close()
    DatabaseRegistry(settings.data_dir).register("broken", broken)
    broken.write_bytes(b"invalid SQLite content")
    payload = json.loads(cases.read_text(encoding="utf-8"))
    payload[0]["db_id"] = "broken"
    cases.write_text(json.dumps(payload), encoding="utf-8")
    generated = []

    def generator(question, *_):
        """参数为问题和生成配置；返回固定SQL，并记录实际进入生成的题目。"""
        generated.append(question)
        return "SELECT id FROM items"

    with pytest.raises(sqlite3.DatabaseError):
        run_direct(cases, settings, generator=generator)
    assert generated == []
    report = run_graph(cases, settings, generator=generator)
    assert generated == ["list"]
    assert [record["outcome"] for record in report["records"]] == ["error", "matched"]
    assert report["records"][0]["error_type"] == "DatabaseError"
    assert report["summary"]["no_prediction"] == 1
    assert report["valid_sql_rate"] == .5
    assert report["execution_accuracy"] == .5


@pytest.mark.parametrize("mode", ["direct", "graph"])
def test_sql_latency_keeps_original_formula_and_includes_errors(tmp_path, monkeypatch, mode):
    """参数为隔离目录、时钟替换器和模式；返回无；固定20题检验SQL旧延迟公式。"""
    cases, settings = _cases(tmp_path)
    payload = [{"db_id": "tiny", "question": str(index), "SQL": "SELECT 1"}
               for index in range(20)]
    cases.write_text(json.dumps(payload), encoding="utf-8")
    clock = SimpleNamespace(now=0.0)

    def monotonic():
        """无参数；返回受控秒数；只替换评测模块时钟，不影响SQLite超时。"""
        return clock.now

    fixed_time = SimpleNamespace(monotonic=monotonic)
    monkeypatch.setattr(import_module(f"querypilot.evaluation.sql._{mode}"), "time", fixed_time)
    monkeypatch.setattr(import_module("querypilot.evaluation.sql._results"), "time", fixed_time)

    def generator(question, *_):
        """参数为题号和生成配置；返回固定SQL；最后一题累加耗时后模拟生成失败。"""
        index = int(question)
        clock.now += (index + 1.123456) / 1000
        if index == 19:
            raise ConnectionError("fixed failure")
        return "SELECT 1"

    operation = run_direct if mode == "direct" else run_graph
    report = operation(cases, settings, generator=generator)
    assert [record["latency_ms"] for record in report["records"]] == [
        index + 1.123 for index in range(20)
    ]
    assert report["records"][-1]["outcome"] == "error"
    assert report["p50_latency_ms"] == 10.623
    # SQL 保留 index=int(n*.95)，20条时选第20条，且不能排除最长的错误题。
    assert report["p95_latency_ms"] == 20.123
    assert report["execution_accuracy"] == 19 / 20


def test_explicit_old_and_complete_new_reports_and_exclusive_export(tmp_path):
    """新目录与旧JSON都可读，旧文件不改写，导出仍含SQL且禁止覆盖。"""
    cases, settings = _cases(tmp_path)
    report = run_direct(cases, settings, provider="fixture")
    run_dir = next(settings.artifacts_dir.glob("runs/evaluation/*/*"))
    assert read_report(run_dir) == report
    old = tmp_path / "old.json"
    old.write_text(json.dumps(report), encoding="utf-8")
    before = old.read_bytes()
    assert read_report(old) == report
    destination = tmp_path / "public.json"
    export_public(old, destination)
    public = json.loads(destination.read_text(encoding="utf-8"))
    assert export_public(run_dir, tmp_path / "new-public.json") == public
    assert "predicted_sql" in public["records"][0]
    assert "question" not in public["records"][0]
    assert "model_base_url" not in public
    with pytest.raises(FileExistsError):
        export_public(old, destination)
    assert old.read_bytes() == before


def test_private_report_preserves_local_text_and_redacts_actual_credentials(tmp_path):
    """默认local占位值不破坏业务SQL，实际密钥与带秘密地址不进入新报告。"""
    cases, settings = _cases(tmp_path)
    report = run_direct(cases, settings, generator=lambda *_: "SELECT 'local-value'")
    assert report["records"][0]["predicted_sql"] == "SELECT 'local-value'"
    secret_settings = settings.model_copy(update={"model_api_key": "TOP_SECRET",
        "model_base_url": "https://user:URL_PASSWORD@example.test/v1?token=URL_TOKEN#fragment"})

    def unavailable(*_):
        """模拟异常包含真实秘密，仍应保留错误类型而清理秘密。"""
        raise ConnectionError("TOP_SECRET https://user:URL_PASSWORD@example.test/v1?token=URL_TOKEN")

    failed = run_direct(cases, secret_settings, generator=unavailable)
    raw = json.dumps(failed)
    assert all(secret not in raw for secret in ("TOP_SECRET", "URL_PASSWORD", "URL_TOKEN"))
    assert failed["model_base_url"] == "https://example.test/v1"
    assert failed["status"] == "model_unavailable"
    assert "ConnectionError" in failed["records"][0]["error"]


@pytest.mark.parametrize("damage", [
    "only_records", "missing_summary", "missing_provider", "missing_count", "missing_dataset_sha256",
    "missing_case_database_id", "missing_case_question", "missing_case_outcome", "missing_case_latency_ms",
    "case_flag_wrong_type", "count_mismatch", "summary_success_mismatch", "summary_failure_mismatch",
    "count_bool", "provider_wrong_type",
])
def test_damaged_completed_report_is_rejected_before_export(tmp_path, damage):
    """真实completed目录的结果被删字段/改类型或计数后拒绝，导出不创建目标。"""
    cases, settings = _cases(tmp_path)
    report = run_direct(cases, settings, provider="fixture")
    run_dir = next(settings.artifacts_dir.glob("runs/evaluation/*/*"))
    result_path = run_dir / "private/result.json"
    if damage == "only_records":
        report = {"records": [{}]}
    elif damage.startswith("missing_case_"):
        del report["records"][0][damage.removeprefix("missing_case_")]
    elif damage.startswith("missing_"):
        del report[damage.removeprefix("missing_")]
    elif damage == "case_flag_wrong_type":
        report["records"][0]["valid_sql"] = "true"
    elif damage == "count_mismatch":
        report["count"] += 1
    elif damage == "summary_success_mismatch":
        report["summary"]["success"] = 0
    elif damage == "summary_failure_mismatch":
        report["summary"]["failure"] = 1
    elif damage == "count_bool":
        report["count"] = True
    else:
        report["provider"] = []
    result_path.write_text(json.dumps(report), encoding="utf-8")
    before = result_path.read_bytes()
    destination = tmp_path / "must-not-exist" / "public.json"
    with pytest.raises(ValueError):
        read_report(run_dir)
    with pytest.raises(ValueError):
        export_public(run_dir, destination)
    assert not destination.parent.exists()
    assert result_path.read_bytes() == before


@pytest.mark.parametrize("mode,status", [("model", "model_unavailable"), ("graph", "no_predictions")])
def test_complete_unavailable_report_remains_readable_and_exportable(tmp_path, mode, status):
    """没有预测是合法领域结果，不能因完成目录验证而误判损坏。"""
    cases, settings = _cases(tmp_path)

    def offline(*_):
        """模拟模型不可用，禁止真实服务调用。"""
        raise ConnectionError("model offline")

    report = run_direct(cases, settings, generator=offline) if mode == "model" else run_graph(cases, settings, generator=offline)
    run_dir = next(settings.artifacts_dir.glob("runs/evaluation/*/*"))
    assert report["status"] == status
    assert read_report(run_dir) == report
    exported = export_public(run_dir, tmp_path / "public.json")
    assert exported["status"] == status and exported["execution_accuracy"] is None
    assert exported["valid_sql_rate"] is None
    assert exported["summary"]["no_prediction"] == 2
    assert all("error" not in record and "error_type" not in record and "question" not in record
               for record in exported["records"])


def test_explicit_legacy_json_keeps_permissive_field_compatibility(tmp_path):
    """旧文件不要求当前summary/provider/count，读取导出不改原JSON。"""
    source = tmp_path / "legacy.json"
    legacy = {"records": [{"predicted_sql": "SELECT 1"}], "historical_extra": "kept"}
    source.write_text(json.dumps(legacy), encoding="utf-8")
    before = source.read_bytes()
    assert read_report(source) == legacy
    public = export_public(source, tmp_path / "legacy-public.json")
    assert public["records"] == [{"predicted_sql": "SELECT 1"}]
    assert source.read_bytes() == before
