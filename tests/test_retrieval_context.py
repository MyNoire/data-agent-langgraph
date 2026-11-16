"""组装 Retrieval Context：用手写的 Hit 列表验证归拢、补表 Chunk、去重和排序。"""

import json
from uuid import NAMESPACE_URL, uuid5

from querypilot.querying.api import TableContext, assemble_context
from querypilot.retrieval.api import RetrievalHit

TABLE_CHUNKS = {"member": "member 的表 Chunk", "event": "event 的表 Chunk", "attendance": "attendance 的表 Chunk"}


def _column(rank: int, table: str, column: str) -> RetrievalHit:
    """table.column 的列 Hit，名次为 rank；正文写明是哪一列，方便看断言。"""
    key = json.dumps(["column", "student_club", "main", table, column])
    return RetrievalHit(rank=rank, score=1.0 / rank, chunk_id=str(uuid5(NAMESPACE_URL, key)), chunk_key=key,
                        knowledge_type="column", title=f"{table}.{column}", content=f"{table}.{column} 的列 Chunk",
                        content_hash="c" * 64, schema_name="main", table_name=table, column_name=column)


def test_column_hits_are_grouped_by_table_and_each_table_carries_its_table_chunk():
    """输入：三个列 Hit，名次 1 和 3 属于 member，名次 2 属于 event；表 Chunk 都没被命中。

    输出：两项，member 在前（它最好的名次是 1）、event 在后；每项带上自己的表 Chunk 正文，
    列按名次排列；没被命中的 attendance 不出现。
    """
    first_name = _column(1, "member", "first_name")
    event_name = _column(2, "event", "event_name")
    last_name = _column(3, "member", "last_name")

    context = assemble_context([first_name, event_name, last_name], TABLE_CHUNKS)

    assert context == [
        TableContext(table="member", table_chunk="member 的表 Chunk", columns=[first_name, last_name], relationships=[]),
        TableContext(table="event", table_chunk="event 的表 Chunk", columns=[event_name], relationships=[]),
    ]
