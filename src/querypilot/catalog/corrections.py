"""应用用户已批准的字段说明修正规则，保留原始及派生来源。"""

from collections import Counter
from datetime import date
import csv
from hashlib import sha256
import io
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from querypilot.catalog.description_models import EnrichedCatalog
from querypilot.catalog.descriptions import enrich_catalog
from querypilot.catalog.models import CatalogModel


class CorrectionError(ValueError):
    """输入版本或修正前提不满足，拒绝生成修正。"""


class CharacterFix(CatalogModel):
    """输入为限定单元格的字符替换计划；输出经过类型校验的规则。"""

    kind: Literal["replace_characters"]
    rule_id: str
    table_name: str
    column_name: str
    expected_value_sha256: str
    replacements: dict[str, str]
    expected_counts: dict[str, int]
    reason: str


class MoveFix(CatalogModel):
    """输入为同表两列间的取值说明迁移；输出目标必须为空的规则。"""

    kind: Literal["move_value_description"]
    rule_id: str
    table_name: str
    source_column: str
    target_column: str
    expected_value_sha256: str
    reason: str


class CorrectionApproval(CatalogModel):
    """输入为本次用户批准记录；输出批准范围与历史审核对象的标识。"""

    reviewer: Literal["user"]
    approved_on: date
    decision: str = Field(min_length=1)
    scope: Literal["listed_corrections_only"]
    approved_scope_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_candidate_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ApprovedCorrectionPlan(CatalogModel):
    """输入为来源版本与修正清单；输出已获用户批准的修正计划。"""

    format_version: Literal["description-quality-plan-v1"]
    status: Literal["approved"]
    approval: CorrectionApproval
    database_id: str
    base_enriched_sha256: str
    source_sha256: dict[str, str]
    evidence_summary: list[str]
    rules: list[Annotated[CharacterFix | MoveFix, Field(discriminator="kind")]]


def _digest(raw: bytes) -> str:
    """计算字节内容的 SHA-256。

    参数:
        raw: 输入字节，不进行解码或规范化。
    返回:
        小写十六进制哈希字符串。
    """
    return sha256(raw).hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    """将对象写入新 JSON 文件。

    参数:
        path: 已创建的修正目录内的新文件路径。
        payload: 可序列化对象。
    返回:
        None；按稳定格式写入 UTF-8 JSON。
    异常:
        FileExistsError、OSError: 文件已存在或不可写。
    """
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def build_curated_catalog(base_file: Path, source_dir: Path, plan_file: Path, output_dir: Path) -> dict:
    """验证输入并生成派生 CSV、增强目录及逐项修正报告。

    参数:
        base_file: R1.2 原始增强目录文件。
        source_dir: 对应的原始 <database_id>/database_description 目录。
        plan_file: 锁定文件/单元格哈希的显式修正计划。
        output_dir: 尚不存在的输出目录；现有目录一律拒绝覆盖。
    返回:
        修正状态、变更数量和剩余警告数量的摘要字典。
    异常:
        CorrectionError: 输入版本、目标空值或字符计数不满足前提。
        ValidationError、DescriptionError: 计划、目录或 CSV 不合法。
        FileExistsError、OSError: 输出已存在或文件不可读写。
    副作用:
        只向新目录写派生文件；不改原 CSV、base_file、生产入口或审核状态。
        输出不是原子发布；需解析最后生成的 curation_manifest.json 并校验各产物哈希。
    """
    base_raw, plan_raw = base_file.read_bytes(), plan_file.read_bytes()
    plan = ApprovedCorrectionPlan.model_validate_json(plan_raw)
    scope = plan.model_dump(mode="json", exclude={"status", "approval"})
    scope_raw = json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if _digest(scope_raw) != plan.approval.approved_scope_sha256:
        raise CorrectionError("执行内容与批准范围不一致，必须重新审核")
    if _digest(base_raw) != plan.base_enriched_sha256:
        raise CorrectionError("增强目录哈希变化，必须重新审核规则")
    base = EnrichedCatalog.model_validate_json(base_raw)
    if base.catalog.database_id != plan.database_id:
        raise CorrectionError("计划与增强目录的 database_id 不一致")
    if len({rule.rule_id for rule in plan.rules}) != len(plan.rules):
        raise CorrectionError("规则 ID 不可重复")
    source_root = source_dir.resolve(strict=True)
    original = enrich_catalog(base.catalog, source_root)
    if original.enriched_catalog != base:
        raise CorrectionError("当前原始来源无法重建给定增强目录")
    actual_hashes = {Path(source.source_file).name: source.source_sha256 for source in base.sources}
    if actual_hashes != plan.source_sha256:
        raise CorrectionError("来源文件集合或哈希变化，必须重新审核规则")

    # 使用上一层已经验证的来源清单；不允许规则提供任意文件路径。
    raw_files = {name: (source_root / name).read_bytes() for name in actual_hashes}
    if any(_digest(raw) != actual_hashes[name] for name, raw in raw_files.items()):
        raise CorrectionError("读取过程中来源变化")
    records = {
        name: list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True))
        for name, raw in raw_files.items()
    }
    notes = {(note.table_name, note.column_name): note for note in base.descriptions}
    changes, changed_files, touched = [], set(), set()

    def locate(table_name: str, column_name: str) -> tuple[str, int, int]:
        """定位规则所指向的唯一原始单元格。

        参数:
            table_name、column_name: 目录中的真实表名和列名。
        返回:
            来源文件名、CSV 记录位置、value_description 列位置。
        异常:
            CorrectionError: 字段无说明、重复修改或原记录定位不唯一。
        """
        key = (table_name, column_name)
        if key not in notes or key in touched:
            raise CorrectionError("规则字段不存在或被重复修改")
        note = notes[key]
        name = Path(note.source.source_file).name
        positions = [i for i, row in enumerate(records[name]) if i > 0 and row == note.source.raw_values]
        if len(positions) != 1:
            raise CorrectionError("不能唯一定位原始记录")
        headers = [header.strip() for header in records[name][0]]
        touched.add(key)
        return name, positions[0], headers.index("value_description")

    def replace_cell(rule: CharacterFix | MoveFix, column: str, location: tuple[str, int, int], value: str) -> None:
        """记录一项单元格修正，并在内存副本中替换。

        参数:
            rule: 对应的显式规则。
            column: 被修改的真实字段名。
            location: locate 返回的文件、行和列位置。
            value: 修正后的完整单元格文本。
        返回:
            None；更新内存副本，并将修改前原文和来源加入审计清单。
        """
        name, row_index, field_index = location
        old = records[name][row_index][field_index]
        note = notes[(rule.table_name, column)]
        changes.append({
            "rule_id": rule.rule_id, "kind": rule.kind,
            "table_name": rule.table_name, "column_name": column,
            "field": "value_description", "before": old, "after": value,
            "reason": rule.reason, "original_source": note.source.model_dump(mode="json"),
            "original_source_sha256": actual_hashes[name],
        })
        records[name][row_index][field_index] = value
        changed_files.add(name)

    for rule in plan.rules:
        source_column = rule.column_name if isinstance(rule, CharacterFix) else rule.source_column
        location = locate(rule.table_name, source_column)
        name, row_index, field_index = location
        value = records[name][row_index][field_index]
        if _digest(value.encode("utf-8")) != rule.expected_value_sha256:
            raise CorrectionError("单元格内容与规则预期不一致")
        if isinstance(rule, CharacterFix):
            if not rule.replacements or set(rule.replacements) != set(rule.expected_counts):
                raise CorrectionError("替换字符与计数规则不一致")
            for char in rule.replacements:
                if len(char) != 1 or rule.expected_counts[char] < 1 or value.count(char) != rule.expected_counts[char]:
                    raise CorrectionError("待替换字符数量与规则预期不一致")
            # 单次 translate 避免前一项替换的结果再次被下一项替换。
            cleaned = value.translate(str.maketrans(rule.replacements))
            replace_cell(rule, source_column, location, cleaned)
        else:
            target = locate(rule.table_name, rule.target_column)
            target_name, target_row, target_field = target
            if records[target_name][target_row][target_field] != "" or not value:
                raise CorrectionError("迁移必须来自非空说明，且目标单元格严格为空")
            replace_cell(rule, source_column, location, "")
            replace_cell(rule, rule.target_column, target, value)

    # 确认源文件仍未变化，再开始写新目录；所有规则预检失败均不写出半套副本。
    if any((source_root / name).read_bytes() != raw for name, raw in raw_files.items()):
        raise CorrectionError("构建期间原始来源发生变化")
    output = output_dir.resolve()
    if output.is_relative_to(source_root):
        raise CorrectionError("修正输出不得放入原始说明目录")
    output.mkdir(parents=True, exist_ok=False)
    derived = output / "derived_source" / plan.database_id / "database_description"
    derived.mkdir(parents=True)
    for name in sorted(raw_files):
        if name not in changed_files:
            with (derived / name).open("xb") as handle:
                handle.write(raw_files[name])
        else:
            with (derived / name).open("x", encoding="utf-8", newline="") as handle:
                csv.writer(handle, lineterminator="\n").writerows(records[name])
    result = enrich_catalog(base.catalog, derived)
    _write_json(output / "enriched_catalog.json", result.enriched_catalog.model_dump(mode="json"))
    _write_json(output / "import_warnings.json", {
        "format_version": "description-warnings-v1", "database_id": plan.database_id,
        "warnings": [warning.model_dump(mode="json") for warning in result.warnings],
    })
    # 每条派生记录都可追溯；label 新取值的来源另由 changes 中同 rule_id 的迁移两端说明。
    derived_notes = {(note.table_name, note.column_name): note for note in result.enriched_catalog.descriptions}
    lineage = [{
        "table_name": key[0], "column_name": key[1],
        "original_source": note.source.model_dump(mode="json"),
        "original_source_sha256": actual_hashes[Path(note.source.source_file).name],
        "derived_source": derived_notes[key].source.model_dump(mode="json"),
        "correction_ids": [change["rule_id"] for change in changes
                           if (change["table_name"], change["column_name"]) == key],
    } for key, note in sorted(notes.items())]
    _write_json(output / "correction_report.json", {
        "format_version": "description-quality-report-v1", "status": "approved_corrections_applied",
        "base_enriched_sha256": _digest(base_raw), "plan_sha256": _digest(plan_raw),
        "approval": plan.approval.model_dump(mode="json"),
        "evidence_summary": plan.evidence_summary, "changes": changes, "lineage": lineage,
        "warning_counts_before": dict(Counter(w.code for w in original.warnings)),
        "warning_counts_after": dict(Counter(w.code for w in result.warnings)),
    })
    artifacts = {path.relative_to(output).as_posix(): _digest(path.read_bytes())
                 for path in sorted(output.rglob("*")) if path.is_file()}
    manifest = {
        "format_version": "description-curation-v1", "status": "approved_corrections_applied",
        "database_id": plan.database_id, "base_enriched_sha256": _digest(base_raw),
        "plan_sha256": _digest(plan_raw), "builder_sha256": _digest(Path(__file__).read_bytes()),
        "original_source_sha256": actual_hashes,
        "approval": plan.approval.model_dump(mode="json"),
        "artifacts_sha256": artifacts,
        "changes": len(changes), "rules": len(plan.rules), "remaining_warnings": len(result.warnings),
    }
    _write_json(output / "curation_manifest.json", manifest)
    return {key: manifest[key] for key in ("status", "database_id", "changes", "rules", "remaining_warnings")}
