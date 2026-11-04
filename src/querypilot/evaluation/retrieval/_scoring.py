"""检索评测的计分规则（工单 03 结对实现）。

口径见 GLOSSARY「评测」一节：Table Ranking、Table Complete Recall@K、Table Recall@K、
Tables to Cover（K 以表计），以及只作诊断的 Column Recall@K（K 以 Hit 计）。
"""

from querypilot.retrieval.api import RetrievalHit

from .models import RetrievalCase

TABLE_KS = (2, 3, 5)
COLUMN_K = 20


def score_case(case: RetrievalCase, hits: list[RetrievalHit]) -> dict:
    """单题计分：返回要并入逐题结果的字段。hits 按 rank 排列；input_error 的题传入空列表。"""
    return {}


def summarize(records: list[dict]) -> dict:
    """一组逐题结果（整体或某个库）的指标汇总：返回要并入 summary 的字段。"""
    return {}
