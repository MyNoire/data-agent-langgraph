"""取值样例：只读打开业务 SQLite，按结构逐列取出真实取值，作为构建 Corpus 的输入。"""

from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
from typing import Literal

from pydantic import Field

from .models import Catalog, CatalogModel, TableSchema
from ._sqlite import extract_catalog

VALUE_RULE_VERSION = "value-samples-v1"
"""取值规则的版本：上限、样例个数、键列和截断的规则任何一处改变都要升版本。"""
ALL_VALUES_LIMIT = 20
"""文本列去重后非空取值不超过这个数时全部列出。"""
SAMPLE_COUNT = 3
"""其他列给出现次数最多的几个值。"""
MAX_VALUE_CHARS = 80
"""单个文本值超过这个长度时截断。"""


class SampleValue(CatalogModel):
    """一个取值；文本超长时只保留前 MAX_VALUE_CHARS 个字符并标记。"""

    value: str | int | float
    truncated: bool = False


class ColumnValues(CatalogModel):
    """一列的取值。mode：all 为全部取值，samples 为出现最多的几个，private 为隐私列不取值。"""

    table_name: str = Field(min_length=1)
    column_name: str = Field(min_length=1)
    mode: Literal["all", "samples", "private"]
    distinct_count: int | None = Field(default=None, ge=0)
    values: list[SampleValue] = Field(default_factory=list)


class ValueSamples(CatalogModel):
    """一个库全部列的取值样例；columns 按结构里表和列的顺序排列。"""

    format_version: Literal["value-samples-v1"] = "value-samples-v1"
    rule_version: Literal["value-samples-v1"] = VALUE_RULE_VERSION
    database_id: str = Field(min_length=1)
    schema_name: str = "main"
    source_sqlite_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    columns: list[ColumnValues]


def _text_affinity(declared_type: str) -> bool:
    """SQLite 的文本亲和规则：声明类型含 CHAR、CLOB 或 TEXT。"""
    upper = declared_type.upper()
    return any(word in upper for word in ("CHAR", "CLOB", "TEXT"))


def _key_columns(table: TableSchema) -> set[str]:
    """主键列和外键源列。"""
    return set(table.primary_key_columns()) | {name for fk in table.foreign_keys for name in fk.source_columns}


def _sample(value: str | int | float) -> SampleValue:
    if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
        return SampleValue(value=value[:MAX_VALUE_CHARS], truncated=True)
    return SampleValue(value=value)


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _column_values(connection: sqlite3.Connection, table: TableSchema, column: str, *,
                   declared_type: str, key: bool) -> ColumnValues:
    """按规则取一列的值。"""
    source = f"FROM {_quoted(table.name)} WHERE {_quoted(column)} IS NOT NULL"
    distinct = connection.execute(f"SELECT COUNT(DISTINCT {_quoted(column)}) {source}").fetchone()[0]
    if _text_affinity(declared_type) and not key and distinct <= ALL_VALUES_LIMIT:
        rows = connection.execute(f"SELECT DISTINCT {_quoted(column)} {source} ORDER BY 1").fetchall()
        mode = "all"
    else:
        rows = connection.execute(
            f"SELECT {_quoted(column)} {source} GROUP BY 1 ORDER BY COUNT(*) DESC, 1 LIMIT ?", (SAMPLE_COUNT,),
        ).fetchall()
        mode = "samples"
    return ColumnValues(table_name=table.name, column_name=column, mode=mode, distinct_count=distinct,
                        values=[_sample(row[0]) for row in rows])


def collect_value_samples(database_id: str, path: Path, *, private_columns: set[tuple[str, str]]) -> ValueSamples:
    """只读打开一个 SQLite 文件，按规则 value-samples-v1 取出每一列的取值。

    参数:
        database_id: 库标识，写进结果。
        path: SQLite 文件。
        private_columns: 隐私列 (表, 列)；这些列不执行任何取值查询，结果里只记 mode=private。
    返回:
        ValueSamples，列的顺序与结构一致，记录来源文件的 sha256。
    异常:
        ValueError: private_columns 里有结构中不存在的列。其余同 extract_catalog。
    副作用:
        只读打开并关闭连接，不写业务库。
    """
    catalog: Catalog = extract_catalog(database_id, path)
    known = {(table.name, column.name) for table in catalog.tables for column in table.columns}
    unknown = private_columns - known
    if unknown:
        raise ValueError(f"隐私列不在结构中：{sorted(unknown)}")
    resolved = path.resolve(strict=True)
    columns = []
    with closing(sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        for table in catalog.tables:
            keys = _key_columns(table)
            for column in table.columns:
                if (table.name, column.name) in private_columns:
                    columns.append(ColumnValues(table_name=table.name, column_name=column.name, mode="private"))
                else:
                    columns.append(_column_values(connection, table, column.name,
                                                  declared_type=column.declared_type, key=column.name in keys))
        connection.commit()
    return ValueSamples(database_id=database_id, schema_name=catalog.schema_name,
                        source_sqlite_sha256=sha256(resolved.read_bytes()).hexdigest(), columns=columns)
