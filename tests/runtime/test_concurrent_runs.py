"""真实重叠的公开任务不切换共享 handler、不串运行文件。"""

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import threading

from querypilot.querying.api import ask
from querypilot.runtime.api import read_run_result
from querypilot.sql_generation.api import GeneratedSql
from test_task_runs import registered_settings


def test_overlapping_public_queries_are_isolated(tmp_path):
    """同步两个生成器保证真实重叠；每份结果与自身 run 一致。"""
    settings = registered_settings(tmp_path)
    barrier = threading.Barrier(2)
    shared = logging.getLogger("querypilot.progress")
    handlers = list(shared.handlers)

    def execute(value):
        """从公开 ask 运行不同固定结果，屏障仅用于测试重叠。"""
        def generate(*_):
            """两任务同时进入生成阶段，返回不同的固定只读 SQL。"""
            barrier.wait(timeout=10)
            return GeneratedSql(f"SELECT {value} AS value")
        return ask("demo", f"private-{value}", settings, generator=generate)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, [11, 22]))
    assert [result["result"]["rows"] for result in results] == [[[11]], [[22]]]
    runs = list(settings.artifacts_dir.glob("runs/query/*/*"))
    assert len(runs) == 2
    assert sorted(read_run_result(run)["result"]["rows"][0][0] for run in runs) == [11, 22]
    for run in runs:
        events = [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        assert {event["run_id"] for event in events} == {run.name}
    assert shared.handlers == handlers

def test_overlapping_direct_storage_and_http_keep_own_run_files(tmp_path, r2_corpus, monkeypatch):
    """真实重叠四类入口；PG边界用替身，仅证明日志接线隔离，事务另由实库验证。"""
    from fastapi.testclient import TestClient
    from querypilot.entrypoints import http
    from querypilot.evaluation.api import run_direct
    from querypilot.knowledge.api import (
        ImportSummary, ImportVerificationSummary, KnowledgeStorageSettings,
        import_corpus, load_corpus, verify_import,
    )
    from querypilot.knowledge.knowledge_store import api as store_api
    from types import ModuleType
    import sys

    settings = registered_settings(tmp_path)
    source = tmp_path / "overlap-cases.json"
    source.write_text(json.dumps([{"db_id": "demo", "question": "PRIVATE_DIRECT_QUESTION",
                                  "SQL": "SELECT 31 AS value"}]), encoding="utf-8")
    barrier = threading.Barrier(4)
    shared = logging.getLogger("querypilot.progress")
    handlers = shared.handlers[:]
    held = load_corpus(r2_corpus)

    def receipt(corpus, *, run_id, outcome):
        """按真实源身份构造安全回执，不伪装成真实PG操作证据。"""
        values = dict(corpus_id=corpus.manifest.corpus_id, database_id="demo",
                      schema_version=corpus.manifest.identity_descriptor.schema_versions["demo"],
                      core_digest=corpus.manifest.core_digest, counts=corpus.manifest.counts,
                      projection_digest="1" * 64, timestamps_digest="2" * 64,
                      import_run_id=run_id, elapsed_ms=0)
        if outcome == "verified":
            return ImportVerificationSummary(**values)
        return ImportSummary(result_version="knowledge-import-result-v1", outcome=outcome, **values)

    def imported(corpus, storage_settings, *, run_id, logger):
        """同步模拟已提交边界；正文不进入事件，不建立新运行。"""
        barrier.wait(timeout=15)
        logger.info("corpus_import_finished", extra={"run_id": run_id, "status": "imported"})
        return receipt(corpus, run_id=run_id, outcome="imported")

    def verified(corpus, storage_settings, *, run_id, logger):
        """同步模拟复验边界；实际PG全字段校验由integration测试承担。"""
        barrier.wait(timeout=15)
        logger.info("corpus_verify_finished", extra={"run_id": run_id, "status": "verified"})
        return receipt(corpus, run_id=run_id, outcome="verified")

    def generate(*_):
        """替代模型，与三个不同操作同时进入主要执行阶段。"""
        barrier.wait(timeout=15)
        return GeneratedSql("SELECT 31 AS value")

    original_execute = http.execute_sql

    def http_execute(*args, **kwargs):
        """仅同步HTTP入口，随后仍走真实只读SQLite。"""
        barrier.wait(timeout=15)
        return original_execute(*args, **kwargs)

    # 只替换PG操作边界，普通运行测试不要求安装驱动或连接服务。
    fake_importer = ModuleType("querypilot.knowledge.knowledge_store._importer")
    fake_importer.import_verified_corpus = imported
    fake_importer.verify_corpus_import = verified
    monkeypatch.setitem(sys.modules, fake_importer.__name__, fake_importer)
    monkeypatch.setattr(store_api, "_driver", lambda: None)
    monkeypatch.setattr(http, "execute_sql", http_execute)
    storage = KnowledgeStorageSettings(_env_file=None)
    client = TestClient(http.create_app(settings))
    with ThreadPoolExecutor(max_workers=4) as pool:
        evaluation = pool.submit(run_direct, source, settings, generator=generate)
        imported_run = pool.submit(import_corpus, r2_corpus, storage)
        verified_run = pool.submit(verify_import, r2_corpus, storage)
        response = pool.submit(client.post, "/queries",
                               json={"database_id": "demo", "sql": "SELECT 41 AS value"})
        assert evaluation.result(timeout=30)["summary"]["success"] == 1
        assert imported_run.result(timeout=30).outcome == "imported"
        assert verified_run.result(timeout=30).outcome == "verified"
        assert response.result(timeout=30).json()["rows"] == [[41]]
    runs = list(settings.artifacts_dir.glob("runs/*/*/*"))
    assert len(runs) == 4
    assert sorted(json.loads((run / "run.json").read_text(encoding="utf-8"))["operation"]
                  for run in runs) == ["evaluation", "knowledge_import", "knowledge_verify", "query"]
    for run in runs:
        events_text = (run / "events.jsonl").read_text(encoding="utf-8")
        events = [json.loads(line) for line in events_text.splitlines()]
        assert {event["run_id"] for event in events} == {run.name}
        assert json.loads((run / "run.json").read_text(encoding="utf-8"))["status"] == "completed"
        assert "PRIVATE_DIRECT_QUESTION" not in events_text and "SELECT 31" not in events_text
    assert held.manifest.corpus_id == load_corpus(r2_corpus).manifest.corpus_id
    assert shared.handlers == handlers
