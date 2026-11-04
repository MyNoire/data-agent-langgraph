"""检索评测的计分规则。

口径见 GLOSSARY「评测」一节：Table Ranking、Table Complete Recall@K、Table Recall@K、
Tables to Cover（K 以表计），以及只作诊断的 Column Recall@K（K 以 Hit 计）。
"""

from querypilot.retrieval.api import RetrievalHit

from .models import RelevantColumn
import json

TABLE_KS = (2, 3, 5)
# Column Recall 在前 COLUMN_K 个 Hit 里找列；不能超过运行器的检索预算 TOP_K。
COLUMN_K = 20


def score_case(relevant_tables: list[str], relevant_columns: list[RelevantColumn],
               hits: list[RetrievalHit]) -> dict:
    """单题计分，返回逐题结果中 scores 字段的内容。

    hits 按 rank 排列；input_error 的题传入空列表。
    """
    hit_tables = []
    for hit in hits:
        if hit.knowledge_type == "relationship":
            table_name = json.loads(hit.chunk_key)[3]
            if table_name not in hit_tables:
                hit_tables.append(table_name)
        else:
            if hit.table_name not in hit_tables:
                hit_tables.append(hit.table_name)
    table_ranks = {}
    for table_name in relevant_tables:
        table_ranks[table_name] = hit_tables.index(table_name) + 1 if table_name in hit_tables else None
    return {
        "table_ranks": table_ranks
    }


def summarize(scores: list[dict]) -> dict:
    """一组题（整体或某个库）的指标汇总，返回 summary 中 scores 字段的内容。

    scores 是这组题各自的 score_case 结果。
    """
    return {}
