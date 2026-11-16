"""渲染 Retrieval Context：用两个库的真实 Chunk 正文验证每一项"保留"和"去掉"，以及两种结构写法。"""

import json
import pytest
from uuid import NAMESPACE_URL, uuid5

from querypilot.querying.api import TableContext, render_context
from querypilot.retrieval.api import RetrievalHit

import _real_chunks as chunks


def _hit(rank: int, knowledge_type: str, content: str, table: str | None = None, column: str | None = None) -> RetrievalHit:
    """用真实正文构造的 Hit；渲染只读 content、table_name 和 column_name。"""
    key = json.dumps([knowledge_type, table, column, rank])
    return RetrievalHit(rank=rank, score=1.0, chunk_id=str(uuid5(NAMESPACE_URL, key)), chunk_key=key,
                        knowledge_type=knowledge_type, title="t", content=content, content_hash="c" * 64,
                        schema_name=None if table is None else "main", table_name=table, column_name=column)


def _toxicology() -> list[TableContext]:
    """atom 带两列（element 的取值解释有 15 行，atom_id 没有取值解释）和一条关系；connected 只有两条同指 atom 的外键。"""
    return [
        TableContext(table="atom", table_chunk=chunks.TOXICOLOGY_ATOM_TABLE,
                     columns=[_hit(1, "column", chunks.TOXICOLOGY_ATOM_ELEMENT, "atom", "element"),
                              _hit(3, "column", chunks.TOXICOLOGY_ATOM_ATOM_ID, "atom", "atom_id")],
                     relationships=[_hit(4, "relationship", chunks.TOXICOLOGY_ATOM_FK0)]),
        TableContext(table="connected", table_chunk=chunks.TOXICOLOGY_CONNECTED_TABLE, columns=[],
                     relationships=[_hit(2, "relationship", chunks.TOXICOLOGY_CONNECTED_FK1),
                                    _hit(5, "relationship", chunks.TOXICOLOGY_CONNECTED_FK2)]),
    ]


ROLES = '''    角色区别: 源表有 2 条外键指向 "atom"，源端角色分别为:
    - 外键 1: "atom_id2"
    - 外键 2: "atom_id"
    同时引用这些角色时，为目标表使用不同别名，分别连接各自源列。'''

TOXICOLOGY_COLUMNS = f'''表 "atom"
  表用途: 记录分子中的原子及其元素类型，用于分析分子的元素组成或按元素筛选原子。
  记录粒度: 一个原子，关联其所属分子。
  字段目录: "atom_id", "molecule_id", "element"
  主键: "atom_id"
  外键: "molecule_id" → "molecule" ("molecule_id")
  列 "element" TEXT: the element of the toxicology
    取值解释: • cl: chlorine
    • c: carbon
    • h: hydrogen
    • o: oxygen
    • s: sulfur
    • n: nitrogen
    • p: phosphorus
    • na: sodium
    • br: bromine
    • f: fluorine
    • i: iodine
    • sn: Tin
    • pb: lead
    • te: tellurium
    • ca: Calcium
  列 "atom_id" TEXT: the unique id of atoms
  关系 "atom"."molecule_id" → "molecule"."molecule_id": 原子属于该分子；可将原子元素组成与分子的致癌性标签关联分析。

表 "connected"
  表用途: 记录两个原子之间由哪条化学键连接，将原子与化学键组织成可查询的连接关系。
  记录粒度: 一对原子及连接它们的化学键。
  字段目录: "atom_id", "atom_id2", "bond_id"
  主键: "atom_id", "atom_id2"
  外键: "bond_id" → "bond" ("bond_id"); "atom_id2" → "atom" ("atom_id"); "atom_id" → "atom" ("atom_id")
  关系 "connected"."atom_id2" → "atom"."atom_id": 连接关系中的第二个原子；可查询第二端原子的元素属性，与 atom_id 的第一端角色区分。
{ROLES}
  关系 "connected"."atom_id" → "atom"."atom_id": 连接关系中的第一个原子；可查询第一端原子的元素属性。
{ROLES}'''


def test_columns_format_keeps_structure_knowledge_and_clues_and_drops_the_rest():
    """输入：toxicology 的两张表，columns 写法。

    输出（逐字相同）：
    - 表：保留表用途、记录粒度、字段目录、主键、出向外键；去掉"数据库: toxicology"一行和所有 "main". 前缀。
    - 列：保留列名、声明类型、字段解释；element 的 15 行取值解释完整保留；去掉 not_null / 主键位置 / hidden
      一行、默认 SQL 表达式、值为"来源未说明"的展示名和取值解释，以及只是列名换个写法的展示名（atom_id 的
      "atom id"）。
    - 关系：保留字段对、关联语义，以及同表两条外键都指向 atom 时的角色区别；去掉引用方向、候选 JOIN 和
      ON UPDATE / DELETE / MATCH。
    - 每张表里列在前、关系在后，各自保持组装给出的顺序；表之间空一行。
    """
    assert render_context(_toxicology(), structure_format="columns") == TOXICOLOGY_COLUMNS


def test_student_club_column_with_no_display_name_or_value_explanation_is_one_line():
    """输入：student_club 的 member（命中 position 列）和 attendance（命中 attendance → member 关系），columns 写法。

    输出：position 的展示名和取值解释都是"来源未说明"，整列只剩一行；member 的两条出向外键都在。
    """
    context = [
        TableContext(table="member", table_chunk=chunks.STUDENT_CLUB_MEMBER_TABLE,
                     columns=[_hit(1, "column", chunks.STUDENT_CLUB_MEMBER_POSITION, "member", "position")],
                     relationships=[]),
        TableContext(table="attendance", table_chunk=chunks.STUDENT_CLUB_ATTENDANCE_TABLE, columns=[],
                     relationships=[_hit(2, "relationship", chunks.STUDENT_CLUB_ATTENDANCE_FK0)]),
    ]

    assert render_context(context, structure_format="columns") == '''表 "member"
  表用途: 记录社团会员的姓名、在社团中的职位、联系方式和 T 恤尺码，是按会员筛选、统计并关联专业、家乡和活动的入口。
  记录粒度: 一名社团会员。
  字段目录: "member_id", "first_name", "last_name", "email", "position", "t_shirt_size", "phone", "zip", "link_to_major"
  主键: "member_id"
  外键: "zip" → "zip_code" ("zip_code"); "link_to_major" → "major" ("major_id")
  列 "position" TEXT: The position the member holds in the club

表 "attendance"
  表用途: 记录哪位会员参加了哪场活动，用于统计活动的出席人数或会员参加过的活动。
  记录粒度: 一名会员出席一场活动。
  字段目录: "link_to_event", "link_to_member"
  主键: "link_to_event", "link_to_member"
  外键: "link_to_member" → "member" ("member_id"); "link_to_event" → "event" ("event_id")
  关系 "attendance"."link_to_member" → "member"."member_id": 出席记录对应的会员；可查询会员参加过哪些活动，或按会员属性分析出席情况。'''


def test_ddl_format_replaces_only_the_structure_lines_with_the_create_table_statement():
    """输入：同一份 toxicology 上下文，ddl 写法，传入两张表的建表语句。

    输出：每张表的字段目录、主键、外键三行换成这张表的建表语句原文（不缩进、不改写），其余每一行与
    columns 写法相同。
    """
    table_ddl = {
        "atom": 'CREATE TABLE "atom"\n(\n    atom_id TEXT not null primary key,\n    molecule_id TEXT,\n    element TEXT\n)',
        "connected": 'CREATE TABLE "connected"\n(\n    atom_id TEXT not null,\n    atom_id2 TEXT not null,\n    bond_id TEXT\n)',
        "bond": 'CREATE TABLE "bond" (bond_id TEXT)',   # 不在上下文里的表：不出现
    }
    columns_lines = TOXICOLOGY_COLUMNS.split("\n")
    structure = [line for line in columns_lines if line.startswith(("  字段目录:", "  主键:", "  外键:"))]
    expected = TOXICOLOGY_COLUMNS
    for table in ("atom", "connected"):
        three = "\n".join(structure[:3])
        structure = structure[3:]
        expected = expected.replace(three, table_ddl[table], 1)

    rendered = render_context(_toxicology(), structure_format="ddl", table_ddl=table_ddl)

    assert rendered == expected
    assert "bond_id TEXT)" not in rendered


@pytest.mark.parametrize(("column", "display_name", "kept"), [
    ("atom_id", "atom id", False),
    ("atom_id", "Atom ID", False),
    ("atom_id2", "atom id 2", False),
    ("atom_id", "Atom identifier", True),
])
def test_display_name_is_kept_only_when_it_says_more_than_the_column_name(column, display_name, kept):
    """输入：atom_id 列的真实正文，列名和展示名换成几种组合（atom_id2 → "atom id 2" 是 toxicology 的真实写法）。

    输出：展示名去掉空格和下划线、忽略大小写后与列名相同就不放；否则放在列行下面。
    """
    content = chunks.TOXICOLOGY_ATOM_ATOM_ID.replace("展示名: atom id", f"展示名: {display_name}")
    context = [TableContext(table="atom", table_chunk=chunks.TOXICOLOGY_ATOM_TABLE,
                            columns=[_hit(1, "column", content, "atom", column)], relationships=[])]

    rendered = render_context(context, structure_format="columns")

    assert (f"    展示名: {display_name}" in rendered) is kept
