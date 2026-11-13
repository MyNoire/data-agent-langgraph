"""从不同业务公开入口检查运行归属、知识身份与并发隔离。"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from conftest import _curated, _write_json
from test_catalog_corrections import _inputs as correction_inputs
from test_task_runs import registered_settings

from querypilot.evaluation.api import run_direct
from querypilot.knowledge.api import (build_corpus, curate_catalog, load_corpus,
                                     render_knowledge, verify_corpus)
from querypilot.querying.api import ask
from querypilot.runtime.api import read_run_result, task_run
from querypilot.sql_generation.api import GeneratedSql


def corpus_inputs(tmp_path):
    """构造原创整理后目录和语义文件；不读正式知识、不产生隐式运行。"""
    root = tmp_path / "knowledge-inputs"
    root.mkdir()
    curated = _curated(root)
    semantic = root / "semantics.json"
    _write_json(semantic, {
        "database_id": "demo", "version": "mixed-fixture-v1", "author": "原创测试",
        "tables": {"items": {"purpose": "KNOWLEDGE_BODY_SENTINEL", "row_grain": "一件物品。"},
                   "events": {"purpose": "物品事件。", "row_grain": "一次事件。"}},
        "relationships": [{"source_table": "events", "source_columns": ["tenant_ref", "item_ref"],
                           "target_table": "items", "target_columns": ["tenant_id", "item_id"],
                           "meaning": "关联同租户物品。"}],
    })
    return curated, semantic


def test_mixed_public_operations_own_one_run_and_reused_corpus_is_untouched(tmp_path):
    """查询、fixture评测、两次语料操作各建一根；加载/复验/渲染不另建。"""
    settings = registered_settings(tmp_path)
    cases = tmp_path / "original-cases.json"
    cases.write_text(json.dumps([{"db_id": "demo", "question_id": 9,
                                 "question": "QUESTION_PRIVATE", "SQL": "SELECT label FROM items"}]))
    curated, semantic = corpus_inputs(tmp_path)
    target = tmp_path / "corpus"
    query = ask("demo", "QUESTION_PRIVATE", settings, generator=lambda *_: GeneratedSql("SELECT label FROM items"))

    def forbid_model(*args):
        """fixture应使用题集SQL，不得调用任何生成器或模型。"""
        raise AssertionError("fixture attempted model generation")

    report = run_direct(cases, settings, provider="fixture", generator=forbid_model)
    first = build_corpus(curated, semantic, target, "demo", settings=settings)
    original_files = {path.relative_to(target): path.read_bytes() for path in target.rglob("*") if path.is_file()}
    second = build_corpus(curated, semantic, target, "demo", settings=settings)
    assert query["result"]["rows"] == [["ROW_PRIVATE"]]
    assert report["summary"]["success"] == 1 and report["provider"] == "fixture"
    assert first.corpus_id == second.corpus_id and second.outcome == "reused"
    assert {path.relative_to(target): path.read_bytes() for path in target.rglob("*") if path.is_file()} == original_files
    assert verify_corpus(target).corpus_id == first.corpus_id
    assert load_corpus(target).manifest.corpus_id == first.corpus_id
    render_knowledge(curated, semantic)

    runs = list(settings.artifacts_dir.glob("runs/*/*/*"))
    assert len(runs) == 4
    operations = [json.loads((run / "run.json").read_text())["operation"] for run in runs]
    assert sorted(operations) == ["evaluation", "knowledge_corpus", "knowledge_corpus", "query"]
    assert all(read_run_result(run) is not None for run in runs)
    safe_paths = [run / name for run in runs for name in ("run.json", "config.json", "events.jsonl")]
    safe_paths += list((settings.artifacts_dir / "logs").rglob("*.jsonl"))
    safe_text = "".join(path.read_text(encoding="utf-8") for path in safe_paths)
    for private in ("QUESTION_PRIVATE", "ROW_PRIVATE", "SELECT label", "KNOWLEDGE_BODY_SENTINEL", "tenant_ref"):
        assert private not in safe_text
    assert not (target / "run.json").exists()


def test_curation_direct_owns_run_and_borrowed_operation_does_not_duplicate(tmp_path):
    """会写资料的修正API独立调用自管运行，上层传入时只借用同一份记录。"""
    settings = registered_settings(tmp_path)
    inputs_root = tmp_path / "curation-inputs"
    inputs_root.mkdir()
    base, source, plan = correction_inputs(inputs_root)
    standalone = curate_catalog(base, source, plan, tmp_path / "curated-one", settings=settings)
    with task_run("knowledge_catalog", settings, command="catalog-curation") as outer:
        nested = curate_catalog(base, source, plan, tmp_path / "curated-two", run_context=outer)
        assert not (outer.run_dir / "result.json").exists()
        outer.complete(nested)
    runs = list(settings.artifacts_dir.glob("runs/knowledge_catalog/*/*"))
    assert len(runs) == 2
    assert standalone == nested
    for run in runs:
        assert read_run_result(run)["changes"] == 3
        events = [json.loads(line)["event"] for line in (run / "events.jsonl").read_text().splitlines()]
        assert events.count("run_started") == 1 and events.count("run_finished") == 1


def test_overlapping_query_and_corpus_do_not_share_event_sink(tmp_path, monkeypatch):
    """两个不同公开操作真实重叠时，事件文件仍只属于自己的内部运行ID。"""
    from querypilot.knowledge.knowledge_corpus import api as corpus_api

    settings = registered_settings(tmp_path)
    curated, semantic = corpus_inputs(tmp_path)
    barrier = Barrier(2)
    original_build = corpus_api._build_knowledge_corpus
    shared = logging.getLogger("querypilot.progress")
    handlers = shared.handlers[:]

    def synchronized_build(*args, **kwargs):
        """屏障只控制并发时机，仍执行真实语料构建。"""
        barrier.wait(timeout=15)
        return original_build(*args, **kwargs)

    def generate(*args):
        """固定SQL替代模型，同时与构建操作建立真实重叠。"""
        barrier.wait(timeout=15)
        return GeneratedSql("SELECT 23 AS value")

    monkeypatch.setattr(corpus_api, "_build_knowledge_corpus", synchronized_build)
    with ThreadPoolExecutor(max_workers=2) as pool:
        query = pool.submit(ask, "demo", "CONCURRENT_QUESTION_PRIVATE", settings, generator=generate)
        corpus = pool.submit(build_corpus, curated, semantic, tmp_path / "corpus", "demo", settings=settings)
        assert query.result(timeout=30)["result"]["rows"] == [[23]]
        assert corpus.result(timeout=30).outcome == "built"
    runs = list(settings.artifacts_dir.glob("runs/*/*/*"))
    assert len(runs) == 2
    for run in runs:
        events = [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()]
        assert {event["run_id"] for event in events} == {run.name}
        assert "CONCURRENT_QUESTION_PRIVATE" not in (run / "events.jsonl").read_text()
    assert shared.handlers == handlers
