"""渲染 Retrieval Context：用两个库的真实 Chunk 正文验证每一项"保留"和"去掉"，以及两种结构写法。"""

import json
from dataclasses import replace

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



CONVENTION = "数据约定: 每条化学键正反各存一行。"
ELEMENT_VALUES = "取值: 全部 3 个：'c', 'cl', 'h'"
ATOM_ID_VALUES = "取值: 样例（出现最多的 3 个，共 9111 个不同值）：'TR000_1', 'TR000_2', 'TR000_3'"


@pytest.mark.parametrize("structure_format", ["columns", "ddl"])
def test_values_and_data_convention_are_kept_in_both_structure_formats(structure_format):
    """输入：带取值样例和数据约定的新 Chunk。connected 的表 Chunk 在记录粒度后有数据约定；element 的取值
    接在 15 行取值解释之后，atom_id 没有取值解释、直接是取值样例。

    输出：与旧 Chunk 的渲染相比只多三行——表的记录粒度后面多"数据约定"，命中的列在说明后面多"取值"；
    columns 和 ddl 两种写法一样。没被命中的列（如 molecule_id）仍然只在字段目录或建表语句里。
    """
    def with_line(content: str, after: str, line: str) -> str:
        return content.replace(after, after + "\n" + line, 1)

    atom, connected = _toxicology()
    element, atom_id = atom.columns
    context = [
        replace(atom, columns=[
            element.model_copy(update={"content": chunks.TOXICOLOGY_ATOM_ELEMENT + "\n" + ELEMENT_VALUES}),
            atom_id.model_copy(update={"content": chunks.TOXICOLOGY_ATOM_ATOM_ID + "\n" + ATOM_ID_VALUES})]),
        replace(connected, table_chunk=with_line(
            chunks.TOXICOLOGY_CONNECTED_TABLE, "记录粒度: 一对原子及连接它们的化学键。", CONVENTION)),
    ]
    table_ddl = {"atom": 'CREATE TABLE "atom" (atom_id TEXT, molecule_id TEXT, element TEXT)',
                 "connected": 'CREATE TABLE "connected" (atom_id TEXT, atom_id2 TEXT, bond_id TEXT)'}
    old = render_context(_toxicology(), structure_format=structure_format, table_ddl=table_ddl)

    rendered = render_context(context, structure_format=structure_format, table_ddl=table_ddl)

    expected = with_line(old, "    • ca: Calcium", "    " + ELEMENT_VALUES)
    expected = with_line(expected, '  列 "atom_id" TEXT: the unique id of atoms', "    " + ATOM_ID_VALUES)
    expected = with_line(expected, "  记录粒度: 一对原子及连接它们的化学键。", "  " + CONVENTION)
    assert rendered == expected
    assert rendered.count("\n") == old.count("\n") + 3


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


def _composite_relationship(tmp_path, *, meaning: str | None) -> list[TableContext]:
    """用真实的 Corpus 构建流程生成原创库 demo 的复合外键 events(tenant_ref, item_ref) → items。

    meaning 为 None 时语义摘要不覆盖这条外键，模板写"端点含义"而不写"关联语义"。
    """
    from conftest import _curated, _write_json

    from querypilot.knowledge.api import load_corpus
    from querypilot.knowledge.knowledge_corpus._corpus import build_knowledge_corpus

    semantic = tmp_path / "config" / "semantics.json"
    semantic.parent.mkdir()
    relationships = [] if meaning is None else [{
        "source_table": "events", "source_columns": ["tenant_ref", "item_ref"], "target_table": "items",
        "target_columns": ["tenant_id", "item_id"], "meaning": meaning}]
    _write_json(semantic, {"database_id": "demo", "version": "t", "author": "原创测试",
                           "tables": {"items": {"purpose": "租户内物品。", "row_grain": "一件物品。"},
                                      "events": {"purpose": "物品事件。", "row_grain": "一次事件。"}},
                           "relationships": relationships})
    build_knowledge_corpus(_curated(tmp_path), semantic, tmp_path / "corpus", "demo")
    chunks = load_corpus(tmp_path / "corpus").chunks
    table = next(c for c in chunks if c.knowledge_type == "table" and c.table_name == "events")
    relationship = next(c for c in chunks if c.knowledge_type == "relationship")
    return [TableContext(table="events", table_chunk=table.content, columns=[],
                         relationships=[_hit(1, "relationship", relationship.content)])]


EVENTS_HEADER = '''表 "events"
  表用途: 物品事件。
  记录粒度: 一次事件。
  字段目录: "item_ref", "tenant_ref"
  主键: 未声明
  外键: "tenant_ref", "item_ref" → "items" ("tenant_id", "item_id")
  关系 "events"."tenant_ref" → "items"."tenant_id"; "events"."item_ref" → "items"."item_id"'''
COMPOSITE = "    复合外键: 上述字段对共同构成一条关系，JOIN 必须保留全部 AND 条件。"


def test_composite_foreign_key_keeps_the_keep_every_and_condition_line(tmp_path):
    """输入：带关联语义的复合外键（两个字段对）。

    输出：关系行带关联语义，下面保留"复合外键：JOIN 必须保留全部 AND 条件"——它提醒模型别只写一半的
    ON 条件，不能和候选 JOIN 一起丢掉。
    """
    rendered = render_context(_composite_relationship(tmp_path, meaning="事件关联同租户物品。"),
                              structure_format="columns")

    assert rendered == EVENTS_HEADER + ": 事件关联同租户物品。\n" + COMPOSITE


def test_relationship_without_semantics_shows_each_endpoint_meaning(tmp_path):
    """输入：语义摘要没有覆盖的复合外键，模板对每个字段对写一行"端点含义"，没有"关联语义"。

    输出：关系行只有字段对，下面是两行端点含义（一行都不少）和复合外键提醒；不因缺关联语义而报错。
    """
    rendered = render_context(_composite_relationship(tmp_path, meaning=None), structure_format="columns")

    assert rendered == EVENTS_HEADER + "\n" + "\n".join([
        '    端点含义: "tenant_ref"（含义: referenced tenant identifier） → "tenant_id"（含义: tenant identifier）',
        '    端点含义: "item_ref"（含义: referenced item identifier） → "item_id"（含义: item identifier）',
        COMPOSITE])


def test_a_line_the_renderer_does_not_know_fails_loudly_instead_of_being_swallowed():
    """输入：关系 Chunk 里多出一行模板新加的标签（这里用"新标签"模拟）。

    输出：ValueError。只有取值解释和角色区别允许续行；别处出现不认识的行说明模板变了，要同步改渲染规则，
    不能把它悄悄接到上一行后面再随上一行丢掉。
    """
    content = chunks.TOXICOLOGY_ATOM_FK0.replace("ON UPDATE", "新标签: 模板新增的内容\nON UPDATE")
    context = [TableContext(table="atom", table_chunk=chunks.TOXICOLOGY_ATOM_TABLE, columns=[],
                            relationships=[_hit(1, "relationship", content)])]

    with pytest.raises(ValueError):
        render_context(context, structure_format="columns")
