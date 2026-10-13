"""读取一个 BIRD 数据库的说明 CSV，绑定到已有结构快照。"""

import csv
import hashlib
import io
from pathlib import Path

from querypilot.catalog.description_models import (
    ColumnDescription, DescriptionImportResult, DescriptionSource,
    EnrichedCatalog, ImportWarning, SourceRecord,
)
from querypilot.catalog.models import CatalogSnapshot

class DescriptionError(ValueError):
    """来源格式或绑定关系无法安全确定，终止本次说明构建。"""

_HEADERS = {
    "original_column_name", "column_name", "column_description",
    "data_format", "value_description",
}
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")

def _match_name(raw_name: str, candidates: list[str]) -> str | None:
    """把 CSV 来源名绑定到唯一真实名字。

    参数:
        raw_name: 来源中的名称，可能含首尾空白或大小写差异。
        candidates: 当前作用域中的真实表名或字段名。
    返回:
        精确匹配优先；否则清理来源后匹配；未找到返回 None。
    异常:
        DescriptionError: 规范化后匹配不唯一。
    """
    if raw_name in candidates:
        return raw_name
    key = raw_name.strip().translate(_ASCII_LOWER)
    matches = [name for name in candidates if name.translate(_ASCII_LOWER) == key]
    if len(matches) > 1:
        raise DescriptionError(f"来源名称无法唯一绑定：{raw_name!r}")
    return matches[0] if matches else None

def _optional_text(value: str) -> str | None:
    """清理用于读取的文本，同时由来源对象另存原文。

    参数:
        value: CSV 单元格原始文本。
    返回:
        去除首尾空白后的字符串；空白内容返回 None。
    """
    return value.strip() or None

def _read_csv(path: Path, source_file: str) -> tuple[DescriptionSource, list[SourceRecord]]:
    """读取文件级信息及原始 CSV 记录，不做字段绑定。

    参数:
        path: 明确选定目录中的一个 CSV 文件。
        source_file: 无机器绝对路径的逻辑来源标识。
    返回:
        文件来源对象和按原顺序排列的记录；只跳过无单元格的空行。
    异常:
        DescriptionError: 非 UTF-8/BOM 编码或表头不符合当前契约。
        csv.Error: 引号等 CSV 格式错误。
        OSError: 文件不可读。
    """
    raw = path.read_bytes()
    return read_description_bytes(raw, source_file)


def read_description_bytes(raw: bytes, source_file: str) -> tuple[DescriptionSource, list[SourceRecord]]:
    """解析固定 CSV 原字节，供文件导入和快照核验共用。

    参数:
        raw: 已固定的 UTF-8 或 UTF-8-sig 字节。
        source_file: 逻辑来源位置。
    返回:
        文件级来源及带物理行号、原记录和单元格的记录列表。
    异常:
        DescriptionError / csv.Error: 编码、表头或 CSV 格式不合法。
    副作用:
        无文件读写；规则与旧 _read_csv 相同。
    """
    encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError as exc:
        raise DescriptionError(f"需要显式处理来源编码：{source_file}") from exc
    physical_lines = io.StringIO(text, newline="").readlines()
    reader = csv.reader(physical_lines, strict=True)
    headers_raw = next(reader, [])
    headers = [header.strip() for header in headers_raw]
    if len(headers) != len(_HEADERS) or set(headers) != _HEADERS:
        raise DescriptionError(f"缺失、重复或额外的表头：{source_file}")
    source = DescriptionSource(
        source_file=source_file, source_sha256=hashlib.sha256(raw).hexdigest(),
        encoding=encoding, headers_raw=headers_raw,
    )
    records = []
    while True:
        line_start = reader.line_num + 1
        values = next(reader, None)
        if values is None:
            break
        if not values:
            continue
        records.append(SourceRecord(
            source_file=source_file, record_index=len(records) + 1,
            line_start=line_start, line_end=reader.line_num,
            raw_values=values,
            raw_record="".join(physical_lines[line_start - 1:reader.line_num]),
        ))
    return source, records

def enrich_catalog(catalog: CatalogSnapshot, description_dir: Path) -> DescriptionImportResult:
    """为现有快照绑定一个库的字段说明，不改动快照。

    参数:
        catalog: R1.1 导出的结构快照，作为真实对象范围。
        description_dir: BIRD 的 <database_id>/database_description 目录。
    返回:
        draft 增强目录和警告；无效记录只进入警告，不增加真实字段。
    异常:
        DescriptionError: 来源范围不符、重复绑定或文件格式不支持。
        OSError、csv.Error: 文件或 CSV 读取失败。
    副作用:
        只读取选定目录的 CSV；不改 SQLite、输入 catalog 或来源文件。
    """
    root = description_dir.resolve(strict=True)
    if not root.is_dir() or root.name != "database_description" or root.parent.name != catalog.database_id:
        raise DescriptionError("说明目录必须匹配 <database_id>/database_description")
    paths = sorted(root.glob("*.csv"), key=lambda path: path.name)
    if not paths:
        raise DescriptionError("所选目录没有 CSV 文件")
    tables = {table.name: table for table in catalog.tables}
    sources, descriptions, warnings = [], [], []
    seen_tables: set[str] = set()
    bound: set[tuple[str, str]] = set()
    for path in paths:
        if path.resolve(strict=True).parent != root or not path.is_file():
            raise DescriptionError("CSV 路径必须位于选定的说明目录")
        table_name = _match_name(path.stem, list(tables))
        if table_name is None or table_name in seen_tables:
            raise DescriptionError(f"未知表或重复的说明文件：{path.name}")
        seen_tables.add(table_name)
        source_file = f"{catalog.database_id}/database_description/{path.name}"
        source, records = _read_csv(path, source_file)
        sources.append(source)
        headers = [header.strip() for header in source.headers_raw]
        column_names = [column.name for column in tables[table_name].columns]
        for record in records:
            if len(record.raw_values) != len(headers):
                warnings.append(ImportWarning(
                    code="row_width", table_name=table_name,
                    detail="记录的单元格数量与表头不一致；已隔离", source=record,
                ))
                continue
            values = dict(zip(headers, record.raw_values))
            column_name = _match_name(values["original_column_name"], column_names)
            if column_name is None:
                warnings.append(ImportWarning(
                    code="unknown_column", table_name=table_name,
                    detail="来源字段在结构目录中不存在；已隔离", source=record,
                ))
                continue
            key = (table_name, column_name)
            if key in bound:
                raise DescriptionError(f"同一字段出现多条说明，拒绝覆盖：{table_name}.{column_name}")
            bound.add(key)
            description = ColumnDescription(
                table_name=table_name, column_name=column_name,
                display_name=_optional_text(values["column_name"]),
                description=_optional_text(values["column_description"]),
                source_data_format=_optional_text(values["data_format"]),
                value_description=_optional_text(values["value_description"]), source=record,
            )
            descriptions.append(description)
            if values["original_column_name"] != column_name:
                warnings.append(ImportWarning(
                    code="name_normalized", table_name=table_name, column_name=column_name,
                    detail="来源名经首尾空白/ASCII 大小写处理后匹配；实际名字未修改", source=record,
                ))
            suspicious = sorted({
                f"U+{ord(char):04X}" for value in record.raw_values for char in value
                if 0x80 <= ord(char) <= 0x9F or char == "\ufffd"
            })
            if suspicious:
                warnings.append(ImportWarning(
                    code="suspicious_text", table_name=table_name, column_name=column_name,
                    detail="发现需复核的文本字符，保留原文：" + ", ".join(suspicious), source=record,
                ))
            if description.description is None and description.value_description is None:
                warnings.append(ImportWarning(
                    code="empty_meaning", table_name=table_name, column_name=column_name,
                    detail="字段绑定成功，但来源没有字段含义或取值解释", source=record,
                ))
    for table in catalog.tables:
        for column in table.columns:
            if (table.name, column.name) not in bound:
                warnings.append(ImportWarning(
                    code="unbound_column", table_name=table.name, column_name=column.name,
                    detail="没有有效说明绑定到此字段；真实结构仍保留",
                ))
    descriptions.sort(key=lambda item: (item.table_name, item.column_name))
    return DescriptionImportResult(
        enriched_catalog=EnrichedCatalog(catalog=catalog, sources=sources, descriptions=descriptions),
        warnings=warnings,
    )
