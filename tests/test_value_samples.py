"""取值样例：只读打开业务 SQLite，按结构逐列取出真实取值（规则 value-samples-v1）。"""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from querypilot.knowledge.api import ValueSamples, collect_value_samples


def _database(tmp_path: Path, ddl: str, rows: list[tuple]) -> Path:
    """建一个只有一张表的测试库：ddl 是建表语句，rows 是要插入的行。"""
    path = tmp_path / "club.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(ddl)
        placeholders = ", ".join("?" * len(rows[0]))
        table = ddl.split()[2]
        connection.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
    return path


def _column(samples, table: str, column: str):
    return next(item for item in samples.columns if (item.table_name, item.column_name) == (table, column))


def test_a_text_column_with_few_values_lists_all_of_them_sorted_and_verbatim(tmp_path):
    """event.type 只有 3 个取值（含重复和空值）：全部列出，按值排序，保留大小写，不含 NULL。"""
    path = _database(tmp_path, "CREATE TABLE event (type TEXT)",
                     [("Meeting",), ("Game",), ("Meeting",), (None,), ("Guest Speaker",)])

    samples = collect_value_samples("club", path, private_columns=set())

    column = _column(samples, "event", "type")
    assert column.mode == "all"
    assert column.distinct_count == 3
    assert [item.value for item in column.values] == ["Game", "Guest Speaker", "Meeting"]


def test_other_columns_give_the_three_most_frequent_values_ties_broken_by_value(tmp_path):
    """cost 是 REAL：只给出现最多的 3 个（6.0 两次；其余各一次，按值排序取 5.5、7.0）。"""
    path = _database(tmp_path, "CREATE TABLE expense (cost REAL)",
                     [(6.0,), (9.0,), (6.0,), (7.0,), (5.5,), (None,)])

    column = _column(collect_value_samples("club", path, private_columns=set()), "expense", "cost")

    assert column.mode == "samples"
    assert column.distinct_count == 4
    assert [item.value for item in column.values] == [6.0, 5.5, 7.0]


def test_a_text_column_with_more_than_twenty_values_gives_samples(tmp_path):
    """21 个不同的文本值超过上限，只给样例。"""
    path = _database(tmp_path, "CREATE TABLE zip_code (city TEXT)", [(f"City {n:02d}",) for n in range(21)])

    column = _column(collect_value_samples("club", path, private_columns=set()), "zip_code", "city")

    assert (column.mode, column.distinct_count, len(column.values)) == ("samples", 21, 3)


def test_key_columns_give_samples_even_with_few_values(tmp_path):
    """主键和外键源列即使只有 2 个值也只给样例，不全部列出。"""
    path = tmp_path / "club.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE event (event_id TEXT PRIMARY KEY)")
        connection.execute("CREATE TABLE budget (link_to_event TEXT REFERENCES event (event_id))")
        connection.executemany("INSERT INTO event VALUES (?)", [("rec1",), ("rec2",)])
        connection.executemany("INSERT INTO budget VALUES (?)", [("rec1",), ("rec2",)])

    samples = collect_value_samples("club", path, private_columns=set())

    assert _column(samples, "event", "event_id").mode == "samples"
    assert _column(samples, "budget", "link_to_event").mode == "samples"


def test_long_text_values_are_cut_at_eighty_characters_and_marked(tmp_path):
    long_note = "Secured donations to help pay for speaker gifts. " * 3
    path = _database(tmp_path, "CREATE TABLE income (notes TEXT)", [(long_note,), ("Short",)])

    values = _column(collect_value_samples("club", path, private_columns=set()), "income", "notes").values

    assert [(item.value, item.truncated) for item in values] == [
        (long_note[:80], True), ("Short", False)]


def test_private_columns_get_no_values_and_no_value_leaves_the_database(tmp_path):
    """隐私列只记 mode=private；结果的任何地方都不出现它的值。"""
    path = _database(tmp_path, "CREATE TABLE member (email TEXT, position TEXT)",
                     [("angela@example.edu", "President"), ("dean@example.edu", "Member")])

    samples = collect_value_samples("club", path, private_columns={("member", "email")})

    email = _column(samples, "member", "email")
    assert (email.mode, email.distinct_count, email.values) == ("private", None, [])
    assert "example.edu" not in samples.model_dump_json()
    assert _column(samples, "member", "position").mode == "all"


def test_an_unknown_private_column_is_refused(tmp_path):
    """隐私名单写错列名时直接报错，不能悄悄把真正的隐私列漏掉。"""
    path = _database(tmp_path, "CREATE TABLE member (email TEXT)", [("a@example.edu",)])

    with pytest.raises(ValueError):
        collect_value_samples("club", path, private_columns={("member", "e_mail")})


def test_the_result_records_the_source_file_hash_and_the_rule_version(tmp_path):
    path = _database(tmp_path, "CREATE TABLE event (type TEXT)", [("Game",)])

    samples = collect_value_samples("club", path, private_columns=set())

    assert samples.source_sqlite_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert (samples.database_id, samples.rule_version) == ("club", "value-samples-v1")


def test_cli_values_command_reads_the_private_list_from_the_semantic_file(tmp_path, monkeypatch, capsys):
    """knowledge catalog values：按登记的库取值，隐私名单取自语义配置；标准输出只有计数，没有取值。"""
    from querypilot.databases.api import DatabaseRegistry
    from querypilot.entrypoints.cli import main
    from querypilot.runtime.api import Settings

    path = _database(tmp_path, "CREATE TABLE member (email TEXT, position TEXT)",
                     [("angela@example.edu", "President"), ("dean@example.edu", "Member")])
    DatabaseRegistry(tmp_path).register("club", path)
    settings = Settings(_env_file=None, data_dir=tmp_path)
    monkeypatch.setattr("querypilot.runtime.api.get_settings", lambda: settings)
    monkeypatch.setattr("querypilot.entrypoints.cli.get_settings", lambda: settings)
    semantic = tmp_path / "club-semantics.json"
    semantic.write_text(json.dumps({
        "database_id": "club", "version": "club-v2", "author": "测试", "tables": {},
        "private_columns": [{"table_name": "member", "column_name": "email"}]}), encoding="utf-8")
    output = tmp_path / "values" / "club-values.json"

    assert main(["--artifacts-dir", str(tmp_path / "artifacts"), "knowledge", "catalog", "values",
                 "--database-id", "club", "--semantic-file", str(semantic), "--output", str(output)]) == 0

    samples = ValueSamples.model_validate_json(output.read_text(encoding="utf-8"))
    assert _column(samples, "member", "email").mode == "private"
    assert "example.edu" not in output.read_text(encoding="utf-8")
    summary = json.loads(capsys.readouterr().out)
    assert summary["modes"] == {"all": 1, "private": 1}
    assert "President" not in json.dumps(summary)
