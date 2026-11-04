"""检索评测计分规则：经 evaluate_retrieval 和假检索器，只看报告里逐题和汇总的 scores。"""

from querypilot.evaluation.api import evaluate_retrieval
from test_retrieval_eval_runner import _case, _cases_file, _hit, _result, _settings


def _scores(tmp_path, hits, *, relevant_tables=("items",), relevant_columns=(("items", "label"),)) -> dict:
    """一道题、一份固定的 Hit 列表，返回这道题的 scores。"""
    case = _case(1, relevant_tables=relevant_tables, relevant_columns=relevant_columns)
    report = evaluate_retrieval(_cases_file(tmp_path, [case]), _settings(tmp_path),
                                retriever=lambda request: _result(request, hits))
    return report["records"][0]["scores"]


def test_table_ranking_counts_each_table_once_at_its_first_hit(tmp_path):
    hits = [
        _hit(1, "column", "items", "label"),   # 列 Chunk 把 items 带进排名，第 1 张
        _hit(2, "table", "items", None),       # items 再次出现，不再占名次
        _hit(3, "column", "users", "name"),    # 不相关的表也占名次，第 2 张
        _hit(4, "column", "events", "id"),     # events 第 3 张
    ]

    scores = _scores(tmp_path, hits, relevant_tables=("events", "items"))

    # 只报相关表的名次；users 不是相关表，不出现在这里，但它把 events 挤到了第 3。
    assert scores["table_ranks"] == {"events": 3, "items": 1}
