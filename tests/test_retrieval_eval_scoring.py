"""检索评测计分规则：经 evaluate_retrieval 和假检索器，只看报告里逐题和汇总的 scores。"""

import json

import pytest

from querypilot.evaluation.api import evaluate_retrieval
from test_retrieval_eval_runner import _case, _cases_file, _hit, _result, _settings


def _scores(tmp_path, hits, *, relevant_tables=("items",), relevant_columns=(("items", "label"),)) -> dict:
    """一道题、一份固定的 Hit 列表，返回这道题的 scores。"""
    case = _case(1, relevant_tables=relevant_tables, relevant_columns=relevant_columns)
    report = evaluate_retrieval(_cases_file(tmp_path, [case]), _settings(tmp_path),
                                retriever=lambda request: _result(request, hits))
    return report["records"][0]["scores"]


def _relationship_hit(rank: int, source_table: str, index: int = 0):
    """关系类 Hit：三个定位字段为空；chunk_key 是 JSON 数组 [类型, 库, schema, 源表, 序号]，和 Corpus 一致。"""
    chunk_key = json.dumps(["relationship", "demo", "main", source_table, index])
    return _hit(rank, "relationship", None, None).model_copy(update={"schema_name": None, "chunk_key": chunk_key})


def test_relevant_table_rank_is_its_first_hit_rank(tmp_path):
    hits = [
        _hit(1, "column", "items", "label"),   # 列 Chunk 也算 items 命中
        _hit(2, "table", "items", None),       # items 再次出现，名次仍取第 1 个 Hit
        _hit(3, "column", "users", "name"),    # 不相关的表也占 Hit 名额
        _hit(4, "column", "events", "id"),
    ]

    scores = _scores(tmp_path, hits, relevant_tables=("events", "items"))

    # 名次以 Hit 计，和 top_k 同一单位；users 不是相关表，不报，但它占了第 3 个 Hit。
    assert scores["table_ranks"] == {"events": 4, "items": 1}


def test_relevant_table_never_retrieved_has_no_rank(tmp_path):
    hits = [_hit(1, "column", "items", "label")]

    scores = _scores(tmp_path, hits, relevant_tables=("events", "items"))

    # 每张相关表都要报出来；没被检索到的名次为空，这样报告里能直接看到漏了哪张表。
    assert scores["table_ranks"] == {"events": None, "items": 1}


def test_relationship_hit_ranks_its_source_table_from_chunk_key(tmp_path):
    hits = [
        _relationship_hit(1, "events"),        # 关系 Chunk 没有 table_name，源表 events 从 chunk_key 取
        _hit(2, "column", "items", "label"),
    ]

    scores = _scores(tmp_path, hits, relevant_tables=("events", "items"))

    assert scores["table_ranks"] == {"events": 1, "items": 2}


def test_table_recall_at_k_cuts_at_the_kth_hit(tmp_path):
    hits = [
        _hit(1, "column", "items", "label"),
        *(_hit(rank, "column", "users", f"c{rank}") for rank in range(2, 7)),   # 无关表的 5 个列 Chunk
        _hit(7, "column", "events", "id"),
    ]

    scores = _scores(tmp_path, hits, relevant_tables=("events", "items"))

    # 前 5 个 Hit 只有 items：不完整，召回一半；前 10 个 Hit 两张相关表都在。
    assert {key: value for key, value in scores.items() if "recall@" in key} == {
        "table_complete_recall@5": False, "table_recall@5": 0.5,
        "table_complete_recall@10": True, "table_recall@10": 1.0,
    }


@pytest.mark.parametrize(("hits", "expected"), [
    # 全部命中：取各相关表首次命中名次的最大值，也就是这道题至少要 top_k=7
    ([_hit(1, "column", "items", "label"), _hit(2, "column", "users", "name"), _hit(7, "column", "events", "id")], 7),
    # events 始终没出现：多大的 top_k（在检索预算内）都盖不全，为空
    ([_hit(1, "column", "items", "label"), _hit(2, "column", "users", "name")], None),
])
def test_hits_to_cover_is_the_smallest_top_k_that_covers_all_relevant_tables(tmp_path, hits, expected):
    scores = _scores(tmp_path, hits, relevant_tables=("events", "items"))

    assert scores["hits_to_cover"] == expected
