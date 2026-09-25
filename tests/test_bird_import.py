"""用微型本地夹具验证 BIRD 导入脚本，不依赖真实大型数据包。"""

import json
import hashlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_bird_import_script_with_tiny_local_fixture(tmp_path):
    """用微型题集验证导入脚本登记数据库并生成 smoke 文件。

    参数:
        tmp_path: pytest 提供的隔离临时目录。
    返回:
        None；通过断言报告测试结果。
    """
    root = tmp_path / "bird"
    database_folder = root / "dev_databases" / "tiny"
    database_folder.mkdir(parents=True)
    with sqlite3.connect(database_folder / "tiny.sqlite") as db:
        db.execute("CREATE TABLE t (id INTEGER)")
    (root / "mini_dev_sqlite.json").write_text(
        json.dumps([{"db_id": "tiny", "question_id": 1, "question": "Count rows", "SQL": "SELECT COUNT(*) FROM t"}]),
        encoding="utf-8",
    )
    output = tmp_path / "smoke.json"
    project = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["QUERYPILOT_DATA_DIR"] = str(tmp_path)
    completed = subprocess.run(
        [sys.executable, str(project / "scripts" / "import_bird.py"), "--root", str(root), "--limit", "1", "--output", str(output)],
        cwd=project, env=env, capture_output=True, text=True, check=True,
    )
    assert "imported 1 cases" in completed.stdout
    assert json.loads(output.read_text(encoding="utf-8"))[0]["db_id"] == "tiny"


def test_fixed_manifest_prepares_questions_and_rejects_source_changes(tmp_path):
    """检查固定清单顺序、数据库哈希及题目来源哈希。

    参数:
        tmp_path: pytest 提供的隔离临时目录。
    返回:
        None；通过断言报告测试结果。
    """
    root = tmp_path / "bird"
    root.mkdir()
    source = root / "mini_dev_sqlite.json"
    source.write_text(json.dumps([
        {"db_id": "tiny", "question_id": 1, "question": "First", "SQL": "SELECT 1"},
        {"db_id": "tiny", "question_id": 2, "question": "Second", "SQL": "SELECT 2"},
    ]), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                                    "question_ids": [2, 1]}), encoding="utf-8")
    output = tmp_path / "smoke.json"
    project = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["QUERYPILOT_DATA_DIR"] = str(tmp_path)
    command = [sys.executable, str(project / "scripts" / "import_bird.py"), "--root", str(root),
               "--manifest", str(manifest), "--questions-only", "--output", str(output)]
    completed = subprocess.run(command, cwd=project, env=env, capture_output=True, text=True, check=True)
    assert "prepared 2 cases" in completed.stdout
    assert [case["question_id"] for case in json.loads(output.read_text(encoding="utf-8"))] == [2, 1]

    with sqlite3.connect(root / "tiny.sqlite") as db:
        db.execute("CREATE TABLE t (id INTEGER)")
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["database_sha256"] = {"tiny": "0" * 64}
    manifest.write_text(json.dumps(manifest_data), encoding="utf-8")
    full_command = [arg for arg in command if arg != "--questions-only"]
    bad_database = subprocess.run(full_command, cwd=project, env=env, capture_output=True, text=True)
    assert bad_database.returncode != 0
    assert "database checksum mismatch" in bad_database.stderr
    assert not (tmp_path / "registry.json").exists()

    manifest_data["database_sha256"]["tiny"] = hashlib.sha256((root / "tiny.sqlite").read_bytes()).hexdigest()
    manifest.write_text(json.dumps(manifest_data), encoding="utf-8")
    imported = subprocess.run(full_command, cwd=project, env=env, capture_output=True, text=True, check=True)
    assert "imported 2 cases" in imported.stdout

    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    rejected = subprocess.run(command, cwd=project, env=env, capture_output=True, text=True)
    assert rejected.returncode != 0
    assert "checksum mismatch" in rejected.stderr
