"""字段说明、来源和合并结果的契约；保持结构目录本身不变。"""

from typing import Literal

from pydantic import Field

from .models import CatalogModel, Catalog

class DescriptionSource(CatalogModel):
    """输入为 CSV 文件信息；输出一份可定位的文件来源记录。"""

    source_file: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    encoding: Literal["utf-8", "utf-8-sig"]
    headers_raw: list[str]

class SourceRecord(CatalogModel):
    """输入为一条 CSV 记录及位置；输出保留原文的来源定位。"""

    source_file: str
    record_index: int = Field(ge=1)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    raw_values: list[str]
    raw_record: str

class ColumnDescription(CatalogModel):
    """输入为真实字段身份和来源说明；输出绑定后的知识记录。"""

    table_name: str
    column_name: str
    display_name: str | None
    description: str | None
    source_data_format: str | None
    value_description: str | None
    review_status: Literal["source_extracted"] = "source_extracted"
    source: SourceRecord

class ImportWarning(CatalogModel):
    """输入为问题与可选原始记录；输出可复核的导入警告。"""

    code: Literal[
        "row_width", "unknown_column", "name_normalized",
        "suspicious_text", "empty_meaning", "unbound_column",
    ]
    table_name: str
    column_name: str | None = None
    detail: str
    source: SourceRecord | None = None

class EnrichedCatalog(CatalogModel):
    """输入为结构、来源和说明；输出待复核的增强目录。"""

    format_version: Literal["enriched-catalog-v1"] = "enriched-catalog-v1"
    status: Literal["draft"] = "draft"
    catalog: Catalog
    sources: list[DescriptionSource]
    descriptions: list[ColumnDescription]

class DescriptionImportResult(CatalogModel):
    """输入为增强目录及警告；输出合并函数的一次完整结果。"""

    enriched_catalog: EnrichedCatalog
    warnings: list[ImportWarning]