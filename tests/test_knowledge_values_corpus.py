"""构建 Corpus 时读入取值样例文件：列 Chunk 多一行取值，取值文件进入 Corpus 身份。"""

import json
from pathlib import Path

import pytest

from conftest import _write_json
from test_knowledge_corpus import _inputs, _rows
from querypilot.knowledge.knowledge_corpus.api import CorpusError, verify_corpus
from querypilot.knowledge.knowledge_corpus._corpus import build_knowledge_corpus

HASH = "0" * 64


def _values(tmp_path: Path, **changes) -> Path:
    """demo 库的取值样例文件：items.label 全部取值，其余列给样例；changes 覆盖某一列的内容。"""
    columns = {
        ("items", "item_id"): {"mode": "samples", "distinct_count": 4,
                               "values": [{"value": "i-01"}, {"value": "i-02"}, {"value": "i-03"}]},
        ("items", "label"): {"mode": "all", "distinct_count": 2,
                             "values": [{"value": "Bob's"}, {"value": "x" * 80, "truncated": True}]},
        ("items", "tenant_id"): {"mode": "samples", "distinct_count": 1, "values": [{"value": "t-1"}]},
        ("events", "item_ref"): {"mode": "samples", "distinct_count": 0, "values": []},
        ("events", "tenant_ref"): {"mode": "samples", "distinct_count": 1, "values": [{"value": 7}]},
    }
    for (table, column), value in changes.items():
        columns[(table, column)] = value
    path = tmp_path / "values" / "demo-values.json"
    path.parent.mkdir(exist_ok=True)
    _write_json(path, {"format_version": "value-samples-v1", "rule_version": "value-samples-v1",
                       "database_id": "demo", "schema_name": "main", "source_sqlite_sha256": HASH,
                       "columns": [{"table_name": table, "column_name": column, **value}
                                   for (table, column), value in columns.items()]})
    return path


def _column_chunk(corpus: Path, table: str, column: str) -> dict:
    return next(row for row in _rows(corpus, "chunks")
                if row["knowledge_type"] == "column" and (row["table_name"], row["column_name"]) == (table, column))


def test_column_chunks_get_a_values_line_and_the_values_file_joins_the_corpus_identity(tmp_path):
    curated, semantic = _inputs(tmp_path)
    plain = build_knowledge_corpus(curated, semantic, tmp_path / "plain", "demo")

    result = build_knowledge_corpus(curated, semantic, tmp_path / "valued", "demo", values_file=_values(tmp_path))

    corpus = tmp_path / "valued"
    assert result.corpus_id != plain.corpus_id
    assert verify_corpus(corpus).corpus_id == result.corpus_id
    # 取值一行接在取值解释（可能有续行）之后，是正文的最后一行。
    assert _column_chunk(corpus, "items", "label")["content"].split("\n")[-1] == (
        "取值: 全部 2 个：'Bob''s', '" + "x" * 80 + "'…")
    assert _column_chunk(corpus, "items", "item_id")["content"].endswith(
        "取值: 样例（出现最多的 3 个，共 4 个不同值）：'i-01', 'i-02', 'i-03'")
    assert _column_chunk(corpus, "events", "tenant_ref")["content"].endswith(
        "取值: 样例（出现最多的 1 个，共 1 个不同值）：7")
    assert _column_chunk(corpus, "events", "item_ref")["content"].endswith("取值: 没有非空值")
    assert (corpus / "inputs/values/demo-values.json").read_bytes() == _values(tmp_path).read_bytes()


def _with_private(semantic: Path, *columns: tuple[str, str]) -> None:
    """在语义配置里写上隐私列名单。"""
    profile = json.loads(semantic.read_bytes())
    profile["private_columns"] = [{"table_name": table, "column_name": column} for table, column in columns]
    _write_json(semantic, profile)


def test_a_private_column_gets_no_values_line(tmp_path):
    curated, semantic = _inputs(tmp_path)
    _with_private(semantic, ("items", "label"))
    values = _values(tmp_path)
    data = json.loads(values.read_bytes())
    data["columns"][1] = {"table_name": "items", "column_name": "label", "mode": "private"}
    _write_json(values, data)

    build_knowledge_corpus(curated, semantic, tmp_path / "corpus", "demo", values_file=values)

    label = _column_chunk(tmp_path / "corpus", "items", "label")
    assert "取值:" not in label["content"]
    assert label["metadata"]["value_samples"]["mode"] == "private"


def test_a_values_file_with_values_for_a_private_column_is_refused(tmp_path):
    """语义配置说 items.label 是隐私列，取值文件却给了它的值：拒绝构建，不写出任何东西。"""
    curated, semantic = _inputs(tmp_path)
    _with_private(semantic, ("items", "label"))

    with pytest.raises(CorpusError):
        build_knowledge_corpus(curated, semantic, tmp_path / "corpus", "demo", values_file=_values(tmp_path))
    assert not (tmp_path / "corpus").exists()


def test_a_values_file_that_does_not_match_the_structure_is_refused(tmp_path):
    """取值文件少了一列（来自别的结构版本）：拒绝构建。"""
    curated, semantic = _inputs(tmp_path)
    values = _values(tmp_path)
    data = json.loads(values.read_bytes())
    data["columns"].pop()
    _write_json(values, data)

    with pytest.raises(CorpusError):
        build_knowledge_corpus(curated, semantic, tmp_path / "corpus", "demo", values_file=values)


def test_without_a_values_file_column_chunks_are_unchanged(tmp_path):
    """不给取值文件：没有取值一行，也没有取值的 metadata。"""
    curated, semantic = _inputs(tmp_path)

    build_knowledge_corpus(curated, semantic, tmp_path / "corpus", "demo")

    for row in _rows(tmp_path / "corpus", "chunks"):
        assert "取值:" not in row["content"]
        assert "value_samples" not in row["metadata"]


def test_cli_build_passes_the_values_file_through(tmp_path, capsys):
    from querypilot.entrypoints.cli import main
    curated, semantic = _inputs(tmp_path)
    out = tmp_path / "corpus"

    assert main(["--artifacts-dir", str(tmp_path / "artifacts"), "knowledge", "corpus", "build",
                 "--database-id", "demo", "--curated-dir", str(curated), "--semantic-file", str(semantic),
                 "--values-file", str(_values(tmp_path)), "--output-dir", str(out)]) == 0

    assert json.loads(capsys.readouterr().out)["outcome"] == "built"
    assert (out / "inputs/values/demo-values.json").is_file()
