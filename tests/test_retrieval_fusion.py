"""RRF 融合：纯函数，不需要 PG。分数为 Σ 1/(60 + rank)，rank 从 1 开始。"""

import pytest

from querypilot.retrieval import _fusion


def _row(name: str, score: float = 0.0) -> dict:
    """一行检索结果的最小形状：身份字段 + 原始分数 + 一个随行字段。"""
    return {"chunk_id": f"id-{name}", "chunk_key": name, "title": f"标题 {name}", "score": score}


def test_chunk_in_both_rankings_sums_its_reciprocal_ranks():
    """BM25 [a, b]，向量 [b, c]：b = 1/61 + 1/62 排第一，a = 1/61，c = 1/62。"""
    fused = _fusion.reciprocal_rank_fusion([[_row("a", 9.0), _row("b", 5.0)], [_row("b", 0.8), _row("c", 0.7)]])
    assert [row["chunk_key"] for row in fused] == ["b", "a", "c"]
    assert [row["score"] for row in fused] == pytest.approx([0.0325224, 0.0163934, 0.0161290], abs=1e-7)


def test_equal_scores_are_ordered_by_chunk_key():
    """BM25 [z]，向量 [m]：两个都是 1/61，按 chunk_key 排成 m、z，与哪一路在前无关。"""
    for rankings in ([[_row("z")], [_row("m")]], [[_row("m")], [_row("z")]]):
        assert [row["chunk_key"] for row in _fusion.reciprocal_rank_fusion(rankings)] == ["m", "z"]


def test_one_empty_ranking_keeps_the_other_order_with_rrf_scores():
    """BM25 无命中 []，向量 [c, a, b]：名次不变，分数为 1/61、1/62、1/63，其他字段原样；两路都空时为 []。"""
    vector = [_row("c", 0.9), _row("a", 0.5), _row("b", 0.1)]
    fused = _fusion.reciprocal_rank_fusion([[], vector])
    assert [(row["chunk_key"], row["title"]) for row in fused] == [("c", "标题 c"), ("a", "标题 a"), ("b", "标题 b")]
    assert [row["score"] for row in fused] == pytest.approx([0.0163934, 0.0161290, 0.0158730], abs=1e-7)
    assert vector[0]["score"] == 0.9
    assert _fusion.reciprocal_rank_fusion([[], []]) == []
