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


def expand_to_documents(hits: list[RetrievalHit], chunks: list[RetrievalHit]) -> list[RetrievalHit]:
    """Small-to-Big：把一次检索的 Hit 换成它们所属各表的全部 Chunk。纯函数，不读数据库。

    参数:
        hits: 一次检索的 Hit，按 rank 升序。
        chunks: 同一 Knowledge Scope 的全部 Chunk，按全量读取的顺序。
    返回:
        命中的每张表的全部 Chunk（表、列、关系，按 hit_table 归属）。表按 Hit 第一次出现的顺序排，
        表内保持 chunks 的顺序；rank 从 1 重新连续编号，所以 assemble_context 排出的表序就是检索的表序，
        每张表的内容与全量知识里这张表相同。没被命中的表不出现。
    """
    tables = list(dict.fromkeys(hit_table(hit) for hit in hits))
    by_table = {table: [chunk for chunk in chunks if hit_table(chunk) == table] for table in tables}
    expanded = [chunk for table in tables for chunk in by_table[table]]
    return [chunk.model_copy(update={"rank": rank}) for rank, chunk in enumerate(expanded, 1)]


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