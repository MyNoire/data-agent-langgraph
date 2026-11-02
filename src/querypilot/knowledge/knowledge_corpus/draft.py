"""R1.3 的中间契约；尚不是 PostgreSQL 的最终行结构。"""

from typing import Any, Literal

from pydantic import Field

from querypilot.knowledge.knowledge_catalog.api import CatalogModel

class DocumentDraft(CatalogModel):
    """输入为一张表的标准化内容与来源；输出一份完整知识文档。"""

    document_key: str
    database_id: str
    dialect: Literal["sqlite"] = "sqlite"
    source_kind: Literal["derived_catalog"] = "derived_catalog"
    title: str
    content: str
    metadata: dict[str, Any]

class ChunkDraft(CatalogModel):
    """输入为单个知识对象；输出可供后续独立召回的正文和定位。"""

    chunk_key: str
    document_key: str
    database_id: str
    knowledge_type: Literal["table", "column", "relationship"]
    schema_name: str | None = None
    table_name: str | None = None
    column_name: str | None = None
    relation_key: str | None = None
    title: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)

class RelationDraft(CatalogModel):
    """输入为一条完整声明外键；输出有序两端与来源文档引用。"""

    relation_key: str
    source_document_key: str
    database_id: str
    source_schema: str
    source_table: str
    source_columns: list[str] = Field(min_length=1)
    target_schema: str
    target_table: str
    target_columns: list[str] = Field(min_length=1)
    relation_origin: Literal["declared_fk"] = "declared_fk"
    provenance: dict[str, Any]
    metadata: dict[str, Any]

class KnowledgeDraft(CatalogModel):
    """输入为本次渲染的三类对象；输出一个未发布的知识草稿集合。"""

    format_version: Literal["knowledge-render-draft-v1"] = "knowledge-render-draft-v1"
    status: Literal["draft"] = "draft"
    documents: list[DocumentDraft]
    chunks: list[ChunkDraft]
    relations: list[RelationDraft]


class TableMeaning(CatalogModel):
    """输入为离线推断的表摘要；输出用途和记录粒度，不复制字段详情。"""

    purpose: str = Field(min_length=1)
    row_grain: str = Field(min_length=1)


class RelationshipMeaning(CatalogModel):
    """输入为关系摘要及完整端点身份；输出可与真实声明 FK 绑定的说明。"""

    source_table: str
    source_columns: list[str] = Field(min_length=1)
    target_table: str
    target_columns: list[str] = Field(min_length=1)
    meaning: str = Field(min_length=1)


class SemanticProfile(CatalogModel):
    """输入为可复用的推断配置；输出带版本、性质和数据库范围的摘要集合。"""

    format_version: Literal["knowledge-semantics-v1"] = "knowledge-semantics-v1"
    database_id: str
    schema_name: str = "main"
    version: str = Field(min_length=1)
    origin: Literal["inferred"] = "inferred"
    author: str = Field(min_length=1)
    tables: dict[str, TableMeaning]
    relationships: list[RelationshipMeaning] = Field(default_factory=list)
