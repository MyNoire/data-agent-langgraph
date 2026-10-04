"""结构目录的必要行为验收，使用原创 SQLite fixture。"""

from contextlib import closing
import hashlib
from pathlib import Path
import runpy
import sqlite3
import sys

import pytest

from querypilot.catalog.models import CatalogSnapshot
from querypilot.catalog.sqlite import CatalogError, extract_catalog
from querypilot.config import Settings
from querypilot.database.registry import DatabaseRegistry


@pytest.fixture
def catalog_database(tmp_path: Path) -> Path:
    """创建只含结构的临时数据库。

    参数:
        tmp_path: pytest 提供的本测试独立目录。
    返回:
        含特殊标识符、生成列与复合关系的 SQLite 文件路径。
    副作用:
        仅在测试临时目录建表；不读取或写入真实 BIRD 数据。
    """
    path = tmp_path / "fixture.sqlite"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript('''
            CREATE TABLE "parent table" (
                "key one" TEXT, "key""two" INTEGER,
                PRIMARY KEY ("key""two", "key one")
            ) WITHOUT ROWID;
            CREATE TABLE "child table" (
                x INTEGER, y TEXT,
                doubled INTEGER GENERATED ALWAYS AS (x * 2) STORED,
                FOREIGN KEY (x, y) REFERENCES "PARENT TABLE" ON DELETE CASCADE,
                FOREIGN KEY (x, y) REFERENCES "parent table"("key""two", "key one")
            );
        ''')
    return path


def test_catalog_preserves_structure_and_source(catalog_database: Path) -> None:
    """验证关系语义、重复结果与源文件不变。

    参数:
        catalog_database: 原创数据库 fixture。
    返回:
        None；结构错配或源文件改变时断言失败。
    """
    before = hashlib.sha256(catalog_database.read_bytes()).hexdigest()
    snapshot = extract_catalog("fixture", catalog_database)
    tables = {table.name: table for table in snapshot.tables}
    parent = tables["parent table"]
    child = tables["child table"]
    assert parent.without_rowid is True
    assert parent.primary_key_columns() == ['key"two', "key one"]
    assert next(column for column in child.columns if column.name == "doubled").hidden == 3
    assert len(child.foreign_keys) == 2
    assert {relation.on_delete for relation in child.foreign_keys} == {"CASCADE", "NO ACTION"}
    implicit = next(relation for relation in child.foreign_keys if relation.declared_target_columns == [None, None])
    assert implicit.target_table == "parent table"
    assert implicit.source_columns == ["x", "y"]
    assert implicit.target_columns == ['key"two', "key one"]
    assert all(relation.target_columns == ['key"two', "key one"] for relation in child.foreign_keys)
    assert extract_catalog("fixture", catalog_database).model_dump() == snapshot.model_dump()
    assert CatalogSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
    assert hashlib.sha256(catalog_database.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("ddl", [
    'CREATE TABLE child(x REFERENCES missing);',
    'CREATE TABLE parent(a, b, PRIMARY KEY(a, b)); CREATE TABLE child(x REFERENCES parent);',
])
def test_catalog_rejects_unresolvable_relation(tmp_path: Path, ddl: str) -> None:
    """验证错误结构不能产生看似可用的连接关系。

    参数:
        tmp_path: pytest 临时目录。
        ddl: 目标缺失或字段数量不一致的原创结构定义。
    返回:
        None；没有抛出 CatalogError 则测试失败。
    """
    path = tmp_path / "invalid.sqlite"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(ddl)
    with pytest.raises(CatalogError):
        extract_catalog("invalid", path)


def test_cli_exports_and_refuses_overwrite(
    catalog_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 CLI 使用登记路径、可重复导出并拒绝覆盖。

    参数:
        catalog_database: 原创数据库 fixture。
        tmp_path: 输出与注册表所在的临时目录。
        monkeypatch: pytest 的临时配置和命令行替换器。
    返回:
        None；输出内容或拒绝覆盖行为不符合预期则失败。
    副作用:
        仅向临时目录写注册表和两个 JSON 文件。
    """
    DatabaseRegistry(tmp_path).register("fixture", catalog_database)
    settings = Settings(_env_file=None, data_dir=tmp_path)
    monkeypatch.setattr("querypilot.config.get_settings", lambda: settings)
    script = Path(__file__).resolve().parents[1] / "scripts" / "export_catalog.py"
    outputs = [tmp_path / "first.json", tmp_path / "second.json"]
    for output in outputs:
        monkeypatch.setattr(sys, "argv", [str(script), "--database-id", "fixture", "--output", str(output)])
        runpy.run_path(str(script), run_name="__main__")
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    assert CatalogSnapshot.model_validate_json(outputs[0].read_text(encoding="utf-8")).database_id == "fixture"
    original = outputs[1].read_bytes()
    with pytest.raises(FileExistsError):
        runpy.run_path(str(script), run_name="__main__")
    assert outputs[1].read_bytes() == original
