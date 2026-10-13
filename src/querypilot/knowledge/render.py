"""从已批准的描述修正包，渲染离线知识草稿。"""

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from querypilot.catalog.description_models import ColumnDescription, EnrichedCatalog, SourceRecord
from querypilot.catalog.models import CatalogSnapshot, ForeignKeySchema, TableSchema
from querypilot.knowledge.models import (
    ChunkDraft, DocumentDraft, KnowledgeDraft, RelationDraft, SemanticProfile,
)

class KnowledgeError(ValueError):
    """知识输入不符合当前渲染约定时抛出的异常。"""

@dataclass(frozen=True)
class _Bundle:
    """输入为已核对的包内容；输出供渲染函数使用的只读属性集合。"""
    enriched: EnrichedCatalog
    manifest: dict[str, Any]
    report: dict[str, Any]
    manifest_sha256: str

def _require(condition: bool, message: str) -> None:
    """检查输入约定。

    参数:
        condition: 必须满足的条件。
        message: 条件不满足时的错误说明。
    返回:
        None；失败时抛出 KnowledgeError。
    """
    if not condition:
        raise KnowledgeError(message)

def _key(*parts: str | int) -> str:
    """生成没有分隔符歧义的对象 key。

    参数:
        parts: 有序身份成员；保留名称原始大小写。
    返回:
        紧凑 JSON 数组字符串；不是数据库 UUID。
    """
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))

def _qualified(*parts: str) -> str:
    """为正文呈现明确的 SQLite 对象名称。

    参数:
        parts: schema、表或列的有序名称。
    返回:
        双引号引用并转义后的名称；只渲染文本，不执行 SQL。
    """
    return ".".join('"' + part.replace('"', '""') + '"' for part in parts)


def _field_meaning(note: ColumnDescription | None) -> str:
    """提取字段的简短语义，不复制详细取值说明或猜测缺失含义。

    参数:
        note: 已绑定到真实字段的来源说明；没有记录时为 None。
    返回:
        展示名及原始描述组成的文本；缺失部分明确标记为来源未说明。
    """
    parts = []
    if note and note.display_name:
        parts.append(f"展示名: {note.display_name}")
    parts.append(f"含义: {note.description if note and note.description else '来源未说明'}")
    return "; ".join(parts)


def _semantic_sources(
    identities: list[tuple[str, str]],
    notes: dict[tuple[str, str], ColumnDescription],
    source_hashes: dict[str, str],
    database_id: str,
    schema_name: str,
) -> list[dict[str, Any]]:
    """为摘要引用的字段释义生成精简的来源定位。

    参数:
        identities: 表名与列名的有序身份列表；重复项只保留首次出现。
        notes: 当前修正包中的字段说明索引。
        source_hashes: 派生 CSV 文件名到已验证 SHA-256 的映射。
        database_id: 当前数据库标识。
        schema_name: 当前 schema 名称。
    返回:
        指向所属 document 的字段 lineage 及 CSV 文件、hash、行号的列表。
        没有展示名和描述的字段不产生语义来源引用。
    异常:
        KeyError: 已绑定说明缺少文件 hash；正常输入已由 _load_bundle 拒绝。
    """
    sources = []
    for table_name, column_name in dict.fromkeys(identities):
        note = notes.get((table_name, column_name))
        if note is None or not (note.display_name or note.description):
            continue
        fields = [name for name in ("display_name", "description") if getattr(note, name)]
        sources.append({
            "document_key": _key("table_document", database_id, schema_name, table_name),
            "table_name": table_name, "column_name": column_name, "fields": fields,
            "source_file": note.source.source_file,
            "source_sha256": source_hashes[note.source.source_file],
            "line_start": note.source.line_start, "line_end": note.source.line_end,
            "review_status": note.review_status,
        })
    return sources


def _relationship_content(
    table: TableSchema,
    fk: ForeignKeySchema,
    database_id: str,
    schema_name: str,
    notes: dict[tuple[str, str], ColumnDescription],
    meaning: str | None = None,
) -> str:
    """根据声明外键和两端释义渲染可独立阅读的关系正文。

    参数:
        table: 声明外键的源表，含其他外键以识别重复目标角色。
        fk: 当前完整外键；两端成员按声明顺序配对。
        database_id: 当前数据库标识。
        schema_name: 当前 schema 名称。
        notes: 表列身份到来源说明的索引；缺失描述时显式标记。
        meaning: 可选的离线推断摘要；存在时替代重复的端点字段释义。
    返回:
        含引用方向、端点语义、候选 ON 条件、角色区别及限制的文本。
    异常:
        ValueError: 两端字段数量不同；正常输入已由 _load_bundle 拒绝。
    副作用:
        无；JOIN 条件仅作为知识文本，不选择连接类型或执行 SQL。
    """
    source_table = _qualified(schema_name, table.name)
    target_table = _qualified(schema_name, fk.target_table)
    pairs = list(zip(fk.source_columns, fk.target_columns, strict=True))
    lines = [
        f"数据库: {database_id}; 声明外键 {source_table} / {fk.foreign_key_id}",
        f"引用方向: 源表 {source_table} → 目标表 {target_table}。",
        "同一条外键的有序字段对: " + "; ".join(
            f"{_qualified(schema_name, table.name, left)} → "
            f"{_qualified(schema_name, fk.target_table, right)}" for left, right in pairs
        ),
    ]
    if meaning:
        lines.append(f"关联语义: {meaning}")
    else:
        for left, right in pairs:
            lines.append(f"端点含义: {_qualified(left)}（{_field_meaning(notes.get((table.name, left)))}）"
                         f" → {_qualified(right)}（{_field_meaning(notes.get((fk.target_table, right)))}）")
    lines.append("候选 JOIN（source 为源表别名，target 为目标表别名；仅展示 ON 条件）: "
                 + " AND ".join(f"{_qualified('source', left)} = {_qualified('target', right)}"
                                for left, right in pairs))
    if len(pairs) > 1:
        lines.append("复合外键: 上述字段对共同构成一条关系，JOIN 必须保留全部 AND 条件。")
    same_target = [other for other in table.foreign_keys if other.target_table == fk.target_table]
    if len(same_target) > 1:
        lines.append(f"角色区别: 源表有 {len(same_target)} 条外键指向 {target_table}，源端角色分别为:")
        for other in same_target:
            roles = ", ".join(_qualified(name) for name in other.source_columns)
            lines.append(f"- 外键 {other.foreign_key_id}: {roles}")
        lines.append("同时引用这些角色时，为目标表使用不同别名，分别连接各自源列。")
    lines.extend([
        f"ON UPDATE {fk.on_update}; ON DELETE {fk.on_delete}; MATCH {fk.match}",
    ])
    return "\n".join(lines)


def _load_semantics(
    semantic_file: Path | None,
    catalog: CatalogSnapshot,
) -> tuple[SemanticProfile | None, dict[str, str], dict[str, Any]]:
    """读取可选语义摘要，并绑定到当前真实表和完整外键。

    参数:
        semantic_file: 离线推断配置路径；None 表示不补充推断摘要。
        catalog: 已验证的结构快照，用于校验配置的库、表和关系范围。
    返回:
        配置对象、relation_key 到摘要的映射、配置性质与版本/hash 的 metadata。
    异常:
        KnowledgeError: 库/schema、表或关系不匹配，或摘要重复绑定同一关系。
        OSError / ValueError: 配置不可读或数据契约损坏。
    副作用:
        只读取配置，不调用模型，不修改输入。
    """
    if semantic_file is None:
        return None, {}, {}
    return _parse_semantics(semantic_file.read_bytes(), semantic_file.name, catalog)


def _parse_semantics(
    raw: bytes, semantic_name: str, catalog: CatalogSnapshot,
) -> tuple[SemanticProfile, dict[str, str], dict[str, Any]]:
    """解析同一份语义原字节，避免摘要与正文使用不同版本。

    参数:
        raw: 配置原字节。
        semantic_name: 原配置 basename。
        catalog: 原始结构。
    返回:
        配置、关系映射及 metadata。
    异常:
        KnowledgeError / ValueError: 配置或引用无效。
    副作用:
        无文件读写；不改变结构顺序。
    """
    profile = SemanticProfile.model_validate_json(raw)
    _require(profile.database_id == catalog.database_id and profile.schema_name == catalog.schema_name,
             "语义配置 database_id/schema_name 与当前库不一致")
    tables = {table.name: table for table in catalog.tables}
    _require(profile.tables.keys() <= tables.keys(), "语义配置包含未知表")
    meanings = {}
    for item in profile.relationships:
        table = tables.get(item.source_table)
        matches = [fk for fk in table.foreign_keys if fk.source_columns == item.source_columns
                   and fk.target_table == item.target_table and fk.target_columns == item.target_columns] if table else []
        _require(bool(matches), "语义配置指向不存在的完整外键")
        for fk in matches:
            key = _key("declared_fk", catalog.database_id, catalog.schema_name, table.name, fk.foreign_key_id)
            _require(key not in meanings, "重复的关系语义摘要")
            meanings[key] = item.meaning
    return profile, meanings, {
        "semantic_origin": profile.origin,
        "semantic_profile": {"file": semantic_name, "sha256": sha256(raw).hexdigest(),
                             "version": profile.version, "author": profile.author},
    }


def _load_bundle(bundle_dir: Path) -> _Bundle:
    """读取并检查正式修正包的一致性。

    参数:
        bundle_dir: 含 curation_manifest.json 的已批准修正包目录。
    返回:
        已核对文件 hash、目录身份和字段来源引用的 _Bundle。
    异常:
        KnowledgeError: 状态、路径、hash 或来源引用不符合约定。
        OSError / ValueError: 文件不可读、JSON 或数据契约损坏。
    """
    root = bundle_dir.resolve()
    manifest_bytes = (root / "curation_manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    status = "approved_corrections_applied"
    _require(manifest.get("format_version") == "description-curation-v1"
             and manifest.get("status") == status, "需要已批准的 v1 修正包")
    artifacts = manifest["artifacts_sha256"]
    verified: dict[str, bytes] = {}
    for relative, expected in artifacts.items():
        path = (root / relative).resolve()
        _require(not Path(relative).is_absolute() and "\\" not in relative
                 and path.is_relative_to(root) and path.is_file(),
                 f"产物路径越界或不可读: {relative}")
        raw = path.read_bytes()
        _require(sha256(raw).hexdigest() == expected, f"产物 hash 不一致: {relative}")
        verified[relative] = raw
    return _parse_bundle(manifest_bytes, verified)


def _parse_bundle(manifest_bytes: bytes, verified: dict[str, bytes]) -> _Bundle:
    """核对固定输入字节，供旧文件入口与 corpus 内存入口共用。

    参数:
        manifest_bytes: 原清单字节。
        verified: 清单文件名到原字节的映射。
    返回:
        完成批准范围、摘要和 lineage 核对的包。
    异常:
        KnowledgeError / ValueError: 文件或引用不一致。
    副作用:
        无文件读写，保留原顺序。
    """
    manifest = json.loads(manifest_bytes)
    status = "approved_corrections_applied"
    _require(manifest.get("format_version") == "description-curation-v1"
             and manifest.get("status") == status, "需要已批准的 v1 修正包")
    artifacts = manifest["artifacts_sha256"]
    _require(set(verified) == set(artifacts), "输入文件与清单不一致")
    for relative, expected in artifacts.items():
        _require(sha256(verified[relative]).hexdigest() == expected,
                 f"产物 hash 不一致: {relative}")
    required = {"enriched_catalog.json", "import_warnings.json", "correction_report.json"}
    _require(required <= verified.keys(), "修正包缺少必要产物")
    enriched = EnrichedCatalog.model_validate_json(verified["enriched_catalog.json"])
    warnings = json.loads(verified["import_warnings.json"])
    report = json.loads(verified["correction_report.json"])
    _require(manifest["database_id"] == enriched.catalog.database_id
             == warnings.get("database_id"), "修正包 database_id 不一致")
    _require(warnings.get("format_version") == "description-warnings-v1"
             and warnings.get("warnings") == [] and manifest["remaining_warnings"] == 0,
             "本课仅接受无未解决导入警告的修正包")
    _require(report.get("format_version") == "description-quality-report-v1"
             and report.get("status") == status and report["warning_counts_after"] == {},
             "修正报告状态不一致")
    for field in ("approval", "base_enriched_sha256", "plan_sha256"):
        _require(report[field] == manifest[field], f"修正报告 {field} 不一致")
    _require(manifest["approval"].get("scope") == "listed_corrections_only", "缺少修正范围批准记录")

    sources = {source.source_file: source for source in enriched.sources}
    _require(len(sources) == len(enriched.sources), "重复的 CSV 来源")
    _require(set(verified) == required | {f"derived_source/{name}" for name in sources},
             "派生 CSV 与产物清单不一致")
    for name, source in sources.items():
        _require(source.source_sha256 == artifacts[f"derived_source/{name}"],
                 f"派生 CSV 来源 hash 不一致: {name}")
    tables = {table.name: table for table in enriched.catalog.tables}
    _require(len(tables) == len(enriched.catalog.tables), "重复表名")
    column_sets = {name: {column.name for column in table.columns} for name, table in tables.items()}
    for name, table in tables.items():
        _require(len(column_sets[name]) == len(table.columns), f"重复字段: {name}")
        _require(len({fk.foreign_key_id for fk in table.foreign_keys}) == len(table.foreign_keys),
                 f"重复外键 ID: {name}")
        for fk in table.foreign_keys:
            _require(fk.target_table in tables
                     and len(fk.source_columns) == len(fk.target_columns) == len(fk.declared_target_columns)
                     and set(fk.source_columns) <= column_sets[name]
                     and set(fk.target_columns) <= column_sets.get(fk.target_table, set()),
                     f"外键端点不完整: {name}/{fk.foreign_key_id}")
    notes = {(note.table_name, note.column_name): note for note in enriched.descriptions}
    lineage = {(item["table_name"], item["column_name"]): item for item in report["lineage"]}
    _require(len(notes) == len(enriched.descriptions)
             and len(lineage) == len(report["lineage"]) and notes.keys() == lineage.keys(),
             "字段说明与 lineage 不一一对应")
    changes = report["changes"]
    _require(manifest["changes"] == len(changes)
             and manifest["rules"] == len({change["rule_id"] for change in changes}),
             "修正规则或变化计数不一致")
    for identity, note in notes.items():
        table_name, column_name = identity
        item = lineage[identity]
        original = SourceRecord.model_validate(item["original_source"])
        _require(column_name in column_sets.get(table_name, set()), "字段说明指向不存在的字段")
        _require(note.source.source_file in sources, "字段说明缺少文件来源")
        _require(item["derived_source"] == note.source.model_dump(mode="json"), "派生 lineage 不一致")
        _require(original.source_file == note.source.source_file
                 and item["original_source_sha256"]
                 == manifest["original_source_sha256"].get(Path(original.source_file).name),
                 "原始 lineage 来源不一致")
        related = [change for change in changes
                   if (change["table_name"], change["column_name"]) == identity]
        _require(sorted(item["correction_ids"]) == sorted(change["rule_id"] for change in related),
                 "lineage 修正规则引用不完整")
    for change in changes:
        identity = (change["table_name"], change["column_name"])
        _require(identity in notes and change["field"] == "value_description", "变化指向未知字段")
        item = lineage[identity]
        _require(change["original_source"] == item["original_source"]
                 and change["original_source_sha256"] == item["original_source_sha256"],
                 "变化的原始来源与 lineage 不一致")
        _require((change["after"].strip() or None) == notes[identity].value_description,
                 "变化后的值与当前字段说明不一致")
    return _Bundle(enriched, manifest, report, sha256(manifest_bytes).hexdigest())

def render_knowledge(bundle_dir: Path, semantic_file: Path | None = None) -> KnowledgeDraft:
    """把一份修正包渲染成相互关联的知识草稿。

    参数:
        bundle_dir: 已批准的 R1.2 修正包目录。
        semantic_file: 可选离线推断摘要配置；不提供时表块保持精简结构概览。
    返回:
        KnowledgeDraft，含文档、知识块和声明外键关系；保持 draft 状态。
    副作用:
        只读取本地输入，不写文件，不调用模型、SQLite 或向量数据库。
    """
    bundle = _load_bundle(bundle_dir)
    return _render_bundle(bundle, *_load_semantics(semantic_file, bundle.enriched.catalog))


def render_knowledge_inputs(
    manifest_bytes: bytes, input_files: dict[str, bytes],
    semantic_bytes: bytes, semantic_name: str,
) -> KnowledgeDraft:
    """从固定输入字节生成与 R1.3 文件入口相同的草稿。

    参数:
        manifest_bytes: curation_manifest 原字节。
        input_files: 清单声明的相对文件名到原字节的映射。
        semantic_bytes: 必需语义配置原字节。
        semantic_name: 原 basename，用于保留定位。
    返回:
        原有 KnowledgeDraft，状态仍为 draft。
    异常:
        KnowledgeError / ValueError: 输入或语义无效。
    副作用:
        无；路径及来源范围由调用方校验。
    """
    bundle = _parse_bundle(manifest_bytes, input_files)
    return _render_bundle(bundle, *_parse_semantics(
        semantic_bytes, semantic_name, bundle.enriched.catalog,
    ))


def _render_bundle(
    bundle: _Bundle, profile: SemanticProfile | None,
    relation_meanings: dict[str, str], semantic_metadata: dict[str, Any],
) -> KnowledgeDraft:
    """渲染已核对的内容，保持原知识表达及成员顺序。

    参数:
        bundle: 批准包。
        profile: 可选推断配置。
        relation_meanings: 关系语义映射。
        semantic_metadata: 配置来源和版本。
    返回:
        文档、三类块和关系草稿。
    副作用:
        无文件读写，不排序原结构。
    """
    enriched, manifest, report = bundle.enriched, bundle.manifest, bundle.report
    catalog = enriched.catalog
    db, schema = catalog.database_id, catalog.schema_name
    notes = {(note.table_name, note.column_name): note for note in enriched.descriptions}
    lineage = {(item["table_name"], item["column_name"]): item for item in report["lineage"]}
    source_hashes = {source.source_file: source.source_sha256 for source in enriched.sources}
    result = KnowledgeDraft(documents=[], chunks=[], relations=[])
    input_bundle = {
        "curation_manifest_sha256": bundle.manifest_sha256,
        "enriched_catalog_sha256": manifest["artifacts_sha256"]["enriched_catalog.json"],
        "correction_report_sha256": manifest["artifacts_sha256"]["correction_report.json"],
        "base_enriched_sha256": manifest["base_enriched_sha256"],
        "plan_sha256": manifest["plan_sha256"], "approval": manifest["approval"],
    }
    if profile:
        input_bundle.update(semantic_metadata)
    for table_index, table in enumerate(catalog.tables):
        document_key = _key("table_document", db, schema, table.name)
        locator = ["catalog", "tables", table_index]
        table_title = _qualified(schema, table.name)
        common = {"document_key": document_key, "database_id": db}
        table_meaning = profile.tables.get(table.name) if profile else None
        table_content = "\n".join([
            f"数据库: {db}; 表: {table_title}",
            f"表用途: {table_meaning.purpose if table_meaning else '未配置表级摘要。'}",
            f"记录粒度: {table_meaning.row_grain if table_meaning else '未配置。'}",
            "字段目录: " + ", ".join(_qualified(column.name) for column in table.columns),
            "行标识线索（主键声明、成员顺序）: "
            + (", ".join(_qualified(name) for name in table.primary_key_columns()) or "未声明"),
            "出向关联（声明外键）: " + ("; ".join(
                ", ".join(_qualified(name) for name in fk.source_columns) + " → "
                + _qualified(schema, fk.target_table) + " ("
                + ", ".join(_qualified(name) for name in fk.target_columns) + ")"
                for fk in table.foreign_keys
            ) or "未声明"),
        ])
        table_chunks = [ChunkDraft(
            **common, chunk_key=_key("table", db, schema, table.name), knowledge_type="table",
            schema_name=schema, table_name=table.name, title=table_title, content=table_content,
            metadata={
                "structure_locator": locator, "without_rowid": table.without_rowid, "strict": table.strict,
                "semantic_render_version": "compact-context-v2",
                **(semantic_metadata if table_meaning else {"semantic_origin": "not_configured"}),
                "semantic_sources": _semantic_sources(
                    [(table.name, column.name) for column in table.columns], notes, source_hashes, db, schema,
                ),
            },
        )]
        column_sources = {}
        for column_index, column in enumerate(table.columns):
            note = notes.get((table.name, column.name))
            title = _qualified(schema, table.name, column.name)
            content = "\n".join([
                f"数据库: {db}; 字段: {title}",
                f"声明类型: {column.declared_type or '未声明'}",
                f"not_null 声明标志: {column.not_null_flag}; 主键位置: {column.pk_position}; hidden: {column.hidden}",
                f"默认 SQL 表达式（仅文本）: {column.default_sql if column.default_sql is not None else '未声明'}",
                f"展示名: {note.display_name if note and note.display_name else '来源未说明'}",
                f"字段解释: {note.description if note and note.description else '来源未说明'}",
                f"取值解释: {note.value_description if note and note.value_description else '来源未说明'}",
            ])
            table_chunks.append(ChunkDraft(
                **common, chunk_key=_key("column", db, schema, table.name, column.name),
                knowledge_type="column", schema_name=schema, table_name=table.name,
                column_name=column.name, title=title, content=content,
                metadata={
                    "structure_locator": locator + ["columns", column_index],
                    "structure": column.model_dump(mode="json"),
                    "source_column": column.name if note else None,
                    "display_name": note.display_name if note else None,
                    "source_data_format": note.source_data_format if note else None,
                    "review_status": note.review_status if note else "missing_description",
                },
            ))
            if note:
                column_sources[column.name] = {
                    **lineage[(table.name, column.name)],
                    "derived_source_sha256": source_hashes[note.source.source_file],
                }
        for fk_index, fk in enumerate(table.foreign_keys):
            relation_key = _key("declared_fk", db, schema, table.name, fk.foreign_key_id)
            provenance = {
                "artifact": "enriched_catalog.json",
                "artifact_sha256": input_bundle["enriched_catalog_sha256"],
                "locator": locator + ["foreign_keys", fk_index],
            }
            result.relations.append(RelationDraft(
                relation_key=relation_key, source_document_key=document_key, database_id=db,
                source_schema=schema, source_table=table.name, source_columns=fk.source_columns,
                target_schema=schema, target_table=fk.target_table, target_columns=fk.target_columns,
                provenance=provenance, metadata=fk.model_dump(mode="json"),
            ))
            title = f"声明外键 {table_title} / {fk.foreign_key_id}"
            meaning = relation_meanings.get(relation_key)
            content = _relationship_content(table, fk, db, schema, notes, meaning)
            # 同目标角色说明也会引用兄弟外键的字段，来源一并保存。
            role_fields = [
                (table.name, name) for other in table.foreign_keys
                if other.target_table == fk.target_table for name in other.source_columns
            ]
            table_chunks.append(ChunkDraft(
                **common, chunk_key=_key("relationship", db, schema, table.name, fk.foreign_key_id),
                knowledge_type="relationship", relation_key=relation_key, title=title, content=content,
                metadata={
                    "provenance": provenance, "semantic_render_version": "compact-context-v2",
                    **(semantic_metadata if meaning else {"semantic_origin": "source_extracted"}),
                    "semantic_sources": _semantic_sources(
                        [(table.name, name) for name in fk.source_columns]
                        + [(fk.target_table, name) for name in fk.target_columns] + role_fields,
                        notes, source_hashes, db, schema,
                    ),
                },
            ))
        rule_ids = {rule for item in column_sources.values() for rule in item["correction_ids"]}
        corrections = [change for change in report["changes"] if change["rule_id"] in rule_ids]
        result.documents.append(DocumentDraft(
            document_key=document_key, database_id=db, title=table_title,
            content="\n\n".join(f"## {chunk.title}\n{chunk.content}" for chunk in table_chunks),
            metadata={"schema_name": schema, "table_name": table.name, "input_bundle": input_bundle,
                      "structure_locator": locator, "column_sources": column_sources,
                      "corrections": corrections},
        ))
        result.chunks.extend(table_chunks)
    return result
