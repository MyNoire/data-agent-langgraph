"""字段说明绑定的必要验收；输入均为原创小型 fixture。"""

from contextlib import closing
import csv
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from querypilot.knowledge.knowledge_catalog.api import DescriptionError, enrich_catalog
from querypilot.knowledge.knowledge_catalog.api import CatalogSnapshot
from querypilot.knowledge.knowledge_catalog.api import extract_catalog


HEADERS = [
    "original_column_name", "column_name", "column_description",
    "data_format", "value_description",
]
ROWS = [
    ["id", "", "identifier", "text", ""],
    ["status", "状态", "登记状态", "text", "A=登记"],
    ["amount", "", "金额", "integer", ""],
]


def _write_csv(
    path: Path, rows: list[list[str]], *,
    headers: list[str] | None = None, encoding: str = "utf-8",
) -> None:
    """创建或改写测试自己的说明文件。

    参数:
        path: fixture 内的文件路径。
        rows: 测试用数据记录。
        headers: 可选表头；None 时使用五个标准表头。
        encoding: 测试用编码，默认为 UTF-8。
    返回:
        None；仅向 fixture 路径写 CSV。
    """
    with path.open("w", encoding=encoding, newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADERS if headers is None else headers)
        writer.writerows(rows)


@pytest.fixture
def description_inputs(tmp_path: Path) -> tuple[CatalogSnapshot, Path]:
    """创建一个真实结构快照与空的说明目录。

    参数:
        tmp_path: pytest 提供的独立临时目录。
    返回:
        demo 库的 CatalogSnapshot，以及其 database_description 目录。
    副作用:
        只在临时目录创建三个字段的空表，不读取 BIRD。
    """
    root = tmp_path / "demo" / "database_description"
    root.mkdir(parents=True)
    database = root.parent / "demo.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, status TEXT, amount INTEGER)")
        connection.commit()
    return extract_catalog("demo", database), root


def test_multiline_provenance_and_structure_are_preserved(description_inputs) -> None:
    """验证多行来源、BOM、名称绑定和原结构保持。

    参数:
        description_inputs: 原创目录和来源路径 fixture。
    返回:
        None；来源行号、内容或结构变化不符合约定时失败。
    """
    catalog, root = description_inputs
    original = catalog.model_dump_json()
    path = root / "Items.csv"
    value_text = "A=登记\nB=取消\u0085\nC=完成"
    rows = [ROWS[0], [" STATUS ", "状态", "登记状态", "text", value_text], ["amount", "", "", "integer", ""]]
    _write_csv(path, rows, encoding="utf-8-sig")
    result = enrich_catalog(catalog, root)
    notes = {item.column_name: item for item in result.enriched_catalog.descriptions}
    status = notes["status"]
    assert status.table_name == "items"
    assert status.value_description == value_text
    assert status.source.line_start == 3 and status.source.line_end == 5
    assert status.source.record_index == 2
    assert status.source.raw_values[0] == " STATUS "
    assert status.source.raw_record == ' STATUS ,状态,登记状态,text,"' + value_text + '"\n'
    source = result.enriched_catalog.sources[0]
    assert source.encoding == "utf-8-sig"
    assert source.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert notes["amount"].description is None
    assert {warning.code for warning in result.warnings} == {"name_normalized", "suspicious_text", "empty_meaning"}
    assert catalog.model_dump_json() == original
    assert result.enriched_catalog.catalog.tables[0].columns[0].declared_type == "INTEGER"
    assert notes["id"].source_data_format == "text"
    assert result.enriched_catalog.status == "draft"
    assert all(note.review_status == "source_extracted" for note in notes.values())


def test_invalid_rows_are_quarantined_and_coverage_is_visible(description_inputs) -> None:
    """验证未知字段和错误宽度不能污染真实结构。

    参数:
        description_inputs: 原创目录和来源路径 fixture。
    返回:
        None；无效记录被绑定、丢失原文或缺失覆盖不可见时失败。
    """
    catalog, root = description_inputs
    unknown = ["invented", "", "不存在的字段", "text", "A=B"]
    too_wide = ["status", "", "说明", "text", "", "额外内容"]
    empty_wide = [""] * 6
    empty_named = [""] * 5
    _write_csv(root / "items.csv", [ROWS[0], unknown, too_wide, empty_wide, empty_named])
    result = enrich_catalog(catalog, root)
    assert [item.column_name for item in result.enriched_catalog.descriptions] == ["id"]
    unbound = {warning.column_name for warning in result.warnings if warning.code == "unbound_column"}
    assert unbound == {"status", "amount"}
    assert next(w.source for w in result.warnings if w.code == "unknown_column").raw_values == unknown
    assert next(w.source for w in result.warnings if w.code == "row_width").raw_values == too_wide
    assert any(w.code == "row_width" and w.source.raw_values == empty_wide for w in result.warnings)
    assert any(w.code == "unknown_column" and w.source.raw_values == empty_named for w in result.warnings)
    assert len(result.enriched_catalog.catalog.tables[0].columns) == 3


@pytest.mark.parametrize("problem", ["duplicate_header", "duplicate_binding"])
def test_duplicate_information_is_rejected(description_inputs, problem: str) -> None:
    """验证重复信息不被字典或后续记录静默覆盖。

    参数:
        description_inputs: 原创目录和来源路径 fixture。
        problem: 重复表头或重复字段说明的测试分支。
    返回:
        None；未抛出 DescriptionError 则失败。
    """
    catalog, root = description_inputs
    if problem == "duplicate_header":
        _write_csv(root / "items.csv", ROWS, headers=HEADERS[:-1] + ["column_description"])
    else:
        _write_csv(root / "items.csv", ROWS + [ROWS[0]])
    with pytest.raises(DescriptionError):
        enrich_catalog(catalog, root)


@pytest.mark.parametrize("problem", ["unknown_table", "duplicate_table_file"])
def test_invalid_table_files_are_rejected(description_inputs, problem: str) -> None:
    """验证来源文件只能绑定到一张真实表，且每张表只有一个来源文件。

    参数:
        description_inputs: 原创目录和来源路径 fixture。
        problem: 未知表文件或同一张表对应两个文件的测试分支。
    返回:
        None；未抛出明确的 DescriptionError 则失败。
    """
    catalog, root = description_inputs
    if problem == "unknown_table":
        _write_csv(root / "unknown.csv", ROWS)
    else:
        # 两份文件使用不同字段，避免被字段重复检查意外拦住。
        _write_csv(root / "items.csv", [ROWS[0]])
        _write_csv(root / " items.csv", ROWS[1:])
    with pytest.raises(DescriptionError, match="未知表或重复"):
        enrich_catalog(catalog, root)


def test_cli_and_strict_decoding(description_inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证真实 CLI 输出、拒绝覆盖以及错误编码明确失败。

    参数:
        description_inputs: 原创目录和来源路径 fixture。
        tmp_path: 本测试的目录快照与输出位置。
        monkeypatch: 临时替换命令行参数的 pytest 工具。
    返回:
        None；输出契约、覆盖保护或解码规则不符合要求时失败。
    副作用:
        只在临时目录写输入和输出；不读取模型配置。
    """
    catalog, root = description_inputs
    source_path = root / "items.csv"
    _write_csv(source_path, ROWS)
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(catalog.model_dump_json(), encoding="utf-8")
    output_dir = tmp_path / "result"
    from querypilot.entrypoints.cli import main
    args = ["--artifacts-dir", str(tmp_path / "artifacts"), "knowledge", "catalog", "enrich",
            "--catalog", str(catalog_path), "--description-dir", str(root), "--output-dir", str(output_dir)]
    assert main(args) == 0
    output = output_dir / "enriched_catalog.json"
    before = output.read_bytes()
    assert len(json.loads(before)["descriptions"]) == 3
    assert json.loads((output_dir / "import_warnings.json").read_text(encoding="utf-8"))["warnings"] == []
    assert main(args) == 1
    assert output.read_bytes() == before
    source_path.write_bytes(b"\xff\xfe invalid UTF-8")
    with pytest.raises(DescriptionError, match="编码"):
        enrich_catalog(catalog, root)
