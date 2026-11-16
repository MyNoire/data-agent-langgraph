"""Retrieval Context：把一组 Hit 按所属的表归拢，作为渲染给模型的 Schema 的输入。"""

from dataclasses import dataclass

from querypilot.retrieval.api import RetrievalHit, hit_table


@dataclass(frozen=True)
class TableContext:
    """Retrieval Context 里的一张表。

    table 是表名；table_chunk 是这张表的表 Chunk 正文（表用途、记录粒度、全部列名、主键和外键）；
    columns 和 relationships 是归到这张表的列 Hit 与关系 Hit，各自按 rank 升序。
    """

    table: str
    table_chunk: str
    columns: list[RetrievalHit]
    relationships: list[RetrievalHit]


def assemble_context(hits: list[RetrievalHit], table_chunks: dict[str, str]) -> list[TableContext]:
    """把一组 Hit 组装成 Retrieval Context。纯函数，不读数据库。

    参数:
        hits: 一次检索的 Hit，任意顺序；用 retrieval 的 hit_table 判断每个 Hit 属于哪张表。
        table_chunks: 表名 → 表 Chunk 正文，至少包含每张被命中的表。
    返回:
        每张被命中的表一项，按这张表的 Hit 的最小 rank 升序。
    """
    tables: dict[str, TableContext] = {}
    for hit in sorted(hits, key=lambda hit: hit.rank):
        table_name = hit_table(hit)
        table = tables.setdefault(table_name, TableContext(
            table=table_name, table_chunk=table_chunks[table_name], columns=[], relationships=[]))
        # 表 Hit 不追加：它的正文就是 table_chunk。
        if hit.knowledge_type == "relationship":
            table.relationships.append(hit)
        elif hit.knowledge_type == "column":
            table.columns.append(hit)
    return list(tables.values())