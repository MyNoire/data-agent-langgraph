"""从 SQLite 的 main 命名空间只读提取普通表结构。"""

from collections import defaultdict
from contextlib import closing
from pathlib import Path
import sqlite3

from typing_inspection.typing_objects import is_final

from .models import (
    Catalog, ColumnSchema, ForeignKeySchema, TableSchema,
)

class CatalogError(ValueError):
    """结构无法被当前目录契约可靠表达。"""

_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")

def _canonical_name(name: str, candidates: list[str]) -> str:
    """将声明中的名称绑定到实际对象名。

    参数:
        name: 外键声明中的表名或列名。
        candidates: 同一作用域的真实对象名称列表。
    返回:
        唯一匹配对象的原始名称；仅忽略 ASCII 大小写。
    异常:
        CatalogError: 对象不存在或无法唯一匹配。
    """
    matches = [item for item in candidates if item.translate(_ASCII_LOWER) == name.translate(_ASCII_LOWER)]
    if len(matches) != 1:
        raise CatalogError(f"无法唯一绑定结构对象：{name!r}")
    return matches[0]

def _read_columns(connection: sqlite3.Connection, table_name: str) -> list[ColumnSchema]:
    """读取一张表的字段结构，不查询业务行。

    参数:
        connection: 已开启读事务、row_factory 为 sqlite3.Row 的连接。
        table_name: main 内实际存在的表名。
    返回:
        按 cid 排序的字段对象，包含生成列和原始约束标志。
    异常:
        sqlite3.DatabaseError: 系统目录不可读。
    """
    rows = connection.execute(
        "SELECT * FROM pragma_table_xinfo(?, ?) ORDER BY cid", (table_name, "main")
    ).fetchall()
    return [
        ColumnSchema(
            name=row["name"], ordinal=row["cid"], declared_type=row["type"],
            not_null_flag=bool(row["notnull"]), default_sql=row["dflt_value"],
            pk_position=row["pk"], hidden=(row["hidden"]),
        )
        for row in rows
    ]


def _read_foreign_keys(
    connection: sqlite3.Connection,
    table: TableSchema,
    tables_by_name: dict[str, TableSchema],
) -> list[ForeignKeySchema]:
    """合并外键记录，并把关系两端绑定到当前语料。

    参数:
        connection: 已开启读事务、返回 sqlite3.Row 的连接。
        table: 当前来源表，其字段已经读取。
        tables_by_name: main 内所有普通表的目录，供目标解析使用。
    返回:
        按表内外键 id 排序的关系；同一关系内部按 seq 对齐字段。
    异常:
        CatalogError: 引用缺失、混合省略目标列或字段数量不匹配。
        sqlite3.DatabaseError: 读取系统目录失败。
    """
    rows = connection.execute(
        "SELECT * FROM pragma_foreign_key_list(?, ?) ORDER BY id, seq",
        (table.name, "main"),
    ).fetchall()
    groups: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        groups[row["id"]].append(row)

    relations = []
    for foreign_key_id, members in sorted(groups.items()):
        members.sort(key=lambda row: row["seq"])
        first = members[0]
        target_name = _canonical_name(first["table"], list(tables_by_name))
        target = tables_by_name[target_name]
        source_columns = [
            _canonical_name(row["from"], [column.name for column in table.columns])
            for row in members
        ]
        declared = [row["to"] for row in members]
        if all(name is None for name in declared):
            target_columns = target.primary_key_columns()
        elif any(name is None for name in declared):
            raise CatalogError(f"目标字段只有部分省略：{table.name}/{foreign_key_id}")
        else:
            target_columns = [
                _canonical_name(name, [column.name for column in target.columns])
                for name in declared
            ]
        if len(source_columns) != len(target_columns):
            raise CatalogError(f"外键字段数量不匹配：{table.name}/{foreign_key_id}")
        relations.append(ForeignKeySchema(
            foreign_key_id=foreign_key_id,
            source_columns=source_columns,
            target_table=target_name,
            declared_target_columns=declared,
            target_columns=target_columns,
            on_update=first["on_update"], on_delete=first["on_delete"],
            match=first["match"],
        ))
    return relations

def extract_catalog(database_id: str, path: Path) -> Catalog:
    """提取一个 SQLite 文件的结构目录。

    参数:
        database_id: 已登记的库标识；命令行入口通过 DatabaseRegistry 获取路径。
        path: 已确认来源的 SQLite 文件路径；本函数本身不负责授权。
    返回:
        main 普通表的语料，不包含绝对路径、时间戳、业务行或业务释义。
    异常:
        FileNotFoundError: 输入文件不存在。
        CatalogError: SQLite 版本不足、存在不支持对象或外键无法解析。
        sqlite3.DatabaseError: 文件无效或读取失败。
        pydantic.ValidationError: 结构属性不满足目录契约。
    副作用:
        打开并关闭一个只读连接，不对业务数据库执行写操作。
    """
    if sqlite3.sqlite_version_info < (3, 37, 0):
        raise CatalogError("本节需要 SQLite 3.37 或更新版本")
    resloved = path.resolve(strict=True)
    with closing(sqlite3.connect(resloved.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        objects = connection.execute(
            "SELECT name, type, wr, strict FROM pragma_table_list "
            "WHERE schema = ? AND name NOT GLOB ? ORDER BY name",
            ("main", "sqlite_*"),
        ).fetchall()
        unsupported = [row["name"] for row in objects if row["type"] != "table"]
        if unsupported:
            raise CatalogError(f"本节仅支持普通表，发现视图或虚拟/影子表：{unsupported}")
        tables = [
            TableSchema(
                name=row["name"],without_rowid=bool(row["wr"]), strict=bool(row["strict"]),
                columns=_read_columns(connection, row["name"]),
            )
            for row in objects
        ]
        table_by_name = {table.name: table for table in tables}
        for table in tables:
            table.foreign_keys = _read_foreign_keys(connection, table, table_by_name)
        connection.commit()
    return Catalog(database_id=database_id, tables=tables)