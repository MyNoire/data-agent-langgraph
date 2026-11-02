"""以原创微型数据库验证候选、预检冻结与复制工具，不触碰正式BIRD资料。"""

from hashlib import sha256
import json
import sqlite3

import pytest

from querypilot.evaluation.api import select_final100, freeze_final100, prepare_final100
from querypilot.evaluation.datasets._selection import DIFFICULTIES, QUOTAS, select_cases
from querypilot.runtime.api import Settings, task_run


def _fixture(tmp_path):
    """创建足够原配额的虚构题目与11个微型SQLite，返回显式来源和清单。"""
    root = tmp_path / "source"
    root.mkdir()
    cases = []
    paths = {}
    for database_id, quotas in QUOTAS.items():
        directory = root / database_id
        directory.mkdir()
        path = directory / f"{database_id}.sqlite"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE items(id INTEGER)")
            connection.execute("INSERT INTO items VALUES (1)")
        paths[database_id] = path
        for difficulty, count in zip(DIFFICULTIES, quotas):
            for _ in range(count + 2):
                cases.append({"db_id": database_id, "question_id": len(cases) + 1,
                              "difficulty": difficulty, "question": "原创夹具计数",
                              "SQL": "SELECT COUNT(*) FROM items"})
    # 令稳定排序的第一候选失败，验证冻结仍按原规则跳过后补足配额。
    first = select_cases(cases, set())[0]
    first["SQL"] = "DELETE FROM items"
    source = tmp_path / "source.json"
    source.write_text(json.dumps(cases), encoding="utf-8")
    smoke = tmp_path / "smoke.json"
    smoke.write_text(json.dumps({"dataset": "original-test", "revision": "test-v1", "source_url": "local-fixture",
                                 "source_sha256": sha256(source.read_bytes()).hexdigest(),
                                 "database_sha256": {}, "question_ids": []}), encoding="utf-8")
    return root, source, smoke, first["question_id"], paths


def test_selection_freeze_and_prepare_preserve_fixed_rules_and_targets(tmp_path):
    """固定排序可复现；拒绝危险gold、补足100题、复制复用且拒绝污染目标。"""
    root, source, smoke, rejected_id, databases = _fixture(tmp_path)
    before = {key: path.read_bytes() for key, path in databases.items()}
    selected = select_final100(source, smoke)
    assert selected == select_final100(source, smoke)
    assert selected["count"] == 100 and selected["overlap"] == 0 and selected["database_count"] == 11
    settings = Settings(_env_file=None, data_dir=tmp_path / "target", artifacts_dir=tmp_path / "artifacts")
    manifest = tmp_path / "final.json"
    with task_run("data_prepare", settings, command="freeze-final100", private=True) as context:
        frozen = freeze_final100(root, manifest, source, smoke, run_context=context)
        context.complete(frozen)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(payload["question_ids"]) == len(set(payload["question_ids"])) == 100
    assert rejected_id not in payload["question_ids"]
    assert payload["skipped_preflight"][0]["question_id"] == rejected_id
    assert "SqlRejected" in payload["skipped_preflight"][0]["reason"]
    original_manifest = manifest.read_bytes()
    with pytest.raises(ValueError):
        freeze_final100(root, manifest, source, smoke)
    assert manifest.read_bytes() == original_manifest
    copied = prepare_final100(root, manifest, settings)
    reused = prepare_final100(root, manifest, settings)
    assert set(copied["databases"].values()) == {"copied"}
    assert set(reused["databases"].values()) == {"existing"}
    assert all(path.read_bytes() == before[key] for key, path in databases.items())
    target = settings.data_dir / "bird/dev_databases/california_schools/california_schools.sqlite"
    target.write_bytes(b"changed target")
    with pytest.raises(ValueError, match="existing database checksum mismatch"):
        prepare_final100(root, manifest, settings)
    assert target.read_bytes() == b"changed target"
