"""知识测试共用的原创批准包；不访问正式数据或模型。"""

import csv
from hashlib import sha256
import json
from pathlib import Path

import pytest

from querypilot.catalog.corrections import build_curated_catalog
from querypilot.catalog.descriptions import enrich_catalog
from querypilot.catalog.models import CatalogSnapshot, ColumnSchema, ForeignKeySchema, TableSchema


def pytest_addoption(parser) -> None:
    """输入：pytest 解析器；输出：None；注册缺实库必须失败的显式验收开关。"""
    parser.addoption("--require-postgres", action="store_true", default=False,
                     help="R2 实库验收：缺驱动/DSN/服务时失败，不能以 skip 通过")


@pytest.fixture
def r2_corpus(tmp_path):
    """输入：临时目录；输出：原创两列复合 FK 正式快照；仅写临时文件。"""
    from querypilot.knowledge.corpus import build_knowledge_corpus
    bundle = _bundle(tmp_path)
    semantic = tmp_path / "config" / "semantics.json"
    semantic.parent.mkdir()
    _write_json(semantic, {
        "database_id": "demo", "version": "r2-fixture-v1", "author": "原创测试",
        "tables": {"items": {"purpose": "租户内物品。", "row_grain": "一件物品。"},
                   "events": {"purpose": "物品事件。", "row_grain": "一次事件。"}},
        "relationships": [{"source_table": "events", "source_columns": ["tenant_ref", "item_ref"],
                           "target_table": "items", "target_columns": ["tenant_id", "item_id"],
                           "meaning": "事件关联同租户物品。"}],
    })
    target = tmp_path / "corpus"
    build_knowledge_corpus(bundle, semantic, target, "demo")
    return target

def _write_json(path: Path, payload: dict) -> None:
    """写入测试使用的 JSON 文件。

    参数:
        path: pytest 临时目录中的文件路径。
        payload: 可以序列化为 JSON 的字典。
    返回:
        None；写入 UTF-8 文件。
    """
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _bundle(tmp_path: Path, mutation: str | None = None) -> Path:
    """使用真实修正流程构造一个原创的已批准输入包。

    参数:
        tmp_path: pytest 为当前测试创建的独立目录。
        mutation: 可选原创变化标签，在导入/批准修正前改变来源或结构。
    返回:
        包含清单、增强目录、来源和报告的修正包路径。
    副作用:
        只在 tmp_path 创建文件；不访问 BIRD、模型或真实业务数据。
    """
    source = tmp_path / "demo" / "database_description"
    source.mkdir(parents=True)
    values = "+: active\n-: inactive"
    rows_by_table = {
        "items": [
            ["item_id", "", "item identifier", "text", values],
            ["label", "activation label", "activation status", "text", ""],
            ["tenant_id", "", "tenant identifier", "text", ""],
        ],
        "events": [
            ["item_ref", "", "referenced item identifier", "text", ""],
            ["tenant_ref", "", "referenced tenant identifier", "text", ""],
        ],
    }
    if mutation == "description":
        rows_by_table["items"][1][2] = "activation status with explanation"
    tables = []
    for table_name, rows in rows_by_table.items():
        with (source / f"{table_name}.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow([
                "original_column_name", "column_name",
                "column_description", "data_format", "value_description",
            ])
            writer.writerows(rows)
        tables.append(TableSchema(
            name=table_name,
            without_rowid=False,
            strict=False,
            columns=[
                ColumnSchema(
                    name=row[0],
                    ordinal=index,
                    declared_type="TEXT",
                    not_null_flag=False,
                    default_sql=None,
                    pk_position=(
                        {"tenant_id": 1, "item_id": 2}.get(row[0], 0)
                        if table_name == "items" else 0
                    ),
                    hidden=0,
                )
                for index, row in enumerate(rows)
            ],
            foreign_keys=(
                [ForeignKeySchema(
                    foreign_key_id=0,
                    source_columns=["tenant_ref", "item_ref"],
                    target_table="items",
                    declared_target_columns=["tenant_id", "item_id"],
                    target_columns=["tenant_id", "item_id"],
                    on_update="CASCADE",
                    on_delete="RESTRICT",
                    match="NONE",
                )]
                if table_name == "events" else []
            ),
        ))
    if mutation == "type":
        tables[0].columns[0].declared_type = "VARCHAR(64)"
    elif mutation == "pk":
        for column in tables[0].columns:
            if column.pk_position:
                column.pk_position = 3 - column.pk_position
    elif mutation == "pair":
        fk = tables[1].foreign_keys[0]
        fk.source_columns.reverse()
        fk.target_columns.reverse()
        fk.declared_target_columns.reverse()
    elif mutation == "action":
        tables[1].foreign_keys[0].on_delete = "CASCADE"
    catalog = CatalogSnapshot(database_id="demo", tables=tables)
    enriched = enrich_catalog(catalog, source).enriched_catalog
    base = tmp_path / "base.json"
    base.write_text(enriched.model_dump_json(), encoding="utf-8")
    plan = {
        "format_version": "description-quality-plan-v1",
        "status": "approved",
        "database_id": "demo",
        "base_enriched_sha256": sha256(base.read_bytes()).hexdigest(),
        "source_sha256": {
            f"{name}.csv": sha256((source / f"{name}.csv").read_bytes()).hexdigest()
            for name in rows_by_table
        },
        "evidence_summary": ["原创测试资料，不代表真实业务审核"],
        "rules": [{
            "kind": "move_value_description",
            "rule_id": "move-label",
            "table_name": "items",
            "source_column": "item_id",
            "target_column": "label",
            "expected_value_sha256": sha256(values.encode("utf-8")).hexdigest(),
            "reason": "把状态取值说明迁移到状态字段",
        }],
    }
    scope = {key: value for key, value in plan.items() if key != "status"}
    scope_raw = json.dumps(
        scope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    plan["approval"] = {
        "reviewer": "user",
        "approved_on": "2026-10-05",
        "decision": "原创测试批准记录",
        "scope": "listed_corrections_only",
        "approved_scope_sha256": sha256(scope_raw).hexdigest(),
        "reviewed_plan_sha256": "0" * 64,
        "reviewed_candidate_manifest_sha256": "1" * 64,
    }
    plan_file = tmp_path / "plan.json"
    _write_json(plan_file, plan)
    bundle = tmp_path / "curated"
    build_curated_catalog(base, source, plan_file, bundle)
    return bundle
