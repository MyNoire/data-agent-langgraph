"""把 Retrieval Context 渲染成给模型的 Schema 文本：按行标签筛选和改写 Chunk 正文。

Chunk 正文由 Corpus 的固定模板生成，每行以标签开头、行的顺序固定；只有取值解释和角色区别会续行，
端点含义每个字段对各一行。这里的标签照抄模板（knowledge_corpus/_render.py），模板改了要同步改这里并升
SCHEMA_RENDER_VERSION；出现不认识的行时报 ValueError，不悄悄接到上一行。
"""

from typing import Literal

from ._context import TableContext

SCHEMA_RENDER_VERSION = "r5-schema-v3"
"""渲染规则的版本，写进评测报告；保留或去掉的内容、排版任何一处改变都要升版本。"""

StructureFormat = Literal["columns", "ddl"]
"""表结构的写法：columns 为字段目录、主键、外键三行；ddl 为这张表的建表语句原文。"""

UNKNOWN = "来源未说明"

TABLE_LABELS = ("数据库", "表用途", "记录粒度", "数据约定", "字段目录", "行标识线索（主键声明、成员顺序）", "出向关联（声明外键）")
COLUMN_LABELS = ("数据库", "声明类型", "not_null 声明标志", "默认 SQL 表达式（仅文本）", "展示名", "字段解释", "取值解释",
                 "取值")
RELATIONSHIP_LABELS = ("数据库", "引用方向", "同一条外键的有序字段对", "关联语义", "端点含义",
                       "候选 JOIN（source 为源表别名，target 为目标表别名；仅展示 ON 条件）", "复合外键", "角色区别",
                       "ON UPDATE")
MULTILINE_LABELS = ("取值解释", "角色区别")
"""值可以跨多行的字段；其他字段后面出现不以已知标签开头的行，说明模板变了。"""
REPEATED_LABELS = ("端点含义",)
"""一个 Chunk 里可以出现多次的字段（每个字段对一行），各行以换行连在一起。"""


def _fields(content: str, labels: tuple[str, ...]) -> dict[str, str]:
    """把正文拆成 标签 → 值。

    取值解释和角色区别后面不以已知标签开头的行是它的续行，原样接在后面（以换行分隔）；端点含义可重复，
    各行以换行连在一起。其他位置出现不认识的行时报 ValueError（消息只含标签，不含正文）。
    """
    fields: dict[str, str] = {}
    current = None
    for line in content.split("\n"):
        label = next((label for label in labels if line.startswith(label + ":") or line.startswith(label + " ")), None)
        if label is None:
            if current not in MULTILINE_LABELS:
                raise ValueError(f"Chunk 正文在 {current!r} 之后有渲染规则不认识的行；模板变了要同步改渲染规则")
            fields[current] += "\n" + line
        elif label in REPEATED_LABELS and label in fields:
            fields[label] += "\n" + line[len(label) + 1:].lstrip()
        else:
            current = label
            fields[label] = line[len(label) + 1:].lstrip()
    return fields


def _indented(label: str, value: str, indent: str) -> list[str]:
    """一个可能多行的字段：首行带标签，续行同样缩进，内容不改。"""
    first, *rest = value.split("\n")
    return [f"{indent}{label}: {first}", *(indent + line for line in rest)]


def _restates(display_name: str, column_name: str) -> bool:
    """展示名是否只是列名换个写法：去掉空格和下划线、忽略大小写后相同（如 atom_id2 与 "atom id 2"）。"""
    def bare(name: str) -> str:
        return name.replace(" ", "").replace("_", "").lower()
    return bare(display_name) == bare(column_name)


def _table_lines(table: TableContext, structure_format: StructureFormat, table_ddl: dict[str, str] | None) -> list[str]:
    """一张表的全部行：表头、表用途、记录粒度、数据约定（有时）、结构，然后是命中的列和关系。"""
    fields = _fields(table.table_chunk, TABLE_LABELS)
    lines = [f'表 "{table.table}"', f"  表用途: {fields['表用途']}", f"  记录粒度: {fields['记录粒度']}"]
    if "数据约定" in fields:
        lines.append(f"  数据约定: {fields['数据约定']}")
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
        if column["展示名"] != UNKNOWN and not _restates(column["展示名"], hit.column_name):
            lines += _indented("展示名", column["展示名"], "    ")
        if column["取值解释"] != UNKNOWN:
            lines += _indented("取值解释", column["取值解释"], "    ")
        if "取值" in column:
            lines.append(f"    取值: {column['取值']}")
    for hit in table.relationships:
        relationship = _fields(hit.content, RELATIONSHIP_LABELS)
        pairs = relationship["同一条外键的有序字段对"]
        # 语义摘要覆盖这条外键时有关联语义；否则模板改写每个字段对的端点含义。
        if "关联语义" in relationship:
            lines.append(f"  关系 {pairs}: {relationship['关联语义']}")
        else:
            lines.append(f"  关系 {pairs}")
            lines += [f"    端点含义: {meaning}" for meaning in relationship["端点含义"].split("\n")]
        if "复合外键" in relationship:
            lines.append(f"    复合外键: {relationship['复合外键']}")
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
        每张表一段、段间空一行的文本。表有数据约定时接在记录粒度后；命中的列有取值样例时接在说明后。关系保留字段对、关联语义（没有时为每个字段对的端点含义）、复合外键
        提醒和角色区别。只放有用的结构、知识和线索：去掉数据库名、"main". 前缀、
        声明标志、默认值、值为"来源未说明"的字段、只是列名换个写法的展示名、引用方向、候选 JOIN 和
        ON UPDATE / DELETE / MATCH。
    异常:
        ValueError: Chunk 正文里有渲染规则不认识的行（Corpus 模板变了）。
    """
    sections = ["\n".join(_table_lines(table, structure_format, table_ddl)) for table in context]
    return "\n\n".join(sections).replace('"main".', "")
