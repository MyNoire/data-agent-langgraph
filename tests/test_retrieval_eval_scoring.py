"""检索评测计分规则：经 evaluate_retrieval 和假检索器，只看报告里逐题和汇总的 scores。"""

import json

import pytest

from querypilot.evaluation.api import evaluate_retrieval
from querypilot.retrieval.api import KnowledgeRetrievalError
from test_retrieval_eval_runner import _case, _cases_file, _hit, _result, _settings


def _scores(tmp_path, hits, *, relevant_tables=("items",), relevant_columns=(("items", "label"),)) -> dict:
    """一道题、一份固定的 Hit 列表，返回这道题的 scores。"""
    case = _case(1, relevant_tables=relevant_tables, relevant_columns=relevant_columns)
    report = evaluate_retrieval(_cases_file(tmp_path, [case]), _settings(tmp_path),
                                retriever=lambda request: _result(request, hits))
    return report["records"][0]["scores"]


def _summary(tmp_path, cases: list, hits_by_question: dict) -> dict:
    """按问题原文给每道题一份固定的 Hit 列表（值为 None 表示这道题 input_error），返回报告的 summary。"""
    def retriever(request):
        if hits_by_question[request.query] is None:
            raise KnowledgeRetrievalError("input_error")
        return _result(request, hits_by_question[request.query])

    return evaluate_retrieval(_cases_file(tmp_path, cases), _settings(tmp_path), retriever=retriever)["summary"]


def _summary_scores(tmp_path, hits_by_question: dict, **relevant) -> dict:
    """同一个 Relevant Set 的一组题，返回整体汇总的 scores。"""
    cases = [_case(number, question=question, **relevant) for number, question in enumerate(hits_by_question, 1)]
    return _summary(tmp_path, cases, hits_by_question)["overall"]["scores"]


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
    assert {key: value for key, value in scores.items() if key.startswith("table_") and "recall@" in key} == {
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


def test_relevant_column_rank_is_the_first_hit_of_its_column_chunk(tmp_path):
    hits = [
        _hit(1, "table", "items", None),       # 表 Chunk 不算任何列命中
        _hit(2, "column", "events", "label"),  # 同名列，但属于别的表
        _hit(3, "column", "items", "label"),   # items.label 第一次命中
        _relationship_hit(4, "items"),         # 关系 Chunk 也不算列命中
    ]

    scores = _scores(tmp_path, hits, relevant_columns=(("items", "label"), ("items", "qty")))

    # 键是"表.列"；没命中的列为空。
    assert scores["column_ranks"] == {"items.label": 3, "items.qty": None}


def test_same_column_name_in_two_relevant_tables_is_kept_apart(tmp_path):
    hits = [
        _hit(1, "column", "events", "label"),  # events 也是相关表，但 events.label 不是相关列
        _hit(2, "column", "items", "label"),
        _hit(3, "column", "events", "id"),
    ]

    scores = _scores(tmp_path, hits, relevant_tables=("events", "items"),
                     relevant_columns=(("items", "label"), ("events", "id")))

    assert scores["column_ranks"] == {"items.label": 2, "events.id": 3}


@pytest.mark.parametrize(("relevant_columns", "expected"), [
    # items.label 在第 3 个 Hit，items.qty 在第 12 个：前 10 个 Hit 里只拿到一半
    ((("items", "label"), ("items", "qty")), 0.5),
    # 没有相关列（例如只有 COUNT(*) 的题）：没有可召回的列，为空，不是 0
    ((), None),
])
def test_column_recall_at_10_counts_columns_hit_within_the_first_10_hits(tmp_path, relevant_columns, expected):
    hits = [
        _hit(1, "table", "items", None),
        _hit(3, "column", "items", "label"),
        _hit(12, "column", "items", "qty"),
    ]

    scores = _scores(tmp_path, hits, relevant_columns=relevant_columns)

    assert scores["column_recall@10"] == expected


def test_summary_averages_table_recall_with_input_error_cases_in_the_denominator(tmp_path):
    hits_by_question = {
        "q1": [_hit(1, "column", "items", "label"), _hit(3, "column", "events", "id")],   # @5 完整
        "q2": [_hit(1, "column", "items", "label"), _hit(7, "column", "events", "id")],   # @5 一半，@10 完整
        "q3": None,                                                                     # input_error：按 0 计
    }

    scores = _summary_scores(tmp_path, hits_by_question, relevant_tables=("events", "items"))

    assert {key: value for key, value in scores.items() if key.startswith("table_")} == pytest.approx({
        "table_complete_recall@5": 1 / 3, "table_recall@5": (1.0 + 0.5 + 0.0) / 3,
        "table_complete_recall@10": 2 / 3, "table_recall@10": (1.0 + 1.0 + 0.0) / 3,
    })


def test_summary_reports_hits_to_cover_over_covered_cases_and_counts_the_rest(tmp_path):
    hits_by_question = {
        "q1": [_hit(1, "column", "items", "label"), _hit(3, "column", "events", "id")],    # 3
        "q2": [_hit(1, "column", "items", "label"), _hit(7, "column", "events", "id")],    # 7
        "q3": [_hit(2, "column", "events", "id"), _hit(12, "column", "items", "label")],   # 12
        "q4": [_hit(1, "column", "items", "label")],                                     # events 没出现：为空
        "q5": None,                                                                      # input_error：也为空
    }

    scores = _summary_scores(tmp_path, hits_by_question, relevant_tables=("events", "items"))

    # 中位数和平均数只算能全覆盖的 3 道题；盖不全的 2 道单独报数，不能当成 0 或 20 混进去。
    assert {key: value for key, value in scores.items() if "cover" in key} == pytest.approx({
        "hits_to_cover_median": 7, "hits_to_cover_mean": (3 + 7 + 12) / 3, "uncovered_cases": 2,
    })



def test_summary_hits_to_cover_is_empty_when_no_case_is_covered(tmp_path):
    scores = _summary_scores(tmp_path, {"q1": [_hit(1, "column", "items", "label")], "q2": None},
                             relevant_tables=("events", "items"))

    # 没有一道题能全覆盖：没有可算的数，中位数和平均数为空，而不是报错或 0。
    assert {key: value for key, value in scores.items() if "cover" in key} == {
        "hits_to_cover_median": None, "hits_to_cover_mean": None, "uncovered_cases": 2,
    }


def test_summary_column_recall_skips_cases_without_relevant_columns(tmp_path):
    label, qty = ("items", "label"), ("items", "qty")
    cases = [
        _case(1, question="q1", relevant_columns=(label,)),                      # 1.0
        _case(2, question="q2", relevant_columns=(label, qty)),                  # 0.5
        _case(3, question="q3", relevant_columns=()),                            # 没有相关列：不参与平均
        _case(4, question="q4", relevant_columns=(label,)),                      # input_error：有相关列，按 0 计
        _case(5, question="q5", relevant_columns=(), db_id="other"),             # other 库只有这一道无列的题
    ]
    hits = [_hit(1, "column", "items", "label")]

    summary = _summary(tmp_path, cases, {"q1": hits, "q2": hits, "q3": hits, "q4": None, "q5": hits})

    column = {group: {key: value for key, value in scores.items() if "column" in key}
              for group, scores in [("overall", summary["overall"]["scores"]),
                                    *((db, s["scores"]) for db, s in summary["by_database"].items())]}
    assert column == {
        "overall": {"column_recall@10": 0.5, "no_relevant_column_cases": 2},   # (1.0 + 0.5 + 0.0) / 3
        "demo": {"column_recall@10": 0.5, "no_relevant_column_cases": 1},
        "other": {"column_recall@10": None, "no_relevant_column_cases": 1},
    }
