"""结构目录的数据契约；不包含业务行、模型解释或运行凭据。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

class CatalogModel(BaseModel):
    """目录对象的公共配置：拒绝契约之外的字段。"""
    model_config = ConfigDict(extra="forbid")

class ColumnSchema(CatalogModel):
    """输入为一个字段的结构属性；输出可序列化的字段对象。"""
    name: str = Field(min_length=1)
    ordinal: int = Field(ge=0)
    declared_type: str
    not_null_flag: bool
    default_sql: str | None
    pk_position: int = Field(ge=0)
    hidden: Literal[0, 1, 2, 3]

class ForeignKeySchema(CatalogModel):
    """输入为一条声明外键；输出保持字段配对顺序的关系对象。"""

    foreign_key_id: int = Field(ge=0)
    source_columns: list[str] = Field(min_length=1)
    target_table: str = Field(min_length=1)
    declared_target_columns: list[str | None] = Field(min_length=1)
    target_columns: list[str] = Field(min_length=1)
    on_update: str
    on_delete: str
    match: str

class TableSchema(CatalogModel):
    """输入为表名、列和关系；输出一张表的目录对象。"""

    name: str = Field(min_length=1)
    without_rowid: bool
    strict: bool
    columns: list[ColumnSchema] = Field(min_length=1)
    foreign_keys: list[ForeignKeySchema] = Field(default_factory=list)

    def primary_key_columns(self) -> list[str]:
        """按主键成员顺序获取字段名。

        参数:
            无显式参数；读取当前表的 columns。
        返回:
            按 pk_position 升序排列的字段名；无主键返回空列表。
        """
        members = [column for column in self.columns if column.pk_position > 0]
        return [column.name for column in sorted(members, key=lambda item: item.pk_position)]

class CatalogSnapshot(CatalogModel):
    """输入为库标识和表目录；输出本节使用的结构快照。"""

    format_version: Literal["sqlite-catalog-v1"] = "sqlite-catalog-v1"
    database_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    dialect: Literal["sqlite"] = "sqlite"
    schema_name: Literal["main"] = "main"
    tables: list[TableSchema]