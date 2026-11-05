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
    # 表排名
    for k in TABLE_KS:
        found = sum(rank is not None and rank <= k for rank in table_ranks.values())
        scores[f"table_complete_recall@{k}"] = found == len(relevant_tables)
        scores[f"table_recall@{k}"] = found / len(relevant_tables)
    # 需要多少top_K才能覆盖所需要的表
    for top_k in table_ranks.values():
        if top_k is None:
            scores["hits_to_cover"] = None
            break
        scores["hits_to_cover"] = max(top_k, scores.get("hits_to_cover", 0))
    # 列ranks构造
    column_first_ranks: dict[tuple[str | None, str | None], int] = {}
    for hit in hits:
        if hit.knowledge_type == "column":
            column_first_ranks.setdefault((hit.table_name, hit.column_name), hit.rank)
    column_ranks = {f"{column.table_name}.{column.column_name}": column_first_ranks.get((column.table_name, column.column_name)) for column in relevant_columns}
    scores["column_ranks"] = column_ranks
    # 列排名
    if not relevant_columns:
        scores[f"column_recall@{COLUMN_K}"] = None
    else:
        found = sum(rank is not None and rank <= COLUMN_K for rank in column_ranks.values())
        scores[f"column_recall@{COLUMN_K}"] = found / len(relevant_columns)
    return scores


def summarize(scores: list[dict]) -> dict:
    """一组题（整体或某个库）的指标汇总，返回 summary 中 scores 字段的内容。

    scores 是这组题各自的 score_case 结果。
    """
    return {}
