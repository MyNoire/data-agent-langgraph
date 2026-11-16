"""把 Retrieval Context 渲染成给模型的 Schema 文本：按行标签筛选和改写 Chunk 正文。

Chunk 正文由 Corpus 的固定模板生成，每行以标签开头、行的顺序固定；只有取值解释和角色区别会续行。
这里的标签照抄模板，模板改了要同步改这里并升 SCHEMA_RENDER_VERSION。
"""

from typing import Literal

from ._context import TableContext

SCHEMA_RENDER_VERSION = "r5-schema-v1"
"""渲染规则的版本，写进评测报告；保留或去掉的内容、排版任何一处改变都要升版本。"""

StructureFormat = Literal["columns", "ddl"]
"""表结构的写法：columns 为字段目录、主键、外键三行；ddl 为这张表的建表语句原文。"""

UNKNOWN = "来源未说明"

TABLE_LABELS = ("数据库", "表用途", "记录粒度", "字段目录", "行标识线索（主键声明、成员顺序）", "出向关联（声明外键）")
COLUMN_LABELS = ("数据库", "声明类型", "not_null 声明标志", "默认 SQL 表达式（仅文本）", "展示名", "字段解释", "取值解释")
RELATIONSHIP_LABELS = ("数据库", "引用方向", "同一条外键的有序字段对", "关联语义",
                       "候选 JOIN（source 为源表别名，target 为目标表别名；仅展示 ON 条件）", "角色区别", "ON UPDATE")


def _fields(content: str, labels: tuple[str, ...]) -> dict[str, str]:
    """把正文拆成 标签 → 值。不以已知标签开头的行是上一个字段的续行，原样接在后面（以换行分隔）。"""
    fields: dict[str, str] = {}
    current = None
    for line in content.split("\n"):
        label = next((label for label in labels if line.startswith(label + ":") or line.startswith(label + " ")), None)
        if label is None:
            fields[current] += "\n" + line
        else:
            current = label
            fields[label] = line[len(label) + 1:].lstrip()
    return fields


def _indented(label: str, value: str, indent: str) -> list[str]:
    """一个可能多行的字段：首行带标签，续行同样缩进，内容不改。"""
    first, *rest = value.split("\n")
    return [f"{indent}{label}: {first}", *(indent + line for line in rest)]


def _table_lines(table: TableContext, structure_format: StructureFormat, table_ddl: dict[str, str] | None) -> list[str]:
    """一张表的全部行：表头、表用途、记录粒度、结构，然后是命中的列和关系。"""
    fields = _fields(table.table_chunk, TABLE_LABELS)
    lines = [f'表 "{table.table}"', f"  表用途: {fields['表用途']}", f"  记录粒度: {fields['记录粒度']}"]
    if structure_format == "ddl":
        lines.append(table_ddl[table.table])
    else:
        lines += [f"  字段目录: {fields['字段目录']}",
                  f"  主键: {fields['行标识线索（主键声明、成员顺序）']}",
                  f"  外键: {fields['出向关联（声明外键）']}"]
    for hit in table.columns:
        column = _fields(hit.content, COLUMN_LABELS)
        explanation = column["字段解释"]
        lines.append(f'  列 "{hit.column_name}" {column["声明类型"]}' + ("" if explanation == UNKNOWN else f": {explanation}"))
        for label in ("展示名", "取值解释"):
            if column[label] != UNKNOWN:
                lines += _indented(label, column[label], "    ")
    for hit in table.relationships:
        relationship = _fields(hit.content, RELATIONSHIP_LABELS)
        lines.append(f"  关系 {relationship['同一条外键的有序字段对']}: {relationship['关联语义']}")
        if "角色区别" in relationship:
            lines += _indented("角色区别", relationship["角色区别"], "    ")
    return lines


def render_context(context: list[TableContext], *, structure_format: StructureFormat,
                   table_ddl: dict[str, str] | None = None) -> str:
    """把 Retrieval Context 渲染成 Schema 文本，规则版本为 SCHEMA_RENDER_VERSION。

    参数:
        context: assemble_context 的结果，表的顺序和表内列、关系的顺序原样沿用。
        structure_format: columns 写字段目录、主键、外键三行；ddl 把这三行换成建表语句原文。
        table_ddl: 表名 → 建表语句；ddl 写法必须提供，且包含 context 里的每张表。
    返回:
        每张表一段、段间空一行的文本。只放有用的结构、知识和线索：去掉数据库名、"main". 前缀、
        声明标志、默认值、值为"来源未说明"的字段、引用方向、候选 JOIN 和 ON UPDATE / DELETE / MATCH。
    """
    sections = ["\n".join(_table_lines(table, structure_format, table_ddl)) for table in context]
    return "\n\n".join(sections).replace('"main".', "")
