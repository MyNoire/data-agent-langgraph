"""根据实际 CSV 字节核对来源和表列绑定，不读取路径或公开单步匹配器。"""

from pathlib import Path
from typing import Literal
import csv

from .models import CatalogModel
from .description_models import EnrichedCatalog
from ._descriptions import _match_name, read_description_bytes


class SourceVerification(CatalogModel):
    """输入：完整核验后的统计；返回：不进入语料格式的安全结果。"""

    database_id: str
    status: Literal["verified"] = "verified"
    source_count: int
    record_count: int


class SourceVerificationError(ValueError):
    """来源内容不一致；保留原构建错误类别和逻辑定位供调用方映射。"""

    def __init__(self, error_code: str, message: str, *, logical_path: str | None = None):
        """输入：受控类别、消息和定位；返回：异常；无文件副作用。"""
        super().__init__(message)
        self.error_code = error_code
        self.logical_path = logical_path


def _require(condition: bool, code: str, message: str, *, path: str | None = None) -> None:
    """输入：核验条件；返回：无；不成立抛安全错误，无 I/O。"""
    if not condition:
        raise SourceVerificationError(code, message, logical_path=path)


def _logical_path(relative: str) -> str:
    """输入：来源逻辑名；返回：原名；拒绝越界语法，无 I/O。"""
    _require(isinstance(relative, str) and bool(relative) and "\\" not in relative
             and ":" not in relative and all(part not in {"", ".", ".."} for part in relative.split("/")),
             "source_scope_violation", "逻辑路径不符合规范")
    return relative


def _verify_sources(enriched: EnrichedCatalog, source_bytes: dict[str, bytes]) -> SourceVerification:
    """核对来源记录与提供的派生 CSV，而不只比较声明的 hash。

    参数: source_bytes 为来源文件名到实际字节，enriched 为整理后目录中的说明目录。
    返回: 完整核对统计；缺失说明允许，但已有说明必须可追溯到真实记录。
    异常: SourceVerificationError/ValueError/csv.Error 表示记录、位置或释义不一致。
    副作用: 无文件读写，不编造释义。
    """
    files = {"inputs/bundle/derived_source/" + name: raw for name, raw in source_bytes.items()}
    records_by_file = {}
    headers_by_file = {}
    for source in enriched.sources:
        path = "inputs/bundle/derived_source/" + _logical_path(source.source_file)
        _require(path in files, "input_invalid", "缺少声明的派生 CSV", path=path)
        actual_source, records = read_description_bytes(files[path], source.source_file)
        _require(actual_source == source, "input_invalid", "CSV 文件来源信息与实际字节不一致", path=path)
        records_by_file[source.source_file] = {record.record_index: record for record in records}
        headers_by_file[source.source_file] = [header.strip() for header in source.headers_raw]
    fields = {"display_name": "column_name", "description": "column_description",
              "source_data_format": "data_format", "value_description": "value_description"}
    for note in enriched.descriptions:
        actual = records_by_file.get(note.source.source_file, {}).get(note.source.record_index)
        _require(actual is not None and actual == note.source,
                 "input_invalid", "来源记录、行号或原单元格与 CSV 不一致")
        headers = headers_by_file[note.source.source_file]
        _require(len(actual.raw_values) == len(headers), "input_invalid", "CSV 记录列数不一致")
        values = dict(zip(headers, actual.raw_values))
        table = next((table for table in enriched.catalog.tables
                      if table.name == _match_name(Path(note.source.source_file).stem,
                                                  [item.name for item in enriched.catalog.tables])), None)
        _require(table is not None and table.name == note.table_name
                 and _match_name(values["original_column_name"], [column.name for column in table.columns])
                 == note.column_name, "input_invalid", "CSV 记录绑定到错误表或字段")
        _require(all(getattr(note, field) == (values[header].strip() or None)
                     for field, header in fields.items()),
                 "input_invalid", "字段说明与派生 CSV 单元格不一致")


    return SourceVerification(database_id=enriched.catalog.database_id, source_count=len(enriched.sources),
                              record_count=sum(len(records) for records in records_by_file.values()))


def verify_sources(enriched: EnrichedCatalog, source_bytes: dict[str, bytes]) -> SourceVerification:
    """核验实际来源，而非只信任目录中声明的摘要。

    参数：enriched 为严格增强目录，source_bytes 为文件名到已固定原字节的映射。
    返回：字节、记录位置、原始单元格和字段绑定全部一致的 SourceVerification。
    异常：SourceVerificationError，保留解析错误cause，不输出不可信原始内容。
    副作用：无文件读写，无数据库或模型调用。
    """
    try:
        return _verify_sources(enriched, source_bytes)
    except SourceVerificationError:
        raise
    except (ValueError, KeyError, TypeError, csv.Error) as exc:
        raise SourceVerificationError("input_invalid", "派生来源记录无法完成核验") from exc
