"""R1.3 必要测试：原创来源、复合外键、跨字段迁移和输入完整性。"""

from collections import Counter
import csv
from hashlib import sha256
import json
from pathlib import Path
import runpy
import sys

import pytest

from querypilot.catalog.corrections import build_curated_catalog
from querypilot.catalog.descriptions import enrich_catalog
from querypilot.catalog.models import (
    CatalogSnapshot,
    ColumnSchema,
    ForeignKeySchema,
    TableSchema,
)
from querypilot.knowledge.render import KnowledgeError, render_knowledge


def _write_json(path: Path, payload: dict) -> None:
    """写入测试使用的 JSON 文件。

    参数:
        path: pytest 临时目录中的文件路径。
        payload: 可以序列化为 JSON 的字典。
    返回:
        None；写入 UTF-8 文件。
    """
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _bundle(tmp_path: Path) -> Path:
    """使用真实修正流程构造一个原创的已批准输入包。

    参数:
        tmp_path: pytest 为当前测试创建的独立目录。
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


def test_render_preserves_content_relations_and_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """验证正文、复合外键、迁移来源以及 CLI 输出保护。

    参数:
        tmp_path: 当前测试的独立临时目录。
        monkeypatch: 临时设置 CLI 参数。
        capsys: 捕获 CLI 打印的 JSON 摘要。
    返回:
        None；结果不符合知识草稿契约时断言失败。
    """
    bundle = _bundle(tmp_path)
    before = {path: path.read_bytes() for path in bundle.rglob("*") if path.is_file()}
    draft = render_knowledge(bundle)
    assert draft.status == "draft"
    assert (len(draft.documents), len(draft.chunks), len(draft.relations)) == (2, 8, 1)
    assert Counter(chunk.knowledge_type for chunk in draft.chunks) == {
        "table": 2, "column": 5, "relationship": 1,
    }

    documents = {doc.document_key: doc for doc in draft.documents}
    assert len(documents) == len(draft.documents)
    assert len({chunk.chunk_key for chunk in draft.chunks}) == len(draft.chunks)
    assert all(chunk.document_key in documents for chunk in draft.chunks)

    columns = {
        (chunk.table_name, chunk.column_name): chunk
        for chunk in draft.chunks if chunk.knowledge_type == "column"
    }
    label = columns["items", "label"]
    identifier = columns["items", "item_id"]
    assert "+: active" in label.content
    assert "-: inactive" in label.content
    assert "+: active" not in identifier.content
    assert label.metadata["source_column"] == "label"

    table_chunk = next(chunk for chunk in draft.chunks
                       if chunk.knowledge_type == "table" and chunk.table_name == "items")
    assert "activation status" not in table_chunk.content
    assert "activation label" not in table_chunk.content
    assert "未配置表级摘要" in table_chunk.content
    assert '"tenant_id", "item_id"' in table_chunk.content
    # 概览不再复写字段描述；详细语义和枚举仍由列块负责。
    assert "+: active" not in table_chunk.content
    assert {source["column_name"] for source in table_chunk.metadata["semantic_sources"]} == {
        "item_id", "label", "tenant_id",
    }

    document = documents[label.document_key]
    sources = document.metadata["column_sources"]
    assert sources["label"]["original_source"]["raw_values"][4] == ""
    assert sources["item_id"]["original_source"]["raw_values"][4] == "+: active\n-: inactive"
    assert sources["label"]["derived_source"]["raw_values"][4] == "+: active\n-: inactive"
    assert sources["label"]["correction_ids"] == ["move-label"]
    assert len(sources["label"]["derived_source_sha256"]) == 64
    changes = [
        change for change in document.metadata["corrections"]
        if change["rule_id"] == "move-label"
    ]
    assert len(changes) == 2
    assert {change["column_name"] for change in changes} == {"item_id", "label"}
    assert label.content in document.content

    relation = draft.relations[0]
    assert relation.source_document_key in documents
    assert relation.source_table == "events"
    assert relation.source_columns == ["tenant_ref", "item_ref"]
    assert relation.target_table == "items"
    assert relation.target_columns == ["tenant_id", "item_id"]
    assert relation.metadata["on_update"] == "CASCADE"
    assert relation.metadata["on_delete"] == "RESTRICT"
    relation_chunk = next(
        chunk for chunk in draft.chunks if chunk.knowledge_type == "relationship"
    )
    assert relation_chunk.relation_key == relation.relation_key
    assert (relation_chunk.schema_name, relation_chunk.table_name,
            relation_chunk.column_name) == (None, None, None)
    assert "referenced tenant identifier" in relation_chunk.content
    assert "referenced item identifier" in relation_chunk.content
    assert "tenant identifier" in relation_chunk.content
    assert "item identifier" in relation_chunk.content
    assert ('"source"."tenant_ref" = "target"."tenant_id" AND '
            '"source"."item_ref" = "target"."item_id"') in relation_chunk.content
    assert "JOIN 必须保留全部 AND 条件" in relation_chunk.content
    semantic_sources = relation_chunk.metadata["semantic_sources"]
    assert {(source["table_name"], source["column_name"]) for source in semantic_sources} == {
        ("events", "tenant_ref"), ("events", "item_ref"),
        ("items", "tenant_id"), ("items", "item_id"),
    }
    for source in semantic_sources + table_chunk.metadata["semantic_sources"]:
        source_document = documents[source["document_key"]]
        lineage = source_document.metadata["column_sources"][source["column_name"]]
        assert source["source_sha256"] == lineage["derived_source_sha256"]
        assert source["source_file"] == lineage["derived_source"]["source_file"]
        assert source["line_start"] == lineage["derived_source"]["line_start"]
        assert source["line_end"] == lineage["derived_source"]["line_end"]
        assert source["fields"]

    script = Path(__file__).resolve().parents[1] / "scripts" / "render_knowledge.py"
    output = tmp_path / "rendered"
    monkeypatch.setattr(sys, "argv", [
        str(script), "--bundle", str(bundle), "--output-dir", str(output),
    ])
    runpy.run_path(str(script), run_name="__main__")
    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "status": "draft",
        "documents": 2,
        "chunks": 8,
        "relations": 1,
        "chunk_types": {"table": 2, "column": 5, "relationship": 1},
        "max_chunk_chars": max(len(chunk.content) for chunk in draft.chunks),
    }
    for filename, count in [
        ("documents.jsonl", 2), ("chunks.jsonl", 8), ("relations.jsonl", 1),
    ]:
        records = [
            json.loads(line)
            for line in (output / filename).read_text(encoding="utf-8").splitlines()
        ]
        assert len(records) == count
    saved = {path: path.read_bytes() for path in output.iterdir()}
    with pytest.raises(FileExistsError):
        runpy.run_path(str(script), run_name="__main__")
    assert all(path.read_bytes() == raw for path, raw in saved.items())
    assert all(path.read_bytes() == raw for path, raw in before.items())


@pytest.mark.parametrize("problem", ["hash", "outside_path"])
def test_rejects_corrupt_or_outside_artifact(tmp_path: Path, problem: str) -> None:
    """验证文件损坏和清单越界路径都被拒绝。

    参数:
        tmp_path: 当前测试的独立临时目录。
        problem: 修改文件字节，或添加指向输入包外部的清单路径。
    返回:
        None；渲染器接受损坏或越界输入时断言失败。
    """
    bundle = _bundle(tmp_path)
    if problem == "hash":
        target = bundle / "enriched_catalog.json"
        target.write_bytes(target.read_bytes() + b"\n")
    else:
        outside = tmp_path / "outside.txt"
        outside.write_text("outside the bundle", encoding="utf-8")
        manifest_path = bundle / "curation_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts_sha256"]["../outside.txt"] = sha256(
            outside.read_bytes()
        ).hexdigest()
        _write_json(manifest_path, manifest)
    with pytest.raises(KnowledgeError):
        render_knowledge(bundle)


def test_rejects_inconsistent_lineage_after_rehash(tmp_path: Path) -> None:
    """验证字节哈希一致不能掩盖来源记录之间的矛盾。

    参数:
        tmp_path: 当前测试的独立临时目录。
    返回:
        None；重算报告哈希后，错误 lineage 仍被接受时断言失败。
    """
    bundle = _bundle(tmp_path)
    report_path = bundle / "correction_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    label = next(
        item for item in report["lineage"]
        if (item["table_name"], item["column_name"]) == ("items", "label")
    )
    label["derived_source"]["raw_values"][0] = "wrong_column"
    _write_json(report_path, report)
    manifest_path = bundle / "curation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts_sha256"]["correction_report.json"] = sha256(
        report_path.read_bytes()
    ).hexdigest()
    _write_json(manifest_path, manifest)
    with pytest.raises(KnowledgeError):
        render_knowledge(bundle)


def test_role_context_missing_description_and_quoted_join(tmp_path: Path) -> None:
    """验证多角色外键不会混同、缺失语义不被猜测及 JOIN 标识符转义。

    参数:
        tmp_path: 当前测试的独立临时目录；使用原创包添加第二个引用角色。
    返回:
        None；角色、来源或候选 JOIN 条件不符合契约时断言失败。
    副作用:
        仅修改临时包并重算测试清单的 hash，不修改正式输入。
    """
    bundle = _bundle(tmp_path)
    enriched_path = bundle / "enriched_catalog.json"
    enriched = json.loads(enriched_path.read_text(encoding="utf-8"))
    events = next(table for table in enriched["catalog"]["tables"] if table["name"] == "events")
    events["columns"].append(ColumnSchema(
        name='alternate"ref', ordinal=2, declared_type="TEXT", not_null_flag=False,
        default_sql=None, pk_position=0, hidden=0,
    ).model_dump(mode="json"))
    events["foreign_keys"].append(ForeignKeySchema(
        foreign_key_id=1, source_columns=["tenant_ref", 'alternate"ref'],
        target_table="items", declared_target_columns=["tenant_id", "item_id"],
        target_columns=["tenant_id", "item_id"],
        on_update="NO ACTION", on_delete="NO ACTION", match="NONE",
    ).model_dump(mode="json"))
    _write_json(enriched_path, enriched)
    manifest_path = bundle / "curation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts_sha256"]["enriched_catalog.json"] = sha256(enriched_path.read_bytes()).hexdigest()
    _write_json(manifest_path, manifest)

    draft = render_knowledge(bundle)
    relationships = [chunk for chunk in draft.chunks if chunk.knowledge_type == "relationship"]
    assert len(relationships) == 2
    first, second = relationships
    assert "源表有 2 条外键" in first.content
    assert "外键 0:" in first.content and "外键 1:" in first.content
    assert "为目标表使用不同别名" in first.content
    assert '外键 1: "tenant_ref", "alternate""ref"' in first.content
    # 当前关系的 ON 条件不包含兄弟外键的列。
    first_join = next(line for line in first.content.splitlines() if line.startswith("候选 JOIN"))
    assert "alternate" not in first_join
    second_join = next(line for line in second.content.splitlines() if line.startswith("候选 JOIN"))
    assert ('"source"."tenant_ref" = "target"."tenant_id" AND '
            '"source"."alternate""ref" = "target"."item_id"') in second_join
    assert '"item_ref"' not in second_join
    assert '"alternate""ref"（含义: 来源未说明）' in second.content
    sources = second.metadata["semantic_sources"]
    assert ("events", "item_ref") in {
        (source["table_name"], source["column_name"]) for source in sources
    }
    assert all(source["column_name"] != 'alternate"ref' for source in sources)
    table_chunk = next(chunk for chunk in draft.chunks
                       if chunk.knowledge_type == "table" and chunk.table_name == "events")
    assert '"alternate""ref"' in table_chunk.content
    assert "referenced item identifier" not in table_chunk.content


def test_inferred_summaries_are_compact_and_versioned(tmp_path: Path) -> None:
    """验证推断摘要进入正文、去掉字段重复且保留来源性质和原有列知识。

    参数:
        tmp_path: 当前测试独立目录，用于原创包和原创推断配置。
    返回:
        None；摘要传递、配置版本、完整关系或列知识改变时断言失败。
    副作用:
        仅在临时目录写入 JSON 文件，不调用模型。
    """
    bundle = _bundle(tmp_path)
    baseline = render_knowledge(bundle)
    semantic_file = tmp_path / "semantics.json"
    profile = {
        "database_id": "demo", "version": "demo-inferred-v1", "author": "原创测试推断",
        "tables": {"items": {"purpose": "记录租户下的物品和激活状态。", "row_grain": "租户内的一件物品。"}},
        "relationships": [{
            "source_table": "events", "source_columns": ["tenant_ref", "item_ref"],
            "target_table": "items", "target_columns": ["tenant_id", "item_id"],
            "meaning": "事件引用同一租户内的物品，可查询该物品的激活状态。",
        }],
    }
    _write_json(semantic_file, profile)
    before = semantic_file.read_bytes()
    draft = render_knowledge(bundle, semantic_file)
    table = next(c for c in draft.chunks if c.knowledge_type == "table" and c.table_name == "items")
    relation = next(c for c in draft.chunks if c.knowledge_type == "relationship")
    assert "表用途: 记录租户下的物品和激活状态。" in table.content
    assert "记录粒度: 租户内的一件物品。" in table.content
    assert "activation status" not in table.content
    assert "activation label" not in table.content
    assert "事件引用同一租户内的物品" in relation.content
    assert "referenced item identifier" not in relation.content
    assert '"source"."tenant_ref" = "target"."tenant_id" AND' in relation.content
    for chunk in [table, relation]:
        assert chunk.metadata["semantic_origin"] == "inferred"
        assert chunk.metadata["semantic_profile"]["version"] == "demo-inferred-v1"
        assert chunk.metadata["semantic_profile"]["sha256"] == sha256(before).hexdigest()
    assert [c for c in draft.chunks if c.knowledge_type == "column"] == [
        c for c in baseline.chunks if c.knowledge_type == "column"
    ]
    assert draft.relations == baseline.relations
    assert semantic_file.read_bytes() == before
    # 不能把摘要配置误用于别的数据库或不存在的关系。
    profile["database_id"] = "another_database"
    _write_json(semantic_file, profile)
    with pytest.raises(KnowledgeError, match="database_id"):
        render_knowledge(bundle, semantic_file)
    profile["database_id"] = "demo"
    profile["relationships"][0]["source_columns"] = ["tenant_ref"]
    _write_json(semantic_file, profile)
    with pytest.raises(KnowledgeError, match="不存在的完整外键"):
        render_knowledge(bundle, semantic_file)
