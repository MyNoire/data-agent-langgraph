"""从业务任务注入窄文件故障，检查真实操作结果和主错误优先。"""

import json

import pytest

from querypilot.databases.api import SqlRejected, execute_sql
from querypilot.runtime.api import RunArtifactError
from querypilot.runtime import _artifacts
from test_task_runs import registered_settings


def test_completed_sql_but_result_publish_failed_keeps_known_outcome(tmp_path, monkeypatch):
    """SQL 已执行后结果落盘失败，不能声称业务尚未执行。"""
    settings = registered_settings(tmp_path)
    original = _artifacts._write_json

    def write(path, value, **kwargs):
        """仅阻断本次临时目录中的结果发布，其他状态仍可写。"""
        if path.name == "result.json":
            raise OSError("simulated disk failure")
        return original(path, value, **kwargs)

    monkeypatch.setattr(_artifacts, "_write_json", write)
    with pytest.raises(RunArtifactError) as error:
        execute_sql("demo", "SELECT id FROM items", settings)
    assert error.value.operation_outcome == "succeeded"
    run = next(settings.artifacts_dir.glob("runs/query/*/*"))
    state = json.loads((run / "run.json").read_text(encoding="utf-8"))
    assert state["operation_outcome"] == "succeeded" and state["status"] == "failed"


def test_guard_error_is_not_replaced_by_failure_record_error(tmp_path, monkeypatch):
    """危险 SQL 的原拒绝异常优先，失败记录写不出也不换成产物错误。"""
    settings = registered_settings(tmp_path)
    original = _artifacts._write_json

    def write(path, value, **kwargs):
        """只在更新失败状态时注入故障，保留启动阶段可写。"""
        if path.name == "run.json" and value.get("status") == "failed":
            raise OSError("failure state cannot be saved")
        return original(path, value, **kwargs)

    monkeypatch.setattr(_artifacts, "_write_json", write)
    with pytest.raises(SqlRejected):
        execute_sql("demo", "DELETE FROM items", settings)
