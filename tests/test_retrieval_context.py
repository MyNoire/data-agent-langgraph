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


def _relationship(rank: int, source: str, target: str, index: int = 0) -> RetrievalHit:
    """source → target 外键的关系 Hit；和真实 Corpus 一样，三个定位字段为空，源表只写在 chunk_key 里。"""
    key = json.dumps(["relationship", "student_club", "main", source, index])
    return RetrievalHit(rank=rank, score=1.0 / rank, chunk_id=str(uuid5(NAMESPACE_URL, key)), chunk_key=key,
                        knowledge_type="relationship", title=f"{source} → {target}",
                        content=f"{source} → {target} 的关系 Chunk", content_hash="c" * 64)


def test_relationship_hits_go_to_the_source_table_after_its_columns():
    """输入：名次 1 是 attendance → member 的关系，2 是 member.first_name，3 是 attendance → event 的关系，
    4 是 attendance.link_to_event。

    输出：attendance 在前（最好名次 1），两条关系都归它，排在它的列后面、按名次；member 只有自己的列，
    不因为是关系的目标表而多出关系；event 只是目标表、没有自己的 Hit，不出现。
    """
    to_member = _relationship(1, "attendance", "member", 0)
    first_name = _column(2, "member", "first_name")
    to_event = _relationship(3, "attendance", "event", 1)
    link_to_event = _column(4, "attendance", "link_to_event")

    context = assemble_context([to_member, first_name, to_event, link_to_event], TABLE_CHUNKS)

    assert context == [
        TableContext(table="attendance", table_chunk="attendance 的表 Chunk",
                     columns=[link_to_event], relationships=[to_member, to_event]),
        TableContext(table="member", table_chunk="member 的表 Chunk", columns=[first_name], relationships=[]),
    ]


def _table(rank: int, table: str) -> RetrievalHit:
    """table 的表 Hit；正文与 TABLE_CHUNKS 里这张表的表 Chunk 相同，和真实检索一样。"""
    key = json.dumps(["table", "student_club", "main", table])
    return RetrievalHit(rank=rank, score=1.0 / rank, chunk_id=str(uuid5(NAMESPACE_URL, key)), chunk_key=key,
                        knowledge_type="table", title=table, content=TABLE_CHUNKS[table],
                        content_hash="c" * 64, schema_name="main", table_name=table)


def test_table_hits_count_for_table_order_but_their_chunk_appears_only_as_table_chunk():
    """输入：名次 1 是 member.first_name，2 是 event 的表 Hit，3 是 member 的表 Hit。

    输出：member 在前、event 在后（表 Hit 的名次也算这张表的名次）；表 Chunk 只放在 table_chunk 里，
    不再出现在 columns 或 relationships 里；event 只有表 Hit，也有一项。
    """
    first_name = _column(1, "member", "first_name")
    event = _table(2, "event")
    member = _table(3, "member")

    context = assemble_context([first_name, event, member], TABLE_CHUNKS)

    assert context == [
        TableContext(table="member", table_chunk="member 的表 Chunk", columns=[first_name], relationships=[]),
        TableContext(table="event", table_chunk="event 的表 Chunk", columns=[], relationships=[]),
    ]


def test_the_same_hits_in_any_list_order_give_the_same_context():
    """输入：同一组 5 个 Hit，按名次顺序和倒序各传一次；输出：两次的结果相同，只由 rank 决定。"""
    hits = [_relationship(1, "attendance", "member"), _column(2, "member", "first_name"), _table(3, "event"),
            _column(4, "attendance", "link_to_event"), _column(5, "member", "last_name")]

    assert assemble_context(list(reversed(hits)), TABLE_CHUNKS) == assemble_context(hits, TABLE_CHUNKS)
