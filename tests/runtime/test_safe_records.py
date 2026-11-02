"""凭据与正文分级通过运行入口验证，不用字符串替换伪造安全日志。"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Barrier

from querypilot.runtime.api import JsonFormatter, Settings, log_application, sanitize_private, source_version, task_run


def test_safe_records_drop_body_but_private_result_keeps_business_fields(tmp_path):
    """正文只能进入私有结果/审计，凭据和秘密 URL 在那里也要清理。"""
    key, password, token = "api-sentinel-123", "pg-secret-456", "url-secret-789"
    question, sql, schema, rows, knowledge = ("question-sentinel", "SELECT salary FROM staff", "schema-sentinel", "rows-sentinel", "knowledge-sentinel")
    settings = Settings(_env_file=None, artifacts_dir=tmp_path, model_api_key=key,
                        model_base_url=f"https://me:{password}@example.test/v1?token={token}#private")
    with task_run("evaluation", settings, private=True, secrets=(password, token), config={
        "provider": "fixture", "question": question, "sql": sql, "schema": schema,
        "rows": rows, "knowledge": knowledge, "dsn": f"postgresql://me:{password}@db/private",
    }) as context:
        context.event("eval_case_started", question=question, sql=sql, schema=schema, rows=rows, knowledge=knowledge)
        context.logger.exception(f"not-controlled {question} {key}")
        context.audit_logger.info("sql_executed", extra={"sql": sql, "decision": "success"})
        context.complete({"question": question, "predicted_sql": sql, "schema": schema, "rows": rows,
                          "knowledge": knowledge, "model_base_url": settings.model_base_url,
                          "error": f"{key} postgresql://me:{password}@db/private https://u:p@example.test/v1?token={token}",
                          "nested": {"password": password, "dsn": f"host=db user=me password={password}"}})
    safe_paths = [context.run_dir / name for name in ("run.json", "config.json", "events.jsonl")]
    safe_paths += list((tmp_path / "logs").rglob("*.jsonl"))
    safe_text = "\n".join(p.read_text(encoding="utf-8") for p in safe_paths)
    for sentinel in (question, sql, schema, rows, knowledge, key, password, token):
        assert sentinel not in safe_text
    private = json.loads((context.run_dir / "private/result.json").read_text())
    assert private["question"] == question and private["predicted_sql"] == sql
    assert private["model_base_url"] == "https://example.test/v1"
    assert sql in (context.run_dir / "private/sql-audit.jsonl").read_text()
    all_text = "\n".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*") if p.is_file())
    for secret in (key, password, token):
        assert secret not in all_text
    assert context.audit_logger.propagate is False


def test_formatter_never_stringifies_arbitrary_message_or_exception():
    """未审查消息可以是不能格式化的对象，安全输出仍可用。"""
    class SensitiveMessage:
        """任何格式化都会暴露测试失败。"""
        def __str__(self):
            """禁止日志隐式转换正文。"""
            raise AssertionError("message was formatted")

    record = logging.LogRecord("arbitrary", logging.ERROR, "", 0, SensitiveMessage(), (), None)
    record.sql = "sensitive sql"
    record.error_type = "bad exception with private body"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["event"] == "unrecognized_event"
    assert payload["error_type"] == "Error"
    assert "sql" not in payload


def test_concurrent_contexts_keep_files_and_global_handlers_separate(tmp_path):
    """两个真实重叠运行的事件不串写，也不临时替换共享 logger。"""
    settings = Settings(_env_file=None, artifacts_dir=tmp_path)
    barrier = Barrier(2)
    shared = logging.getLogger("querypilot.progress")
    before = shared.handlers[:]

    def work(index):
        """屏障保证两个上下文同时活动，然后返回各自目录。"""
        with task_run("evaluation", settings) as context:
            barrier.wait(timeout=10)
            context.event("eval_case_started", graph_run_id=f"{context.run_id}:case:{index}")
            context.complete({"index": index})
        return context.run_dir

    with ThreadPoolExecutor(max_workers=2) as pool:
        directories = list(pool.map(work, [1, 2]))
    for directory in directories:
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        assert {event["run_id"] for event in events} == {directory.name}
    assert shared.handlers == before


def test_application_logging_only_uses_safe_fields_and_utc_date(tmp_path):
    """普通日志按日期追加，敏感正文及全局 handler 均不参与。"""
    settings = Settings(_env_file=None, artifacts_dir=tmp_path)
    path = log_application("http_request", settings=settings, request_id="external/request/id", question="private body")
    payload = json.loads(path.read_text())
    assert path.name == "app.jsonl" and len(path.parent.name) == 10
    assert payload["request_id"] == "external/request/id"
    assert "private body" not in path.read_text()


def test_version_without_git_is_unavailable(tmp_path):
    """安装环境找不到 .git 时，不伪造源码版本或干净状态。"""
    result = source_version(tmp_path)
    assert result["version_source"] == "unavailable"
    assert result["git_revision"] is None and result["worktree_dirty"] is None


def test_private_cleaner_keeps_sql_and_removes_password_from_explicit_dsn():
    """清理凭据时保留合法 user 列谓词；完整 DSN 也保护其密码的单独回显。"""
    dsn = "postgresql://alice:p%40ssword@db:5432/private"
    cleaned = sanitize_private({"sql": "SELECT user FROM staff WHERE user='alice'", "error": "p@ssword unavailable"}, secrets=(dsn,))
    assert cleaned["sql"] == "SELECT user FROM staff WHERE user='alice'"
    assert "p@ssword" not in cleaned["error"]


def test_private_business_url_keeps_query_and_fragment_but_model_address_is_public(tmp_path):
    """普通 URL 是业务数据；不能借脱敏改写 SQL 字面量、结果或评测正文。"""
    address = "https://reports.example.test/export?month=10#totals"
    sql = f"SELECT '{address}' AS report_url"
    result = {"sql": sql, "rows": [[address]], "question": f"查看 {address}",
              "model_base_url": "https://models.example.test/v1?region=one#route"}
    with task_run("query", Settings(_env_file=None, artifacts_dir=tmp_path), private=True) as context:
        context.complete(result)
    written = json.loads((context.run_dir / "private/result.json").read_text(encoding="utf-8"))
    assert written["sql"] == sql and written["rows"] == [[address]]
    assert written["question"] == result["question"]
    assert written["model_base_url"] == "https://models.example.test/v1"


def test_attached_cli_stream_is_safe_and_not_closed(tmp_path):
    """CLI终端仅收安全事件；运行关闭不关闭调用方stderr流。"""
    terminal = StringIO()
    settings = Settings(_env_file=None, artifacts_dir=tmp_path, model_api_key="CLI_KEY_SECRET")
    with task_run("query", settings, private=True) as context:
        context.attach_stream(terminal)
        context.attach_stream(terminal)
        context.event("activity_started", question="CLI_QUESTION_PRIVATE", sql="SELECT CLI_SQL_PRIVATE")
        context.complete({"question": "CLI_QUESTION_PRIVATE"})
    assert not terminal.closed
    events = [json.loads(line) for line in terminal.getvalue().splitlines()]
    assert [event["event"] for event in events] == ["activity_started", "run_finished"]
    assert "CLI_QUESTION_PRIVATE" not in terminal.getvalue()
    assert "CLI_SQL_PRIVATE" not in terminal.getvalue()
