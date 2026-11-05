"""检索评测的计分规则。

口径见 GLOSSARY「评测」一节：首次命中名次、Table Complete Recall@K、Table Recall@K、
Hits to Cover，以及只作诊断的 Column Recall@K。K 都以 Hit 计，和检索的 top_k 同一单位。
"""

import json

from querypilot.retrieval.api import RetrievalHit

from .models import RelevantColumn

TABLE_KS = (5, 10)
# Column Recall 在前 COLUMN_K 个 Hit 里找列；不能超过运行器的检索预算 TOP_K。
COLUMN_K = 10


def _hit_table(hit: RetrievalHit) -> str:
    """Hit 所属的表。关系类的定位字段为空，取 chunk_key（[类型, 库, schema, 源表, 序号]）里的源表。"""
    if hit.knowledge_type == "relationship":
        return json.loads(hit.chunk_key)[3]
    return hit.table_name


def score_case(relevant_tables: list[str], relevant_columns: list[RelevantColumn],
               hits: list[RetrievalHit]) -> dict:
    """单题计分，返回逐题结果中 scores 字段的内容。

    hits 按 rank 排列；input_error 的题传入空列表。
    """
    first_ranks: dict[str, int] = {}
    for hit in hits:
        first_ranks.setdefault(_hit_table(hit), hit.rank)
    table_ranks = {table: first_ranks.get(table) for table in relevant_tables}
    scores: dict = {"table_ranks": table_ranks}
    for k in TABLE_KS:
        found = sum(rank is not None and rank <= k for rank in table_ranks.values())
        scores[f"table_complete_recall@{k}"] = found == len(relevant_tables)
        scores[f"table_recall@{k}"] = found / len(relevant_tables)
    return scores


def summarize(scores: list[dict]) -> dict:
    """一组题（整体或某个库）的指标汇总，返回 summary 中 scores 字段的内容。

    scores 是这组题各自的 score_case 结果。
    """
    return {}
