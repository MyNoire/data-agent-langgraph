"""单进程离线 corpus：固定输入、确定性封装、只读核验和防覆盖。"""

from collections import Counter
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import time
from typing import Any, TypeVar

from pydantic import ValidationError

from querypilot.catalog.description_models import EnrichedCatalog
from querypilot.catalog.descriptions import _match_name, read_description_bytes
from querypilot.catalog.models import CatalogSnapshot
from querypilot.knowledge.corpus_identity import (
    canonical_json, content_hash, digest, json_file, jsonl_file, object_id,
    parse_json, schema_version,
)
from querypilot.knowledge.corpus_models import (
    ArtifactEntry, BuildConfig, BuildRunRecord, BuildSummary, CHECK_CODES,
    CheckResult, CorpusChunk, CorpusDocument, CorpusManifest, CorpusModel,
    CorpusRelation, Counts, HistoricalInputs, HistoricalReference,
    IdentityDescriptor, ProvenanceScope, RecheckedInputs, RecheckedLineage,
    SourceEntry, UNVERIFIED, ValidationReport, VerificationSummary,
)
from querypilot.knowledge.models import KnowledgeDraft
from querypilot.knowledge.storage_models import VerifiedCorpus
from querypilot.knowledge.render import KnowledgeError, render_knowledge_inputs

REQUIRED_BUNDLE = {
    "enriched_catalog.json": "enriched_catalog",
    "import_warnings.json": "import_warnings",
    "correction_report.json": "correction_report",
}
MANIFEST_FIELDS = {
    "approval", "artifacts_sha256", "base_enriched_sha256", "builder_sha256",
    "changes", "database_id", "format_version", "original_source_sha256",
    "plan_sha256", "remaining_warnings", "rules", "status",
}
GROUPS = {
    "documents": (CorpusDocument, "document_key", "document_id"),
    "chunks": (CorpusChunk, "chunk_key", "chunk_id"),
    "relations": (CorpusRelation, "relation_key", "relation_id"),
}
T = TypeVar("T", bound=CorpusModel)


class CorpusError(ValueError):
    """可定位且可安全打印的构建/核验错误；原始异常只保存在 cause。"""

    def __init__(self, error_code: str, message: str, *, logical_path: str | None = None,
                 object_key: str | None = None) -> None:
        """初始化错误。输入：类别、安全消息及逻辑定位；输出：异常；无文件副作用。"""
        super().__init__(message)
        self.error_code = error_code
        self.logical_path = logical_path
        self.object_key = object_key

    def as_dict(self) -> dict[str, Any]:
        """生成 CLI 错误。输入：自身；输出：无正文/cause 的字典；无副作用。"""
        result = {"event": "corpus_failed", "component": "knowledge_corpus",
                  "error_code": self.error_code, "message": str(self)}
        for name in ("logical_path", "object_key"):
            if getattr(self, name) is not None:
                result[name] = getattr(self, name)
        return result


@dataclass
class _Inputs:
    """固定源字节及已核对草稿，避免读取过程中混用两个源版本。"""

    files: dict[str, bytes]
    catalog: CatalogSnapshot
    draft: KnowledgeDraft
    manifest: dict[str, Any]
    sources: list[SourceEntry]


@dataclass
class _Candidate:
    """确定性的核心、报告和清单候选；不包含本次操作记录。"""

    core: dict[str, bytes]
    report: ValidationReport
    manifest: CorpusManifest
    rows: dict[str, list[dict[str, Any]]]


def _require(condition: bool, code: str, message: str, *, path: str | None = None,
             key: str | None = None) -> None:
    """检查边界。输入：条件和定位；输出：None；不成立抛 CorpusError；无副作用。"""
    if not condition:
        raise CorpusError(code, message, logical_path=path, object_key=key)


def _logical_path(relative: str) -> str:
    """检查规范逻辑路径。

    参数: relative 为不可信相对路径。
    返回: 原字符串，保留真实名称。
    异常: CorpusError 表示绝对路径、点段、盘符或 Windows ADS 等越界语法。
    副作用: 无。
    """
    _require(isinstance(relative, str) and bool(relative) and "\\" not in relative
             and ":" not in relative and all(part not in {"", ".", ".."}
                                              for part in relative.split("/")),
             "source_scope_violation", "逻辑路径不符合规范")
    return relative


def _safe_path(root: Path, relative: str) -> Path:
    """解析受限路径。输入：根和逻辑路径；输出：根内路径；越界抛 CorpusError；只读。"""
    relative = _logical_path(relative)
    path = root / relative
    try:
        inside = path.resolve().is_relative_to(root.resolve())
    except RuntimeError as exc:
        raise CorpusError("source_scope_violation", "无法解析循环链接", logical_path=relative) from exc
    _require(inside, "source_scope_violation",
             "路径或链接越出指定根目录", path=relative)
    return path


def _read(root: Path, relative: str) -> bytes:
    """读取受限文件。输入：根和相对路径；输出：原字节；I/O 原异常保留到边界处理。"""
    return _safe_path(root, relative).read_bytes()


def _parse(raw: bytes, path: str, code: str = "artifact_integrity") -> Any:
    """严格解析。输入：字节和安全定位；输出：JSON；失败抛脱敏 CorpusError；无写入。"""
    try:
        return parse_json(raw)
    except (ValueError, UnicodeError, TypeError) as exc:
        raise CorpusError(code, "JSON 编码或属性不合法", logical_path=path) from exc


def _model(model: type[T], value: Any, path: str, code: str = "artifact_integrity") -> T:
    """校验模型。输入：类型/数据/定位；输出：严格对象；失败仅暴露类别，保留 cause。"""
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise CorpusError(code, "格式、版本或字段约束不合法", logical_path=path) from exc


def _input_roles(files: dict[str, bytes]) -> dict[str, str]:
    """校验固定源集合。输入：inputs 路径及原字节；输出：路径到固定角色；无写入。"""
    prefix = "inputs/bundle/"
    manifest_path = prefix + "curation_manifest.json"
    _require(manifest_path in files, "input_invalid", "缺少批准包清单", path=manifest_path)
    manifest = _parse(files[manifest_path], manifest_path, "input_invalid")
    _require(isinstance(manifest, dict) and set(manifest) == MANIFEST_FIELDS,
             "source_scope_violation", "批准包包含未知或缺失契约字段", path=manifest_path)
    artifacts = manifest["artifacts_sha256"]
    _require(isinstance(artifacts, dict) and set(REQUIRED_BUNDLE) <= set(artifacts),
             "input_invalid", "批准包缺少必要产物", path=manifest_path)
    roles = {manifest_path: "curation_manifest"}
    for relative in artifacts:
        _logical_path(relative)
        role = REQUIRED_BUNDLE.get(relative)
        if role is None:
            _require(relative.startswith("derived_source/") and relative.endswith(".csv"),
                     "source_scope_violation", "来源角色不允许", path=relative)
            role = "derived_csv"
        roles[prefix + relative] = role
    semantic_paths = [path for path in files if path.startswith("inputs/semantics/")]
    _require(len(semantic_paths) == 1, "input_invalid", "需要唯一的显式语义配置")
    semantic_path = semantic_paths[0]
    _logical_path(semantic_path)
    _require(len(semantic_path.split("/")) == 3, "source_scope_violation", "语义配置必须保留 basename")
    roles[semantic_path] = "inferred_semantics"
    _require(set(files) == set(roles), "source_scope_violation", "输入集合含未允许来源或缺失文件")
    return roles


def _prepare(files: dict[str, bytes], database_id: str) -> _Inputs:
    """从固定字节准备输入。输入：白名单源字节、库；输出：已核对输入；无文件副作用。"""
    roles = _input_roles(files)
    manifest_path = "inputs/bundle/curation_manifest.json"
    manifest = _parse(files[manifest_path], manifest_path, "input_invalid")
    for path in files:
        _logical_path(path)
        if path.endswith(".json"):
            _parse(files[path], path, "input_invalid")
    semantic_path = next(path for path, role in roles.items() if role == "inferred_semantics")
    try:
        enriched = EnrichedCatalog.model_validate(_parse(
            files["inputs/bundle/enriched_catalog.json"], "inputs/bundle/enriched_catalog.json", "input_invalid"))
        _require(bool(database_id) and enriched.catalog.database_id == database_id
                 and bool(enriched.catalog.tables), "input_invalid", "数据库范围或结构不一致/为空")
        _check_csv_records(files, enriched)
        draft = render_knowledge_inputs(
            files[manifest_path], {path.removeprefix("inputs/bundle/"): raw
                                   for path, raw in files.items()
                                   if path.startswith("inputs/bundle/") and path != manifest_path},
            files[semantic_path], Path(semantic_path).name,
        )
    except CorpusError:
        raise
    except (KnowledgeError, ValueError, KeyError, TypeError, csv.Error) as exc:
        raise CorpusError("input_invalid", "批准包、结构、语义或来源 lineage 不一致") from exc
    sources = [SourceEntry(role=role, logical_path=path, sha256=digest(files[path]))
               for path, role in sorted(roles.items(), key=lambda item: (item[1], item[0]))]
    return _Inputs(files, enriched.catalog, draft, manifest, sources)


def _check_csv_records(files: dict[str, bytes], enriched: EnrichedCatalog) -> None:
    """核对来源记录与提供的派生 CSV，而不只比较声明的 hash。

    参数: files 为固定来源字节，enriched 为批准包中的说明目录。
    返回: None；缺失说明允许，但已有说明必须可追溯到真实记录。
    异常: CorpusError/ValueError/csv.Error 表示记录、位置或释义不一致。
    副作用: 无文件读写，不编造释义。
    """
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


def _load_inputs(bundle_dir: Path, semantic_file: Path, database_id: str) -> _Inputs:
    """读取指定源。输入：包/语义/库；输出：固定输入；I/O 或契约失败抛 CorpusError。"""
    try:
        root = bundle_dir.resolve()
        manifest_raw = _read(root, "curation_manifest.json")
        manifest = _parse(manifest_raw, "curation_manifest.json", "input_invalid")
        _require(isinstance(manifest, dict) and isinstance(manifest.get("artifacts_sha256"), dict),
                 "input_invalid", "批准包清单无效")
        files = {"inputs/bundle/curation_manifest.json": manifest_raw}
        for relative in manifest["artifacts_sha256"]:
            _logical_path(relative)
            # 在打开额外文件前拒绝非角色来源。
            _require(relative in REQUIRED_BUNDLE or
                     (relative.startswith("derived_source/") and relative.endswith(".csv")),
                     "source_scope_violation", "来源角色不允许", path=relative)
            files["inputs/bundle/" + relative] = _read(root, relative)
        name = _logical_path(semantic_file.name)
        files["inputs/semantics/" + name] = semantic_file.read_bytes()
        return _prepare(files, database_id)
    except OSError as exc:
        raise CorpusError("input_invalid", "无法读取指定的必需输入") from exc
    except RuntimeError as exc:
        raise CorpusError("input_invalid", "输入根目录链接无法解析") from exc


def _provenance(inputs: _Inputs) -> ProvenanceScope:
    """重建来源范围。输入：批准包；输出：实际复查/历史引用；无副作用。"""
    refs = [HistoricalReference(kind="original_csv", key=_logical_path(key), sha256=value)
            for key, value in inputs.manifest["original_source_sha256"].items()]
    refs += [HistoricalReference(kind=kind, key=key, sha256=inputs.manifest[key])
             for kind, key in [("base_enriched_catalog", "base_enriched_sha256"),
                               ("correction_plan", "plan_sha256")]]
    return ProvenanceScope(
        snapshot_inputs=RecheckedInputs(files=sorted(inputs.files)),
        package_lineage=RecheckedLineage(),
        historical_inputs=HistoricalInputs(references=sorted(refs, key=lambda ref: (ref.kind, ref.key))),
    )


def _candidate(inputs: _Inputs, config: BuildConfig) -> _Candidate:
    """构造内存候选。输入：同字节输入及规则；输出：核心/报告/清单；不写文件。"""
    db = inputs.catalog.database_id
    version = schema_version(inputs.catalog)
    descriptor = IdentityDescriptor(database_scope=[db], schema_versions={db: version},
                                    sources=inputs.sources, build_config=config)
    corpus = digest(canonical_json(descriptor.model_dump(mode="json")))
    scope = {"corpus_id": corpus, "schema_version": version}
    documents = {doc.document_key: object_id("document", corpus, db, doc.document_key)
                 for doc in inputs.draft.documents}
    relations = {rel.relation_key: object_id("relation", corpus, db, rel.relation_key)
                 for rel in inputs.draft.relations}
    rows = {
        "documents": [CorpusDocument(**doc.model_dump(mode="json"), **scope,
            document_id=documents[doc.document_key], content_hash=content_hash(doc.title, doc.content)
        ).model_dump(mode="json") for doc in inputs.draft.documents],
        "relations": [CorpusRelation(**rel.model_dump(mode="json"), **scope,
            relation_id=relations[rel.relation_key], source_document_id=documents[rel.source_document_key]
        ).model_dump(mode="json") for rel in inputs.draft.relations],
        "chunks": [CorpusChunk(**chunk.model_dump(mode="json"), **scope,
            chunk_id=object_id("chunk", corpus, db, chunk.chunk_key),
            document_id=documents[chunk.document_key],
            relation_id=relations[chunk.relation_key] if chunk.relation_key else None,
            content_hash=content_hash(chunk.title, chunk.content)
        ).model_dump(mode="json") for chunk in inputs.draft.chunks],
    }
    counts_by_type = Counter(chunk.knowledge_type for chunk in inputs.draft.chunks)
    counts = Counts(documents=len(rows["documents"]), chunks=len(rows["chunks"]),
                    relations=len(rows["relations"]), chunk_types={
                        kind: counts_by_type[kind] for kind in ("table", "column", "relationship")})
    _check_rows(rows, rows, corpus, version, db)
    core = {**inputs.files, "build-config.json": json_file(config.model_dump(mode="json"))}
    for group, (_, key, _) in GROUPS.items():
        core[group + ".jsonl"] = jsonl_file(rows[group], key)
    entries = [ArtifactEntry(path=path, sha256=digest(raw)) for path, raw in sorted(core.items())]
    core_digest = digest(canonical_json([entry.model_dump(mode="json") for entry in entries]))
    report = ValidationReport(corpus_id=corpus, core_digest=core_digest, counts=counts,
                              checks=[CheckResult(code=code) for code in CHECK_CODES],
                              provenance_scope=_provenance(inputs), unverified_capabilities=UNVERIFIED)
    manifest = CorpusManifest(corpus_id=corpus, identity_descriptor=descriptor, counts=counts,
                              core_artifacts=entries, core_digest=core_digest,
                              validation_artifact=ArtifactEntry(path="validation.json", sha256=digest(
                                  json_file(report.model_dump(mode="json")))), verification_scope=CHECK_CODES)
    return _Candidate(core, report, manifest, rows)


def _check_rows(actual: dict[str, list[dict[str, Any]]], expected: dict[str, list[dict[str, Any]]],
                corpus: str, version: str, db: str) -> None:
    """核对对象及有序引用。输入：读回/重渲染行和范围；输出：None；失败抛 CorpusError。"""
    indexed = {}
    for group, (model, key, id_field) in GROUPS.items():
        seen_ids = set()
        indexed[group] = {}
        for row in actual[group]:
            item = _model(model, row, group + ".jsonl")
            logical = getattr(item, key)
            identity = getattr(item, id_field)
            _require(logical not in indexed[group] and identity not in seen_ids,
                     "identity_violation", "重复对象 key 或 ID", key=logical)
            _require(item.corpus_id == corpus and item.schema_version == version and item.database_id == db,
                     "identity_violation", "对象范围不一致", key=logical)
            _require(identity == object_id(group.removesuffix("s"), corpus, db, logical),
                     "identity_violation", "对象 ID 不符合确定性规则", key=logical)
            seen_ids.add(identity)
            indexed[group][logical] = row
    docs = indexed["documents"]
    rels = indexed["relations"]
    for row in actual["chunks"]:
        parent = docs.get(row["document_key"])
        _require(parent is not None and parent["document_id"] == row["document_id"],
                 "reference_violation", "块的父文档 key/ID 不一致", key=row["chunk_key"])
        relation = rels.get(row["relation_key"])
        if row["knowledge_type"] == "relationship":
            _require(relation is not None and relation["relation_id"] == row["relation_id"]
                     and relation["source_document_key"] == row["document_key"]
                     and all(row[name] is None for name in ("schema_name", "table_name", "column_name")),
                     "reference_violation", "关系块的引用或定位不一致", key=row["chunk_key"])
        else:
            _require(row["relation_key"] is None and row["relation_id"] is None
                     and row["schema_name"] is not None and row["table_name"] is not None
                     and (row["column_name"] is not None) == (row["knowledge_type"] == "column"),
                     "reference_violation", "非关系块定位或关联字段不一致", key=row["chunk_key"])
    for row in actual["relations"]:
        parent = docs.get(row["source_document_key"])
        _require(parent is not None and parent["document_id"] == row["source_document_id"],
                 "reference_violation", "关系源文档 key/ID 不一致", key=row["relation_key"])
    for group, (_, key, _) in GROUPS.items():
        wanted = {row[key]: row for row in expected[group]}
        _require(set(indexed[group]) == set(wanted), "artifact_integrity", "对象覆盖不完整", path=group + ".jsonl")
        for logical, row in indexed[group].items():
            difference = {field for field in row if canonical_json(row[field]) != canonical_json(wanted[logical][field])}
            reference_fields = {"source_table", "source_schema", "source_columns", "target_table", "target_schema",
                                "target_columns", "source_document_key", "source_document_id", "document_key",
                                "document_id", "relation_key", "relation_id", "provenance"}
            code = "reference_violation" if difference & reference_fields else "artifact_integrity"
            _require(not difference, code, "对象内容、来源、摘要或有序关系与输入不一致", key=logical)


def _inventory(root: Path) -> set[str]:
    """枚举快照文件。输入：指定目录；输出：文件集合；越界/循环链接拒绝，不扫描工作区。"""
    files = set()
    visited = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        resolved = directory.resolve()
        _require(resolved.is_relative_to(root.resolve()) and resolved not in visited,
                 "source_scope_violation", "目录链接越界或重复")
        visited.add(resolved)
        for path in directory.iterdir():
            relative = path.relative_to(root).as_posix()
            _safe_path(root, relative)
            if path.is_dir():
                pending.append(path)
            else:
                _require(path.is_file(), "artifact_integrity", "不是可读普通文件", path=relative)
                files.add(relative)
    return files


def _core_candidate(root: Path, entries: list[ArtifactEntry], descriptor: IdentityDescriptor) -> _Candidate:
    """从实际核心重建候选。输入：目录/清单条目/身份描述；输出：独立计算候选；只读。"""
    paths = [entry.path for entry in entries]
    _require(paths == sorted(set(paths)), "artifact_integrity", "核心清单重复或顺序错误")
    source_paths = [source.logical_path for source in descriptor.sources]
    _require(len(source_paths) == len(set(source_paths)) and
             all(path.startswith("inputs/") for path in source_paths),
             "source_scope_violation", "来源路径重复或不在输入布局")
    wanted = set(source_paths) | {"build-config.json", "documents.jsonl", "chunks.jsonl", "relations.jsonl"}
    _require(set(paths) == wanted, "artifact_integrity", "核心文件集合与来源不一致")
    raw = {entry.path: _read(root, entry.path) for entry in entries}
    for entry in entries:
        _require(digest(raw[entry.path]) == entry.sha256, "artifact_integrity", "文件字节摘要不一致", path=entry.path)
    config = _model(BuildConfig, _parse(raw["build-config.json"], "build-config.json"), "build-config.json")
    _require(canonical_json(config.model_dump(mode="json")) == canonical_json(descriptor.build_config.model_dump(mode="json")),
             "identity_violation", "规则配置与身份描述不一致")
    inputs = _prepare({path: raw[path] for path in source_paths}, descriptor.database_scope[0])
    candidate = _candidate(inputs, config)
    _require(candidate.manifest.identity_descriptor == descriptor, "identity_violation", "来源或结构身份描述不一致")
    actual = {}
    for group in GROUPS:
        path = group + ".jsonl"
        actual[group] = [_parse(line, path) for line in raw[path].splitlines()]
    _check_rows(actual, candidate.rows, candidate.manifest.corpus_id,
                descriptor.schema_versions[descriptor.database_scope[0]], descriptor.database_scope[0])
    for path, expected in candidate.core.items():
        _require(raw[path] == expected, "artifact_integrity", "核心文件不符合规范字节或内容", path=path)
    return candidate


def _verify(root: Path, manifest: CorpusManifest, *, precommit: bool = False) -> _Candidate:
    """共用标记前/后规则。输入：目录和候选/实际 Manifest；输出：重建候选；只读。"""
    candidate = _core_candidate(root, manifest.core_artifacts, manifest.identity_descriptor)
    _require(candidate.manifest == manifest, "artifact_integrity", "清单身份、数量或摘要不一致", path="manifest.json")
    report_raw = _read(root, "validation.json")
    report = _model(ValidationReport, _parse(report_raw, "validation.json"), "validation.json")
    _require(report == candidate.report and report_raw == json_file(candidate.report.model_dump(mode="json")),
             "artifact_integrity", "报告与实际核验范围不一致", path="validation.json")
    files = _inventory(root)
    allowed = set(candidate.core) | {"validation.json"}
    if not precommit:
        allowed.add("manifest.json")
    _require(files - {"build-run.json"} == allowed, "artifact_integrity", "快照缺文件或含额外文件")
    if not precommit:
        _require(_read(root, "manifest.json") == json_file(candidate.manifest.model_dump(mode="json")),
                 "artifact_integrity", "清单不是规范字节", path="manifest.json")
    return candidate


def load_verified_corpus(corpus_dir: Path) -> VerifiedCorpus:
    """加载并完整核验正式快照，返回当前调用持有的全部对象。

    参数：corpus_dir 为完整 R1.4 目录。
    返回：VerifiedCorpus，包含清单、报告、三类对象 tuple；不得修改嵌套内容。
    异常：CorpusError，沿用现有来源/身份/文件检查，保留底层 cause。
    副作用：只读，不调用模型/数据库；后续使用者无需未经验证二次读 JSONL。
    """
    try:
        root = Path(corpus_dir).resolve()
        manifest = _model(CorpusManifest, _parse(_read(root, "manifest.json"), "manifest.json"), "manifest.json")
        candidate = _verify(root, manifest)
        return VerifiedCorpus(
            manifest=manifest, validation=candidate.report,
            **{name: tuple(model.model_validate(row) for row in candidate.rows[name])
               for name, (model, _, _) in GROUPS.items()},
        )
    except CorpusError:
        raise
    except OSError as exc:
        message = "快照缺少必需文件" if isinstance(exc, FileNotFoundError) else "当前无法读取快照完成核验"
        raise CorpusError("artifact_integrity", message) from exc
    except (ValueError, KeyError, TypeError, RuntimeError) as exc:
        raise CorpusError("artifact_integrity", "快照结构或来源引用不合法") from exc


def verify_knowledge_corpus(corpus_dir: Path) -> VerificationSummary:
    """只读独立核验正式快照。

    参数: corpus_dir 为完整快照目录，不需要原机器源路径。
    返回: 已重算全部来源、对象和文件后的 verified 摘要。
    异常: CorpusError，保留底层 I/O 或校验 cause；不可信报告不作为检查依据。
    副作用: 只读；不修复、不撤销状态，不调用模型或数据库。
    """
    try:
        snapshot = load_verified_corpus(corpus_dir)
        return VerificationSummary(corpus_id=snapshot.manifest.corpus_id, counts=snapshot.validation.counts,
                                   checks=snapshot.validation.checks)
    except CorpusError:
        raise
    except OSError as exc:
        message = "快照缺少必需文件" if isinstance(exc, FileNotFoundError) else "当前无法读取快照完成核验"
        raise CorpusError("artifact_integrity", message) from exc
    except (ValueError, KeyError, TypeError, RuntimeError) as exc:
        raise CorpusError("artifact_integrity", "快照结构或来源引用不合法") from exc


def _write(root: Path, relative: str, raw: bytes) -> None:
    """排他写入。输入：新目录、逻辑路径和字节；输出：None；I/O 原异常由调用边界处理。"""
    path = _safe_path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)


def _run_record(corpus_id: str, started: float) -> BuildRunRecord:
    """记录操作事实。输入：corpus 及计时起点；输出：记录；只读 Git，不参与核心身份。"""
    revision, dirty = None, None
    try:
        repo = Path(__file__).resolve().parents[3]
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo,
                                           stderr=subprocess.DEVNULL, text=True, timeout=3).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=repo,
                                            stderr=subprocess.DEVNULL, text=True, timeout=3).strip())
    except (OSError, subprocess.SubprocessError):
        revision, dirty = None, None
    return BuildRunRecord(corpus_id=corpus_id,
                          built_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                          elapsed_ms=(time.perf_counter() - started) * 1000,
                          git_revision=revision, git_dirty=dirty)


def build_knowledge_corpus(bundle_dir: Path, semantic_file: Path, output_dir: Path,
                           database_id: str, profile_version: str = "r1-4-v1") -> BuildSummary:
    """构建或只读复用正式离线快照。

    参数: bundle_dir/semantic_file 为固定批准输入，output_dir 为目标，database_id
        为显式单库范围，profile_version 为非空声明规则标签。
    返回: 最终独立核验通过的 built 或一致复用的 reused 摘要。
    异常: CorpusError；写出失败不返回成功，保留底层 cause 和实际目录供独立核验。
    副作用: 只创建新目标；不覆盖/补写已有目标，不改输入，不调用模型/数据库。
    """
    started = time.perf_counter()
    bundle_dir, semantic_file, output_dir = map(Path, (bundle_dir, semantic_file, output_dir))
    try:
        root = output_dir.resolve()
    except (OSError, RuntimeError) as exc:
        raise CorpusError("target_conflict", "输出路径无法解析") from exc
    absolute = Path(os.path.abspath(output_dir))
    _require(os.path.normcase(str(absolute)) == os.path.normcase(str(root)),
             "target_conflict", "输出路径含链接或别名目标")
    try:
        source_roots = (bundle_dir.resolve(), semantic_file.parent.resolve())
    except (OSError, RuntimeError) as exc:
        raise CorpusError("input_invalid", "输入路径无法解析") from exc
    for source in source_roots:
        _require(not root.is_relative_to(source) and not source.is_relative_to(root),
                 "target_conflict", "输入与输出目录重叠")
    config = _model(BuildConfig, {"profile_version": profile_version}, "build-config.json", "input_invalid")
    inputs = _load_inputs(bundle_dir, semantic_file, database_id)
    try:
        candidate = _candidate(inputs, config)
    except (ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, CorpusError):
            raise
        raise CorpusError("input_invalid", "输入不能构成完整知识快照") from exc
    if root.exists():
        try:
            verify_knowledge_corpus(root)
            expected = {**candidate.core, "validation.json": json_file(candidate.report.model_dump(mode="json")),
                        "manifest.json": json_file(candidate.manifest.model_dump(mode="json"))}
            _require(all(_read(root, path) == raw for path, raw in expected.items()),
                     "target_conflict", "已有快照与本次输入或规则不同")
        except (CorpusError, OSError) as exc:
            raise CorpusError("target_conflict", "已有目标无效或与本次候选不一致") from exc
        return BuildSummary(outcome="reused", corpus_id=candidate.manifest.corpus_id,
                            counts=candidate.report.counts, output_dir=str(root))
    try:
        root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise CorpusError("target_conflict", "目标已被创建，不覆盖") from exc
    except OSError as exc:
        raise CorpusError("write_failed", "无法创建新目标") from exc
    try:
        for path, raw in sorted(candidate.core.items()):
            _write(root, path, raw)
        # 从写出的真实输入与核心重建报告，不能沿用内存 passed 跳过读回。
        readback = _core_candidate(root, candidate.manifest.core_artifacts, candidate.manifest.identity_descriptor)
        _write(root, "validation.json", json_file(readback.report.model_dump(mode="json")))
        _write(root, "build-run.json", json_file(_run_record(readback.manifest.corpus_id, started).model_dump(mode="json")))
        _verify(root, readback.manifest, precommit=True)
        _write(root, "manifest.json", json_file(readback.manifest.model_dump(mode="json")))
        verify_knowledge_corpus(root)
    except OSError as exc:
        raise CorpusError("write_failed", "新目标写出或读回失败，完整性需独立核验") from exc
    except CorpusError as exc:
        if isinstance(exc.__cause__, OSError):
            raise CorpusError("write_failed", "新目标最终读回暂不可用，未返回成功") from exc
        raise
    return BuildSummary(outcome="built", corpus_id=candidate.manifest.corpus_id,
                        counts=candidate.report.counts, output_dir=str(root))
