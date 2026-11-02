"""HTTP真实入口的任务归属、信息分级和错误优先级。"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from test_task_runs import registered_settings
from querypilot.entrypoints.http import create_app
from querypilot.evaluation.api import run_direct
from querypilot.runtime.api import read_run_result
from querypilot.runtime import _artifacts


def test_http_read_routes_do_not_create_tasks_and_sql_owns_one_run(tmp_path):
    """健康/列表/结构仅普通日志；SQL成功响应前完整发布一份私有结果。"""
    settings = registered_settings(tmp_path)
    client = TestClient(create_app(settings))
    assert client.get("/health").status_code == 200
    assert client.get("/databases").json() == [{"database_id": "demo", "dialect": "sqlite"}]
    assert client.get("/databases/demo/schema").json()["tables"] == {"items": ["id", "label"]}
    assert not list(settings.artifacts_dir.glob("runs/*/*/*"))
    request_id = "../../EXTERNAL_REQUEST_ID"
    response = client.post("/queries", json={"database_id": "demo", "sql": "SELECT label FROM items"},
                           headers={"x-request-id": request_id})
    assert response.status_code == 200 and response.headers["x-request-id"] == request_id
    assert response.json()["rows"] == [["ROW_PRIVATE"]]
    runs = list(settings.artifacts_dir.glob("runs/query/*/*"))
    assert len(runs) == 1 and UUID(runs[0].name).hex == runs[0].name
    assert read_run_result(runs[0]) == response.json()
    safe_paths = [runs[0] / name for name in ("run.json", "config.json", "events.jsonl")]
    safe_paths += list((settings.artifacts_dir / "logs").rglob("*.jsonl"))
    safe_text = "".join(path.read_text() for path in safe_paths)
    assert "SELECT label" not in safe_text and "ROW_PRIVATE" not in safe_text
    assert all("EXTERNAL_REQUEST_ID" not in str(path.relative_to(settings.artifacts_dir)) for path in safe_paths)


@pytest.mark.parametrize("database_id,sql,status,error_type", [
    ("demo", "DELETE FROM items", 400, "SqlRejected"),
    ("missing", "SELECT label FROM items", 404, "RegistryError"),
])
def test_http_rejected_requests_have_original_status_and_failed_run(tmp_path, database_id, sql, status, error_type):
    """危险语句和未知库保留原HTTP映射，各自产生一份真实失败记录。"""
    settings = registered_settings(tmp_path)
    response = TestClient(create_app(settings)).post("/queries", json={"database_id": database_id, "sql": sql})
    assert response.status_code == status
    runs = list(settings.artifacts_dir.glob("runs/query/*/*"))
    assert len(runs) == 1
    record = json.loads((runs[0] / "run.json").read_text())
    assert record["status"] == "failed" and record["error_type"] == error_type
    assert sql not in (runs[0] / "events.jsonl").read_text()


@pytest.mark.parametrize("database_id,sql,status", [
    ("demo", "DELETE FROM items", 400),
    ("missing", "SELECT id FROM items", 404),
])
def test_http_business_error_survives_failure_record_and_app_log_errors(tmp_path, monkeypatch, database_id, sql, status):
    """业务已失败时，终态和普通日志同时写失败也不能改成HTTP500。"""
    from querypilot.entrypoints import http

    settings = registered_settings(tmp_path)
    original_write = _artifacts._write_json

    def fail_failure_record(path, value, **kwargs):
        """仅阻断临时运行目录失败终态；不影响操作前预检。"""
        if path.name == "run.json" and value.get("status") == "failed":
            raise OSError("failure record unavailable")
        return original_write(path, value, **kwargs)

    def fail_app_log(*args, **kwargs):
        """仅阻断HTTP中间件日期日志。"""
        raise OSError("app log unavailable")

    monkeypatch.setattr(_artifacts, "_write_json", fail_failure_record)
    monkeypatch.setattr(http, "log_application", fail_app_log)
    response = TestClient(create_app(settings)).post("/queries", json={"database_id": database_id, "sql": sql})
    assert response.status_code == status
    assert "artifact_write_failed" not in response.text


def test_http_artifact_failure_after_sql_reports_known_success_and_http500(tmp_path, monkeypatch):
    """执行已完成但私有结果写出失败时，只报告产物错误和已知执行事实。"""
    settings = registered_settings(tmp_path)
    original_write = _artifacts._write_json

    def fail_result(path, value, **kwargs):
        """仅阻断结果发布，不影响SQLite执行或安全失败记录。"""
        if path.name == "result.json":
            raise OSError("disk failed with PRIVATE_ERROR_BODY")
        return original_write(path, value, **kwargs)

    monkeypatch.setattr(_artifacts, "_write_json", fail_result)
    response = TestClient(create_app(settings)).post("/queries", json={"database_id": "demo", "sql": "SELECT id FROM items"})
    assert response.status_code == 500
    assert response.json()["detail"]["operation_outcome"] == "succeeded"
    assert response.json()["detail"]["error_type"] == "artifact_write_failed"
    assert "PRIVATE_ERROR_BODY" not in response.text
    runs = list(settings.artifacts_dir.glob("runs/query/*/*"))
    assert len(runs) == 1
    record = json.loads((runs[0] / "run.json").read_text())
    assert record["status"] == "failed" and record["operation_outcome"] == "succeeded"


def test_http_date_log_failure_preserves_read_error_but_fails_success(tmp_path, monkeypatch):
    """普通请求的日志故障不覆盖已有404，成功请求则明确报告记录失败。"""
    from querypilot.entrypoints import http

    settings = registered_settings(tmp_path)

    def fail_log(*args, **kwargs):
        """模拟日期日志不可写。"""
        raise OSError("date log unavailable")

    monkeypatch.setattr(http, "log_application", fail_log)
    client = TestClient(create_app(settings))
    assert client.get("/databases/missing/schema").status_code == 404
    assert client.get("/health").status_code == 500
    assert not list(settings.artifacts_dir.glob("runs/*/*/*"))


def test_overlapping_http_and_fixture_evaluation_keep_distinct_runs(tmp_path, monkeypatch):
    """HTTP与direct真实重叠执行时，各自只有一根，事件与批次不串写。"""
    from querypilot.databases import api as databases

    settings = registered_settings(tmp_path)
    client = TestClient(create_app(settings))
    cases = tmp_path / "concurrent-cases.json"
    cases.write_text(json.dumps([{"db_id": "demo", "question_id": 7,
                                 "question": "EVAL_QUESTION_PRIVATE", "SQL": "SELECT label FROM items"}]))
    barrier = Barrier(2)
    seen = local()
    actual_execute = databases._execute_readonly
    shared = logging.getLogger("querypilot.progress")
    handlers = shared.handlers[:]

    def synchronized_execute(*args, **kwargs):
        """每个操作第一次读库先会合，之后仍由真实SQLite执行器处理。"""
        if not getattr(seen, "first_read", False):
            seen.first_read = True
            barrier.wait(timeout=15)
        return actual_execute(*args, **kwargs)

    monkeypatch.setattr(databases, "_execute_readonly", synchronized_execute)
    with ThreadPoolExecutor(max_workers=2) as pool:
        request = pool.submit(client.post, "/queries", json={"database_id": "demo", "sql": "SELECT id FROM items"})
        evaluation = pool.submit(run_direct, cases, settings, provider="fixture")
        assert request.result(timeout=30).status_code == 200
        assert evaluation.result(timeout=30)["summary"]["success"] == 1
    runs = list(settings.artifacts_dir.glob("runs/*/*/*"))
    assert len(runs) == 2
    assert sorted(json.loads((run / "run.json").read_text())["operation"] for run in runs) == ["evaluation", "query"]
    for run in runs:
        events = [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()]
        assert {event["run_id"] for event in events} == {run.name}
        assert "EVAL_QUESTION_PRIVATE" not in (run / "events.jsonl").read_text()
    assert shared.handlers == handlers
