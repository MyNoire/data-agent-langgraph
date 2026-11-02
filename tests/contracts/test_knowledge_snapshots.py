"""公开快照加载始终读取实际文件，操作记录不成为知识身份。"""

import pytest

from querypilot.knowledge.api import CorpusError, build_snapshot, load_snapshot, verify_snapshot


def test_public_snapshot_load_rechecks_disk_and_keeps_held_object(r2_corpus):
    """输入：原创快照；返回：持有对象不改而新加载拒绝；仅污染测试目录。"""
    held = load_snapshot(r2_corpus)
    assert verify_snapshot(r2_corpus).corpus_id == held.manifest.corpus_id
    original = held.chunks[0].content
    (r2_corpus / "chunks.jsonl").write_text("{}\n", encoding="utf-8")
    assert held.chunks[0].content == original
    with pytest.raises(CorpusError):
        load_snapshot(r2_corpus)


def test_public_build_reuses_identical_snapshot_with_independent_runs(r2_corpus):
    """输入：同一批准资料与既有快照；返回：两次复用、身份和文件不改；运行各自独立。"""
    from pathlib import Path
    root = r2_corpus.parent
    before = {p.relative_to(r2_corpus): p.read_bytes() for p in r2_corpus.rglob("*") if p.is_file()}
    for _ in range(2):
        result = build_snapshot(root / "curated", root / "config/semantics.json", r2_corpus, "demo")
        assert result.outcome == "reused"
    assert {p.relative_to(r2_corpus): p.read_bytes() for p in r2_corpus.rglob("*") if p.is_file()} == before
    runs = list((root / "artifacts/runs/knowledge_snapshot").rglob("run.json"))
    assert len(runs) == 2
