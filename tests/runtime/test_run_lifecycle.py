"""运行状态和文件保护测试；只使用隔离目录及公开运行入口。"""

import json
from pathlib import Path
from uuid import UUID

import pytest

from querypilot.runtime.api import RunArtifactError, Settings, publish_json, read_run_result, task_run
from querypilot.runtime import _artifacts


def settings_for(tmp_path):
    """返回完全隔离的配置，不读取项目 .env。"""
    return Settings(_env_file=None, artifacts_dir=tmp_path / "artifacts", data_dir=tmp_path / "data")


def test_publish_and_read_complete_result(tmp_path):
    """领域结果完整发布后才可读，路径只使用内部 UUID 和固定类别。"""
    settings = settings_for(tmp_path)
    with task_run("evaluation", settings, command="eval direct", private=True,
                  config={"provider": "fixture"}) as context:
        directory = context.run_dir
        assert UUID(context.run_id).hex == context.run_id
        assert json.loads((directory / "run.json").read_text())["status"] == "started"
        with pytest.raises(RunArtifactError, match="incomplete_run"):
            read_run_result(directory)
        result = {"status": "no_predictions", "records": [], "summary": {"score": None}}
        context.complete(result)
    assert read_run_result(directory) == result
    record = json.loads((directory / "run.json").read_text())
    assert record["result_path"] == "private/result.json"
    assert record["operation_outcome"] == "succeeded"
    assert not (directory / "result.json").exists()
    assert not list(directory.rglob("*.tmp"))


def test_borrowing_context_does_not_close_or_publish(tmp_path):
    """嵌套操作沿用同一目录，关闭权和唯一结果属于外层。"""
    settings = settings_for(tmp_path)
    with task_run("evaluation", settings) as outer:
        with task_run("query", settings, run_context=outer) as inner:
            assert inner is outer
            inner.event("eval_case_started", graph_run_id=f"{outer.run_id}:case:1")
        outer.event("eval_case_finished", case_index=1)
        outer.complete({"done": True})
    assert len(list(settings.artifacts_dir.glob("runs/*/*/*"))) == 1
    assert read_run_result(outer.run_dir) == {"done": True}


def test_existing_result_is_never_overwritten(tmp_path):
    """已经有目标结果时排他发布失败，原有字节及已知业务成功事实保留。"""
    with pytest.raises(RunArtifactError) as caught:
        with task_run("knowledge_import", settings_for(tmp_path)) as context:
            target = context.run_dir / "result.json"
            target.write_text("old contents", encoding="utf-8")
            context.complete({"outcome": "imported"}, outcome="imported")
    assert target.read_text() == "old contents"
    assert caught.value.operation_outcome == "imported"
    assert json.loads((context.run_dir / "run.json").read_text())["status"] == "failed"
    events = [json.loads(line) for line in (context.run_dir / "events.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "run_failed"
    assert events[-1]["error_type"] == "RunArtifactError"
    assert not any(event["event"] == "run_finished" for event in events)


def test_directory_collision_does_not_modify_previous_run(tmp_path, monkeypatch):
    """内部 ID 即使碰撞也不得写旧目录的 run/config/events。"""
    monkeypatch.setattr(_artifacts, "uuid4", lambda: UUID("a" * 32))
    settings = settings_for(tmp_path)
    with task_run("query", settings) as first:
        first.complete({"answer": "old"})
    before = {p.relative_to(first.run_dir): p.read_bytes() for p in first.run_dir.rglob("*") if p.is_file()}
    with pytest.raises(RunArtifactError) as caught:
        with task_run("query", settings):
            pytest.fail("collision must fail before operation")
    assert caught.value.operation_outcome == "not_started"
    after = {p.relative_to(first.run_dir): p.read_bytes() for p in first.run_dir.rglob("*") if p.is_file()}
    assert before == after


def test_no_hardlink_support_does_not_fall_back_to_replace(tmp_path, monkeypatch):
    """不支持排他发布的文件系统明确报错，不使用覆盖式替代。"""
    settings = settings_for(tmp_path)
    with pytest.raises(RunArtifactError) as caught:
        with task_run("query", settings) as context:
            def deny_link(*args, **kwargs):
                """模拟结果发布失败，started/config 已写好。"""
                raise OSError("filesystem has no hard links")
            monkeypatch.setattr(_artifacts.os, "link", deny_link)
            context.complete({"answer": 1})
    assert caught.value.operation_outcome == "succeeded"
    assert not (context.run_dir / "result.json").exists()


def test_result_survives_terminal_record_failure_but_is_not_readable(tmp_path, monkeypatch):
    """结果已发布但终态无法写出时，不删结果，也不接受为完整运行。"""
    settings = settings_for(tmp_path)
    original = _artifacts._write_json
    with pytest.raises(RunArtifactError) as caught:
        with task_run("knowledge_import", settings) as context:
            def fail_final_record(path, value, **kwargs):
                """只阻断当前目录终态更新。"""
                if path.name == "run.json" and kwargs.get("replace_owned"):
                    raise OSError("terminal state write failed")
                return original(path, value, **kwargs)
            monkeypatch.setattr(_artifacts, "_write_json", fail_final_record)
            context.complete({"outcome": "reused"}, outcome="reused")
    assert caught.value.operation_outcome == "reused"
    assert (context.run_dir / "result.json").is_file()
    with pytest.raises(RunArtifactError, match="incomplete_run"):
        read_run_result(context.run_dir)


def test_business_error_wins_over_failure_record_write(tmp_path, monkeypatch):
    """失败记录也无法写出时，最初的业务错误仍为调用方收到的异常。"""
    problem = ValueError("domain failure with private content")
    with pytest.raises(ValueError) as caught:
        with task_run("query", settings_for(tmp_path)) as context:
            monkeypatch.setattr(_artifacts, "_write_json", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk failed")))
            raise problem
    assert caught.value is problem
    assert json.loads((context.run_dir / "run.json").read_text())["status"] == "started"


def test_commit_unknown_is_not_reported_as_rolled_back(tmp_path):
    """业务声明提交结果未知时，运行记录不推测数据库成功或回滚。"""
    class CommitUnknown(RuntimeError):
        """模拟现有存储异常的安全类别。"""
        error_type = "commit_outcome_unknown"

    with pytest.raises(CommitUnknown):
        with task_run("knowledge_import", settings_for(tmp_path)) as context:
            raise CommitUnknown("connection lost with secrets")
    record = json.loads((context.run_dir / "run.json").read_text())
    assert record["operation_outcome"] == "unknown"
    assert record["error_type"] == "commit_outcome_unknown"
    assert record["status"] == "failed"
    assert "connection lost" not in json.dumps(record)


def test_omitting_complete_is_incomplete_not_success(tmp_path):
    """只退出上下文不能冒充领域结果已完整发布。"""
    with pytest.raises(RunArtifactError, match="incomplete_run"):
        with task_run("query", settings_for(tmp_path)) as context:
            context.event("activity_started")
    with pytest.raises(RunArtifactError, match="incomplete_run"):
        read_run_result(context.run_dir)


@pytest.mark.parametrize("pointer", ["../outside.json", "/outside.json", "private/../result.json", "other.json"])
def test_result_pointer_must_be_fixed_relative_path(tmp_path, pointer):
    """损坏或越界指针不得被读取端跟随。"""
    with task_run("query", settings_for(tmp_path)) as context:
        context.complete({"done": True})
    record_path = context.run_dir / "run.json"
    record = json.loads(record_path.read_text())
    record["result_path"] = pointer
    record_path.write_text(json.dumps(record))
    with pytest.raises(RunArtifactError, match="invalid_run"):
        read_run_result(context.run_dir)


def test_truncated_json_result_is_rejected(tmp_path):
    """即使终态为 completed，也不能接受不完整 JSON 结果。"""
    with task_run("query", settings_for(tmp_path)) as context:
        context.complete({"done": True})
    (context.run_dir / "result.json").write_text('{"done":')
    with pytest.raises(RunArtifactError, match="invalid_run"):
        read_run_result(context.run_dir)


def test_public_json_publisher_preserves_old_result(tmp_path):
    """显式领域输出同样完整排他发布，不包装 FileExistsError。"""
    path = tmp_path / "new" / "report.json"
    publish_json(path, {"old": 1})
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        publish_json(path, {"new": 2})
    assert path.read_bytes() == original
    assert not list(path.parent.glob("*.tmp"))


def test_event_write_failure_is_reported_after_known_operation_result(tmp_path, monkeypatch):
    """事件流写失败不能静默成功，也不能丢掉已知导入事实。"""
    with pytest.raises(RunArtifactError) as caught:
        with task_run("knowledge_import", settings_for(tmp_path)) as context:
            handler = context.logger.handlers[0]
            def broken_write(value):
                """仅对本次临时事件流模拟磁盘故障。"""
                raise OSError("private error must not be printed")
            monkeypatch.setattr(handler.stream, "write", broken_write)
            context.event("objects_written")
            context.complete({"outcome": "imported"}, outcome="imported")
    assert caught.value.operation_outcome == "imported"
    assert json.loads((context.run_dir / "run.json").read_text())["artifact_outcome"] == "failed"


def test_business_error_wins_over_event_and_cleanup_failures(tmp_path, monkeypatch):
    """原业务异常与日志失败并发时，仅报告原业务类别。"""
    problem = LookupError("original domain error")
    with pytest.raises(LookupError) as caught:
        with task_run("query", settings_for(tmp_path)) as context:
            def broken_flush():
                """只模拟自有 handler 刷新失败。"""
                raise OSError("cleanup failed")
            monkeypatch.setattr(context.logger.handlers[0], "flush", broken_flush)
            raise problem
    assert caught.value is problem


def test_failed_operation_can_preserve_known_database_outcome(tmp_path):
    """业务异常已经声明提交事实时，不改写为失败或回滚。"""
    class ReceiptFailure(ValueError):
        """模拟现有知识库异常字段。"""
        database_outcome = "imported"
        error_type = "receipt_write_failed"

    with pytest.raises(ReceiptFailure):
        with task_run("knowledge_import", settings_for(tmp_path)) as context:
            raise ReceiptFailure("receipt unavailable")
    record = json.loads((context.run_dir / "run.json").read_text())
    assert record["operation_outcome"] == "imported"
    assert record["error_type"] == "receipt_write_failed"
