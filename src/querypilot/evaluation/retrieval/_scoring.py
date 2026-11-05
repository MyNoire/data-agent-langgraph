"""检索评测的计分规则。

口径见 GLOSSARY「评测」一节：首次命中名次、Table Complete Recall@K、Table Recall@K、
Hits to Cover，以及只作诊断的 Column Recall@K。K 都以 Hit 计，和检索的 top_k 同一单位。
"""

import json
from statistics import median

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

    hits 按 rank 排列；input_error 的题传入空列表。名次都是 Hit rank，没命中为空。
    """
    # 每张相关表的首次命中名次：表、列、关系三类 Hit 都算它所属的表。
    first_table_ranks: dict[str, int] = {}
    for hit in hits:
        first_table_ranks.setdefault(_hit_table(hit), hit.rank)
    table_ranks = {table: first_table_ranks.get(table) for table in relevant_tables}

    # 拿全相关表至少要多少个 Hit：最晚命中的那张表的名次；有一张没命中就为空。
    ranks = list(table_ranks.values())
    hits_to_cover = None if None in ranks else max(ranks)

    scores: dict = {"table_ranks": table_ranks}
    for k in TABLE_KS:
        found = sum(rank is not None and rank <= k for rank in ranks)
        scores[f"table_complete_recall@{k}"] = hits_to_cover is not None and hits_to_cover <= k
        scores[f"table_recall@{k}"] = found / len(relevant_tables)
    scores["hits_to_cover"] = hits_to_cover

    # 每个相关列的首次命中名次：只有这一列自己的列 Chunk 算；按（表, 列）区分同名列。
    first_column_ranks: dict[tuple[str | None, str | None], int] = {}
    for hit in hits:
        if hit.knowledge_type == "column":
            first_column_ranks.setdefault((hit.table_name, hit.column_name), hit.rank)
    column_ranks = {f"{column.table_name}.{column.column_name}":
                    first_column_ranks.get((column.table_name, column.column_name))
                    for column in relevant_columns}
    scores["column_ranks"] = column_ranks

    # Column Recall：没有相关列时为空，不参与汇总平均。
    if relevant_columns:
        found = sum(rank is not None and rank <= COLUMN_K for rank in column_ranks.values())
        scores[f"column_recall@{COLUMN_K}"] = found / len(relevant_columns)
    else:
        scores[f"column_recall@{COLUMN_K}"] = None
    return scores


def _mean(values: list) -> float | None:
    """平均数；没有值可算时为空。"""
    return sum(values) / len(values) if values else None


def summarize(scores: list[dict]) -> dict:
    """一组题（整体或某个库）的指标汇总，返回 summary 中 scores 字段的内容。

    scores 是这组题各自的 score_case 结果。表级召回对所有题取平均（input_error 题按 0 计入分母）；
    Hits to Cover 只对能全覆盖的题取中位数和平均数，盖不全的题数另报；Column Recall 只对有相关列的题
    取平均，没有相关列的题数另报。
    """
    summary: dict = {}
    for k in TABLE_KS:
        for key in (f"table_complete_recall@{k}", f"table_recall@{k}"):
            summary[key] = _mean([score[key] for score in scores])
    covered = [score["hits_to_cover"] for score in scores if score["hits_to_cover"] is not None]
    summary["hits_to_cover_median"] = median(covered) if covered else None
    summary["hits_to_cover_mean"] = _mean(covered)
    summary["uncovered_cases"] = len(scores) - len(covered)
    column_key = f"column_recall@{COLUMN_K}"
    column_recalls = [score[column_key] for score in scores if score[column_key] is not None]
    summary[column_key] = _mean(column_recalls)
    summary["no_relevant_column_cases"] = len(scores) - len(column_recalls)
    return summary
