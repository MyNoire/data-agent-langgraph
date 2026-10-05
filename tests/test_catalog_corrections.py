"""正式修正修正的必要行为测试；只使用原创 CSV 和内存结构契约。"""

import csv
from hashlib import sha256
import json
import runpy
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from querypilot.catalog.corrections import CorrectionError, build_curated_catalog
from querypilot.catalog.descriptions import enrich_catalog
from querypilot.catalog.models import CatalogSnapshot, ColumnSchema, TableSchema


def _inputs(tmp_path: Path, *, occupied_target: bool = False) -> tuple[Path, Path, Path]:
    """构造带字符问题和错位取值说明的原创输入。

    参数:
        tmp_path: 测试的独立临时目录。
        occupied_target: 是否让迁移目标已有文本，用于验证拒绝覆盖。
    返回:
        原增强目录路径、原 CSV 目录、规则文件路径。
    副作用:
        仅向 tmp_path 创建 fixture，不读取真实数据库或评测数据。
    """
    source = tmp_path / "demo" / "database_description"
    source.mkdir(parents=True)
    rows = [
        ["item_id", "", "identifier", "text", "+: active\n-: inactive"],
        ["label", "", "activation status", "text", "already present" if occupied_target else ""],
        ["kind", "", "kind code", "text", "\u0095 A: alpha\n\u0093-\u0094: empty"],
    ]
    with (source / "items.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["original_column_name", "column_name", "column_description", "data_format", "value_description"])
        writer.writerows(rows)
    catalog = CatalogSnapshot(database_id="demo", tables=[TableSchema(
        name="items", without_rowid=False, strict=False,
        columns=[ColumnSchema(name=row[0], ordinal=i, declared_type="TEXT", not_null_flag=False,
                              default_sql=None, pk_position=1 if i == 0 else 0, hidden=0)
                 for i, row in enumerate(rows)],
    )])
    enriched = enrich_catalog(catalog, source).enriched_catalog
    base = tmp_path / "base.json"
    base.write_text(enriched.model_dump_json(), encoding="utf-8")
    plan = {
        "format_version": "description-quality-plan-v1", "status": "approved",
        "database_id": "demo", "base_enriched_sha256": sha256(base.read_bytes()).hexdigest(),
        "source_sha256": {"items.csv": sha256((source / "items.csv").read_bytes()).hexdigest()},
        "evidence_summary": ["原创 fixture，不代表真实业务审核"],
        "rules": [
            {"kind": "replace_characters", "rule_id": "characters", "table_name": "items",
             "column_name": "kind", "expected_value_sha256": sha256(rows[2][4].encode()).hexdigest(),
             "replacements": {"\u0095": "•", "\u0093": "'", "\u0094": "'"},
             "expected_counts": {"\u0095": 1, "\u0093": 1, "\u0094": 1}, "reason": "修正示例标点"},
            {"kind": "move_value_description", "rule_id": "move-label", "table_name": "items",
             "source_column": "item_id", "target_column": "label",
             "expected_value_sha256": sha256(rows[0][4].encode()).hexdigest(), "reason": "迁移示例状态解释"},
        ],
    }
    scope = {key: value for key, value in plan.items() if key != "status"}
    scope_hash = sha256(json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    plan["approval"] = {
        "reviewer": "user", "approved_on": "2026-10-05", "decision": "原创测试批准记录",
        "scope": "listed_corrections_only", "approved_scope_sha256": scope_hash,
        "reviewed_plan_sha256": "0" * 64, "reviewed_candidate_manifest_sha256": "1" * 64,
    }
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return base, source, plan_file


def test_curated_cli_is_traceable_and_non_destructive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    """验证定向修正、跨字段来源、原文保护与重复结果。

    参数:
        tmp_path: pytest 独立临时目录。
        monkeypatch: 临时设置正式 CLI 参数。
        capsys: 读取 CLI 的 JSON 摘要。
    返回:
        None；原文、业务编码、来源或输出保护不符合约定时失败。
    """
    base, source, plan = _inputs(tmp_path)
    inputs_before = {path: path.read_bytes() for path in [base, source / "items.csv", plan]}
    first, repeat = tmp_path / "candidate", tmp_path / "repeat"
    script = Path(__file__).resolve().parents[1] / "scripts/curate_catalog.py"
    monkeypatch.setattr(sys, "argv", [
        str(script), "--base", str(base), "--source-dir", str(source),
        "--plan", str(plan), "--output-dir", str(first),
    ])
    runpy.run_path(str(script), run_name="__main__")
    summary = json.loads(capsys.readouterr().out)
    build_curated_catalog(base, source, plan, repeat)
    assert summary == {"status": "approved_corrections_applied", "database_id": "demo",
                       "changes": 3, "rules": 2, "remaining_warnings": 0}
    original = json.loads(base.read_text(encoding="utf-8"))
    candidate = json.loads((first / "enriched_catalog.json").read_text(encoding="utf-8"))
    notes = {note["column_name"]: note for note in candidate["descriptions"]}
    assert candidate["catalog"] == original["catalog"]
    assert candidate["status"] == "draft"
    assert notes["item_id"]["value_description"] is None
    assert notes["label"]["value_description"] == "+: active\n-: inactive"
    assert notes["kind"]["value_description"] == "• A: alpha\n'-': empty"
    report = json.loads((first / "correction_report.json").read_text(encoding="utf-8"))
    moved = [change for change in report["changes"] if change["rule_id"] == "move-label"]
    assert len(moved) == 2
    assert moved[0]["before"] == moved[1]["after"]
    assert moved[0]["original_source"]["raw_values"][0] == "item_id"
    assert moved[1]["original_source"]["raw_values"][0] == "label"
    assert len(report["lineage"]) == 3
    assert all(path.read_bytes() == raw for path, raw in inputs_before.items())
    for path in first.rglob("*"):
        if path.is_file():
            assert path.read_bytes() == (repeat / path.relative_to(first)).read_bytes()
    before = (first / "curation_manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        build_curated_catalog(base, source, plan, first)
    assert (first / "curation_manifest.json").read_bytes() == before


@pytest.mark.parametrize("changed_input", ["base", "source"])
def test_rejects_stale_inputs(tmp_path: Path, changed_input: str) -> None:
    """验证输入发生变化后，旧规则不能静默继续应用。

    参数:
        tmp_path: pytest 独立临时目录。
        changed_input: 改变增强目录或原 CSV 的分支。
    返回:
        None；未拒绝过期输入或失败时创建了输出则失败。
    """
    base, source, plan = _inputs(tmp_path)
    changed = base if changed_input == "base" else source / "items.csv"
    with changed.open("ab") as handle:
        handle.write(b"\n")
    output = tmp_path / "candidate"
    with pytest.raises(CorrectionError):
        build_curated_catalog(base, source, plan, output)
    assert not output.exists()


def test_refuses_nonempty_move_target(tmp_path: Path) -> None:
    """验证正式修正不能覆盖目标字段已有的取值解释。

    参数:
        tmp_path: pytest 独立临时目录。
    返回:
        None；未拒绝有内容的迁移目标或写出部分结果则失败。
    """
    base, source, plan = _inputs(tmp_path, occupied_target=True)
    output = tmp_path / "candidate"
    with pytest.raises(CorrectionError, match="目标单元格严格为空"):
        build_curated_catalog(base, source, plan, output)
    assert not output.exists()


@pytest.mark.parametrize("problem", ["not_approved", "changed_rule"])
def test_approval_cannot_be_reused_for_unreviewed_rules(tmp_path: Path, problem: str) -> None:
    """验证未批准的计划及变化后的规则不能沿用当前批准记录。

    参数:
        tmp_path: pytest 独立临时目录。
        problem: 将计划改为待审核，或修改已批准范围内的规则内容。
    返回:
        None；接受了未批准规则，或失败时写出结果则断言失败。
    """
    base, source, plan_file = _inputs(tmp_path)
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    if problem == "not_approved":
        plan["status"] = "pending_user_review"
    else:
        plan["rules"][0]["replacements"]["\u0095"] = "CHANGED"
    plan_file.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "curated"
    expected = ValidationError if problem == "not_approved" else CorrectionError
    with pytest.raises(expected):
        build_curated_catalog(base, source, plan_file, output)
    assert not output.exists()
