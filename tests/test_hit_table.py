"""Hit 属于哪张表：表和列 Hit 取自己的表，关系 Hit 取外键的源表。"""

import json
from uuid import NAMESPACE_URL, uuid5

from querypilot.retrieval.api import RetrievalHit, hit_table


def _hit(knowledge_type: str, table: str | None, column: str | None, chunk_key: str) -> RetrievalHit:
    """一个 Hit；只有类型、定位字段和 chunk_key 与归属有关，其余字段取固定值。"""
    return RetrievalHit(rank=1, score=1.0, chunk_id=str(uuid5(NAMESPACE_URL, chunk_key)), chunk_key=chunk_key,
                        knowledge_type=knowledge_type, title="t", content="c", content_hash="c" * 64,
                        schema_name=None if table is None else "main", table_name=table, column_name=column)


def test_table_and_column_hits_belong_to_their_own_table():
    """输入：member 的表 Hit 和 member.first_name 的列 Hit；输出：两者都属于 member。"""
    table = _hit("table", "member", None, json.dumps(["table", "student_club", "main", "member"]))
    column = _hit("column", "member", "first_name",
                  json.dumps(["column", "student_club", "main", "member", "first_name"]))

    assert (hit_table(table), hit_table(column)) == ("member", "member")


def test_relationship_hit_belongs_to_the_source_table_of_the_foreign_key():
    """输入：attendance → member 的关系 Hit，定位字段都为空，源表只写在 chunk_key 里。

    输出：属于源表 attendance，不是目标表 member。
    """
    relationship = _hit("relationship", None, None, json.dumps(["relationship", "student_club", "main", "attendance", 0]))

    assert hit_table(relationship) == "attendance"
