"""语义配置里的数据约定：表 Chunk 在记录粒度后多一行，metadata 记来源 observed。"""

import json
from pathlib import Path

from conftest import _write_json
from test_knowledge_corpus import _inputs, _rows
from querypilot.knowledge.knowledge_corpus._corpus import build_knowledge_corpus


def _table_chunk(corpus: Path, table: str) -> dict:
    return next(row for row in _rows(corpus, "chunks")
                if row["knowledge_type"] == "table" and row["table_name"] == table)


def test_a_table_with_a_data_convention_gets_a_line_after_the_row_grain(tmp_path):
    curated, semantic = _inputs(tmp_path)
    profile = json.loads(semantic.read_bytes())
    profile["tables"]["events"]["data_convention"] = {
        "text": "每次事件正反两个方向各存一行。", "origin": "observed"}
    _write_json(semantic, profile)

    build_knowledge_corpus(curated, semantic, tmp_path / "corpus", "demo")

    events = _table_chunk(tmp_path / "corpus", "events")
    lines = events["content"].split("\n")
    assert lines[lines.index("记录粒度: 一次事件。") + 1] == "数据约定: 每次事件正反两个方向各存一行。"
    assert events["metadata"]["data_convention"] == {"origin": "observed"}
    items = _table_chunk(tmp_path / "corpus", "items")
    assert "数据约定" not in items["content"]
    assert "data_convention" not in items["metadata"]
