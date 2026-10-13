"""R1.4 按内容、确定性、完整性和写出风险组织的原创集成测试。"""

from copy import deepcopy
from contextlib import contextmanager
import json
from pathlib import Path
import runpy
import shutil
import sys

import pytest

from conftest import _bundle, _write_json
from querypilot.knowledge.corpus import CorpusError, build_knowledge_corpus, verify_knowledge_corpus
from querypilot.knowledge.corpus_identity import canonical_json, digest, json_file, jsonl_file
from querypilot.knowledge.corpus_models import CHECK_CODES, UNVERIFIED
from querypilot.knowledge.render import render_knowledge


def _rehash_bundle(bundle: Path) -> None:
    """更新原创样本摘要。输入：临时包；输出：None；只写临时清单，保留源批准记录。"""
    path = bundle / "curation_manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["artifacts_sha256"] = {name: digest((bundle / name).read_bytes())
                                    for name in manifest["artifacts_sha256"]}
    _write_json(path, manifest)


def _inputs(tmp_path: Path, *, complex_schema: bool = False,
            mutation: str | None = None) -> tuple[Path, Path]:
    """准备原创样本。输入：临时目录/复杂结构开关；输出：包、语义路径；仅写临时文件。"""
    bundle = _bundle(tmp_path, mutation)
    profile = {"database_id": "demo", "version": "demo-inferred-v1", "author": "原创测试",
               "tables": {"items": {"purpose": "租户内物品与状态。", "row_grain": "一件物品。"},
                          "events": {"purpose": "物品事件记录。", "row_grain": "一次事件。"}},
               "relationships": [{"source_table": "events", "source_columns": ["tenant_ref", "item_ref"],
                                  "target_table": "items", "target_columns": ["tenant_id", "item_id"],
                                  "meaning": "事件关联同租户物品。"}]}
    if mutation == "pair":
        for field in ("source_columns", "target_columns"):
            profile["relationships"][0][field].reverse()
    elif mutation == "summary":
        profile["tables"]["items"]["purpose"] = "租户内物品与激活状态分析。"
    if complex_schema:
        path = bundle / "enriched_catalog.json"
        enriched = json.loads(path.read_bytes())
        events = enriched["catalog"]["tables"][1]
        events["foreign_keys"].append({**deepcopy(events["foreign_keys"][0]),
                                       "foreign_key_id": 1, "on_delete": "CASCADE"})
        events["columns"].append({"name": 'alternate"ref', "ordinal": 2, "declared_type": "TEXT",
                                  "not_null_flag": False, "default_sql": None, "pk_position": 0, "hidden": 0})
        name = 'Empty " Case/|'
        enriched["catalog"]["tables"].append({"name": name, "without_rowid": False, "strict": False,
            "columns": [{"name": "ID", "ordinal": 0, "declared_type": "INTEGER", "not_null_flag": False,
                         "default_sql": None, "pk_position": 1, "hidden": 0}], "foreign_keys": []})
        profile["tables"][name] = {"purpose": "原创无关联表。", "row_grain": "一条记录。"}
        _write_json(path, enriched)
        _rehash_bundle(bundle)
    semantic = tmp_path / "config/semantics.json"
    semantic.parent.mkdir()
    _write_json(semantic, profile)
    return bundle, semantic


def _files(root: Path) -> dict[str, bytes]:
    """保存目录字节。输入：临时或明确目标；输出：相对名/字节；只读。"""
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _rows(root: Path, group: str) -> list[dict]:
    """读取测试产物。输入：目录及组名；输出：JSONL 行；只读。"""
    return [json.loads(line) for line in (root / (group + ".jsonl")).read_bytes().splitlines()]


def test_complete_preserves_content_sources_and_references(tmp_path: Path) -> None:
    """检查完整语义/来源/范围。输入：原创临时目录；输出：断言；不访问正式包。"""
    bundle, semantic = _inputs(tmp_path, complex_schema=True)
    before = _files(bundle)
    draft = render_knowledge(bundle, semantic)
    out = tmp_path / "corpus"
    result = build_knowledge_corpus(bundle, semantic, out, "demo")
    assert result.outcome == "built" and result.status == "validated"
    verified = verify_knowledge_corpus(out)
    assert verified.corpus_id == result.corpus_id
    assert result.counts.model_dump() == {"documents": 3, "chunks": 12, "relations": 2,
                                          "chunk_types": {"table": 3, "column": 7, "relationship": 2}}
    ids = {}
    for group in ("documents", "chunks", "relations"):
        original = {getattr(row, group[:-1] + "_key"): row.model_dump(mode="json")
                    for row in getattr(draft, group)}
        for row in _rows(out, group):
            key = row[group[:-1] + "_key"]
            assert {field: row[field] for field in original[key]} == original[key]
            assert row["corpus_id"] == result.corpus_id
            ids[key] = row[group[:-1] + "_id"]
            if group == "documents":
                assert row["content_hash"] == digest(canonical_json([row["title"], row["content"]]))
    for row in _rows(out, "chunks"):
        assert row["document_id"] == ids[row["document_key"]]
        assert row["relation_id"] == (ids[row["relation_key"]] if row["relation_key"] else None)
    report = json.loads((out / "validation.json").read_bytes())
    assert report["report_version"] == "knowledge-corpus-report-v1"
    assert report["checks"] == [{"code": code, "status": "passed"} for code in CHECK_CODES]
    assert report["unverified_capabilities"] == UNVERIFIED
    assert report["provenance_scope"]["historical_inputs"]["status"] == "referenced_not_rechecked"
    assert report["provenance_scope"]["snapshot_inputs"]["files"] == sorted(
        path for path in _files(out) if path.startswith("inputs/"))
    assert _files(bundle) == before
    assert (out / "inputs/semantics/semantics.json").read_bytes() == semantic.read_bytes()
    assert json.loads((out / "build-run.json").read_bytes())["record_version"] == "knowledge-corpus-run-v1"


def test_complete_without_foreign_keys(tmp_path: Path) -> None:
    """检查合法空关系。输入：原创临时目录；输出：零关系断言；仅写临时样本。"""
    bundle, semantic = _inputs(tmp_path)
    path = bundle / "enriched_catalog.json"
    enriched = json.loads(path.read_bytes())
    enriched["catalog"]["tables"][1]["foreign_keys"] = []
    _write_json(path, enriched)
    _rehash_bundle(bundle)
    profile = json.loads(semantic.read_bytes())
    profile["relationships"] = []
    _write_json(semantic, profile)
    result = build_knowledge_corpus(bundle, semantic, tmp_path / "corpus", "demo")
    assert result.counts.relations == 0
    assert (tmp_path / "corpus/relations.jsonl").read_bytes() == b""
    assert verify_knowledge_corpus(tmp_path / "corpus").outcome == "verified"


def test_cli_summaries_and_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                 capsys: pytest.CaptureFixture) -> None:
    """检查真实 CLI。输入：临时目录/参数替换/捕获；输出：退出码和脱敏断言。"""
    bundle, semantic = _inputs(tmp_path)
    root = Path(__file__).resolve().parents[1]
    out = tmp_path / "corpus"
    cases = [("build_corpus.py", ["--database-id", "demo", "--bundle", str(bundle),
               "--semantics", str(semantic), "--output-dir", str(out)], "built"),
             ("verify_corpus.py", ["--corpus-dir", str(out)], "verified")]
    for script, args, outcome in cases:
        monkeypatch.setattr(sys, "argv", [script, *args])
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(root / "scripts" / script), run_name="__main__")
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert not captured.err and json.loads(captured.out)["outcome"] == outcome
        assert "租户内物品" not in captured.out and "SELECT" not in captured.out
    for args, code in [([], 2), (["--corpus-dir", str(tmp_path / "missing")], 1)]:
        monkeypatch.setattr(sys, "argv", ["verify_corpus.py", *args])
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(root / "scripts/verify_corpus.py"), run_name="__main__")
        assert exc.value.code == code
        captured = capsys.readouterr()
        assert not captured.out and "Traceback" not in captured.err
        if code == 1:
            assert json.loads(captured.err)["error_code"] == "artifact_integrity"


def test_repeated_build_moved_inputs_and_readonly_reuse(tmp_path: Path) -> None:
    """检查三次、迁移及复用。输入：临时目录；输出：身份/全文件字节断言；不改正式包。"""
    bundle, semantic = _inputs(tmp_path, complex_schema=True)
    snapshots = []
    results = []
    for index in range(3):
        out = tmp_path / f"corpus-{index}"
        results.append(build_knowledge_corpus(bundle, semantic, out, "demo"))
        snapshots.append(_files(out))
    assert len({result.corpus_id for result in results}) == 1
    deterministic = [{path: raw for path, raw in files.items() if path != "build-run.json"}
                     for files in snapshots]
    assert deterministic[0] == deterministic[1] == deterministic[2]
    assert snapshots[0]["build-run.json"] != snapshots[1]["build-run.json"]
    copy_root = tmp_path / "moved"
    shutil.copytree(bundle, copy_root / "bundle")
    (copy_root / "config").mkdir()
    moved_semantic = copy_root / "config" / semantic.name
    moved_semantic.write_bytes(semantic.read_bytes())
    moved = tmp_path / "moved-corpus"
    build_knowledge_corpus(copy_root / "bundle", moved_semantic, moved, "demo")
    assert {path: raw for path, raw in _files(moved).items() if path != "build-run.json"} == deterministic[0]
    before = _files(moved)
    assert build_knowledge_corpus(bundle, semantic, moved, "demo").outcome == "reused"
    assert _files(moved) == before
    portable = tmp_path / "standalone"
    shutil.copytree(moved, portable)
    assert verify_knowledge_corpus(portable).corpus_id == results[0].corpus_id


def _rehash_snapshot(out: Path) -> None:
    """仅更新文件层摘要。输入：被故意篡改的临时快照；输出：None；不修复逻辑身份。"""
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    for entry in manifest["core_artifacts"]:
        entry["sha256"] = digest((out / entry["path"]).read_bytes())
    manifest["core_digest"] = digest(canonical_json(manifest["core_artifacts"]))
    report_path = out / "validation.json"
    report = json.loads(report_path.read_bytes())
    report["core_digest"] = manifest["core_digest"]
    report_path.write_bytes(json_file(report))
    manifest["validation_artifact"]["sha256"] = digest(report_path.read_bytes())
    manifest_path.write_bytes(json_file(manifest))


@pytest.mark.parametrize("problem", [
    "missing", "truncated", "changed_bytes", "extra_file", "stale_report",
    "duplicate_id", "duplicate_key", "dangling_document", "wrong_parent", "dangling_relation",
    "cross_corpus", "cross_database", "cross_schema", "invalid_endpoint", "reordered_pair",
    "wrong_content", "wrong_lineage", "missing_check", "duplicate_check", "false_provenance",
    "missing_unverified", "unknown_version", "duplicate_json", "nonfinite", "invalid_utf8",
])
def test_integrity_rejects_corruption_and_logical_tampering(tmp_path: Path, problem: str) -> None:
    """拒绝文件或重算摘要后的逻辑错误。输入：临时目录/故障；输出：定位错误断言。"""
    bundle, semantic = _inputs(tmp_path, complex_schema=True)
    out = tmp_path / "corpus"
    build_knowledge_corpus(bundle, semantic, out, "demo")
    chunks = _rows(out, "chunks")
    relations = _rows(out, "relations")
    manifest_path = out / "manifest.json"
    if problem == "missing":
        target = out / "chunks.jsonl"
        assert target.resolve().is_relative_to(tmp_path.resolve())
        target.unlink()
    elif problem == "truncated":
        (out / "chunks.jsonl").write_bytes(b'{"chunk_key":')
    elif problem == "changed_bytes":
        (out / "chunks.jsonl").write_bytes((out / "chunks.jsonl").read_bytes() + b"\n")
    elif problem == "extra_file":
        (out / "gold.json").write_text("{}", encoding="utf-8")
    elif problem in {"duplicate_id", "duplicate_key", "dangling_document", "wrong_parent", "dangling_relation",
                     "cross_corpus", "cross_database", "cross_schema", "wrong_content"}:
        first = chunks[0]
        if problem == "duplicate_id":
            chunks.append(deepcopy(first))
        elif problem == "duplicate_key":
            chunks[1]["chunk_key"] = first["chunk_key"]
        elif problem == "dangling_document":
            first["document_key"] = '["missing"]'
        elif problem == "wrong_parent":
            first["document_id"] = next(row["document_id"] for row in chunks
                                         if row["document_key"] != first["document_key"])
        elif problem == "dangling_relation":
            row = next(row for row in chunks if row["knowledge_type"] == "relationship")
            row["relation_key"] = '["missing"]'
        elif problem == "cross_corpus":
            first["corpus_id"] = "a" * 64
        elif problem == "cross_database":
            first["database_id"] = "other"
        elif problem == "cross_schema":
            first["schema_version"] = "a" * 64
        else:
            first["content"] = "人为错误正文"
        (out / "chunks.jsonl").write_bytes(jsonl_file(chunks, "chunk_key"))
        _rehash_snapshot(out)
    elif problem in {"invalid_endpoint", "reordered_pair"}:
        if problem == "invalid_endpoint":
            relations[0]["target_columns"][0] = "不存在的列"
        else:
            relations[0]["target_columns"].reverse()
        (out / "relations.jsonl").write_bytes(jsonl_file(relations, "relation_key"))
        _rehash_snapshot(out)
    elif problem == "wrong_lineage":
        copied_bundle = out / "inputs/bundle"
        path = copied_bundle / "correction_report.json"
        report = json.loads(path.read_bytes())
        report["lineage"][0]["derived_source"]["raw_values"][0] = "wrong_column"
        _write_json(path, report)
        _rehash_bundle(copied_bundle)
        _rehash_snapshot(out)
    elif problem in {"stale_report", "missing_check", "duplicate_check", "false_provenance", "missing_unverified"}:
        path = out / "validation.json"
        report = json.loads(path.read_bytes())
        if problem == "stale_report":
            report["corpus_id"] = "b" * 64
        elif problem == "missing_check":
            report["checks"].pop()
        elif problem == "duplicate_check":
            report["checks"][1] = report["checks"][0]
        elif problem == "false_provenance":
            report["provenance_scope"]["historical_inputs"]["status"] = "rechecked"
        else:
            report["unverified_capabilities"].pop()
        path.write_bytes(json_file(report))
        _rehash_snapshot(out)
    else:
        raw = manifest_path.read_bytes()
        if problem == "unknown_version":
            manifest = json.loads(raw)
            manifest["manifest_version"] = "unknown"
            raw = json_file(manifest)
        elif problem == "duplicate_json":
            raw = raw.replace(b'"status":"validated"', b'"status":"validated","status":"validated"')
        elif problem == "nonfinite":
            raw = raw.replace(b'"documents":3', b'"documents":1e999')
        else:
            raw = b"\xff"
        manifest_path.write_bytes(raw)
    before = _files(out)
    with pytest.raises(CorpusError) as exc:
        verify_knowledge_corpus(out)
    assert exc.value.error_code in {"artifact_integrity", "identity_violation", "reference_violation", "input_invalid"}
    if problem in {"duplicate_id", "duplicate_key", "cross_corpus", "cross_database", "cross_schema"}:
        assert exc.value.error_code == "identity_violation"
    if problem in {"dangling_document", "wrong_parent", "dangling_relation", "invalid_endpoint", "reordered_pair"}:
        assert exc.value.error_code == "reference_violation"
    assert "raw_record" not in json.dumps(exc.value.as_dict())
    assert _files(out) == before


@pytest.mark.parametrize("problem", ["missing_semantics", "empty_structure", "wrong_database", "wrong_semantics",
                                     "unapproved", "extra_source", "outside_path", "empty_version"])
def test_source_policy_rejects_invalid_inputs(tmp_path: Path, problem: str) -> None:
    """拒绝范围/来源错误。输入：临时样本和故障；输出：拒绝且未创建目标；不改正式包。"""
    bundle, semantic = _inputs(tmp_path)
    db, version = "demo", "r1-4-v1"
    if problem == "missing_semantics":
        semantic = semantic.with_name("missing.json")
    elif problem == "empty_structure":
        path = bundle / "enriched_catalog.json"
        enriched = json.loads(path.read_bytes())
        enriched["catalog"]["tables"] = []
        _write_json(path, enriched)
        _rehash_bundle(bundle)
    elif problem == "wrong_database":
        db = "other"
    elif problem == "wrong_semantics":
        profile = json.loads(semantic.read_bytes())
        profile["relationships"][0]["source_columns"] = ["tenant_ref"]
        _write_json(semantic, profile)
    elif problem in {"unapproved", "extra_source", "outside_path"}:
        path = bundle / "curation_manifest.json"
        manifest = json.loads(path.read_bytes())
        if problem == "unapproved":
            manifest["status"] = "draft"
        else:
            name = "gold_sql.json" if problem == "extra_source" else "../outside.csv"
            manifest["artifacts_sha256"][name] = "a" * 64
        _write_json(path, manifest)
    else:
        version = "   "
    out = tmp_path / "corpus"
    before = _files(bundle)
    with pytest.raises(CorpusError) as exc:
        build_knowledge_corpus(bundle, semantic, out, db, version)
    assert exc.value.error_code in {"input_invalid", "source_scope_violation"}
    assert not out.exists() and _files(bundle) == before


@pytest.mark.parametrize("phase", ["core", "report", "precommit_read", "manifest", "manifest_close", "final_read"])
def test_write_faults_and_postcommit_read_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                phase: str) -> None:
    """区分半成品和临时读回失败。输入：临时目录/故障阶段；输出：失败及恢复后核验断言。"""
    bundle, semantic = _inputs(tmp_path)
    out = tmp_path / "corpus"
    original_open = Path.open
    original_read = Path.read_bytes

    @contextmanager
    def close_fault(path: Path):
        """注入关闭后的 I/O 错误。输入：目标文件；输出：上下文句柄；实际字节已完整写入。"""
        with original_open(path, "xb") as handle:
            yield handle
        raise OSError("关闭后操作失败，文件字节已完整")

    def fault_open(path: Path, *args, **kwargs):
        """注入写故障。输入：文件及 open 参数；输出：文件句柄或 OSError；仅影响目标。"""
        mode = args[0] if args else kwargs.get("mode", "r")
        if path.is_relative_to(out) and mode == "xb":
            if phase == "manifest_close" and path.name == "manifest.json":
                return close_fault(path)
            selected = {"core": "chunks.jsonl", "report": "validation.json", "manifest": "manifest.json"}.get(phase)
            if path.name == selected:
                # 真实创建截断文件，再抛错；不是伪造业务异常。
                with original_open(path, "xb") as handle:
                    handle.write(b'{"incomplete":')
                raise OSError("原创 I/O 故障")
        return original_open(path, *args, **kwargs)

    def fault_read(path: Path) -> bytes:
        """注入标记后读取故障。输入：Path；输出：字节或 OSError；不修改文件。"""
        if phase == "final_read" and path == out / "manifest.json":
            raise OSError("临时读取故障")
        if phase == "precommit_read" and path == out / "validation.json":
            raise OSError("标记前报告不可读")
        return original_read(path)

    before = _files(bundle)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", fault_open)
        patch.setattr(Path, "read_bytes", fault_read)
        with pytest.raises(CorpusError) as exc:
            build_knowledge_corpus(bundle, semantic, out, "demo")
        assert exc.value.error_code == "write_failed" and exc.value.__cause__ is not None
    saved = _files(out)
    if phase in {"final_read", "manifest_close"}:
        assert verify_knowledge_corpus(out).outcome == "verified"
        assert build_knowledge_corpus(bundle, semantic, out, "demo").outcome == "reused"
    else:
        with pytest.raises(CorpusError):
            verify_knowledge_corpus(out)
        if phase != "manifest":
            assert not (out / "manifest.json").exists()
    assert _files(out) == saved and _files(bundle) == before


@pytest.mark.parametrize("kind", ["partial", "corrupt", "different", "file", "overlap"])
def test_existing_target_is_never_overwritten(tmp_path: Path, kind: str) -> None:
    """保护已有目标。输入：临时样本及目标类型；输出：拒绝/全文件不变断言。"""
    bundle, semantic = _inputs(tmp_path)
    out = tmp_path / "corpus"
    if kind == "partial":
        out.mkdir()
        (out / "partial.txt").write_text("保留", encoding="utf-8")
    elif kind == "file":
        out.write_bytes(b"keep")
    elif kind == "overlap":
        out = bundle / "nested"
    else:
        build_knowledge_corpus(bundle, semantic, out, "demo", "other" if kind == "different" else "r1-4-v1")
        if kind == "corrupt":
            (out / "chunks.jsonl").write_bytes(b"broken")
    before = _files(tmp_path)
    with pytest.raises(CorpusError) as exc:
        build_knowledge_corpus(bundle, semantic, out, "demo")
    assert exc.value.error_code == "target_conflict"
    assert _files(tmp_path) == before


def test_source_policy_rejects_outside_link(tmp_path: Path) -> None:
    """检查越界目录链接。输入：临时目录；输出：拒绝断言；无权限时明确 skip。"""
    bundle, semantic = _inputs(tmp_path)
    outside = tmp_path / "outside"
    shutil.copytree(bundle / "derived_source", outside)
    link = bundle / "linked_source"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"系统不允许创建目录符号链接：{type(exc).__name__}")
    path = bundle / "curation_manifest.json"
    manifest = json.loads(path.read_bytes())
    name = next(name for name in manifest["artifacts_sha256"] if name.startswith("derived_source/"))
    # 使用看似合法角色的链接路径，让 resolve 的根边界检查实际触发。
    linked_name = name.replace("derived_source/", "derived_source/link/", 1)
    inner_link = bundle / "derived_source/link"
    inner_link.symlink_to(outside, target_is_directory=True)
    manifest["artifacts_sha256"][linked_name] = manifest["artifacts_sha256"].pop(name)
    _write_json(path, manifest)
    with pytest.raises(CorpusError) as exc:
        build_knowledge_corpus(bundle, semantic, tmp_path / "corpus", "demo")
    assert exc.value.error_code == "source_scope_violation"


@pytest.mark.parametrize("change", ["description", "summary", "type", "pk", "pair", "action",
                                    "rule", "source_format", "operation"])
def test_version_change_matrix(tmp_path: Path, change: str) -> None:
    """验证版本分层。输入：临时目录和变化；输出：结构/corpus/内容/ID 变化断言。"""
    left = tmp_path / "left"
    left.mkdir()
    bundle, semantic = _inputs(left)
    baseline_out = tmp_path / "baseline"
    baseline = build_knowledge_corpus(bundle, semantic, baseline_out, "demo")
    right = tmp_path / "right"
    right.mkdir()
    mutated_bundle, mutated_semantic = _inputs(right, mutation=change)
    if change == "source_format":
        mutated_semantic.write_bytes(mutated_semantic.read_bytes() + b"\n")
    changed_out = tmp_path / "changed"
    changed = build_knowledge_corpus(mutated_bundle, mutated_semantic, changed_out, "demo",
                                     "r1-4-v2" if change == "rule" else "r1-4-v1")
    first_manifest = json.loads((baseline_out / "manifest.json").read_bytes())
    second_manifest = json.loads((changed_out / "manifest.json").read_bytes())
    first_version = first_manifest["identity_descriptor"]["schema_versions"]["demo"]
    second_version = second_manifest["identity_descriptor"]["schema_versions"]["demo"]
    structure_changed = change in {"type", "pk", "pair", "action"}
    assert (first_version != second_version) == structure_changed
    assert (baseline.corpus_id != changed.corpus_id) == (change != "operation")
    first = {row["chunk_key"]: row for row in _rows(baseline_out, "chunks")}
    second = {row["chunk_key"]: row for row in _rows(changed_out, "chunks")}
    assert first.keys() == second.keys()
    for key, row in first.items():
        other = second[key]
        assert (row["chunk_id"] != other["chunk_id"]) == (change != "operation")
        assert (row["content_hash"] != other["content_hash"]) == (
            [row["title"], row["content"]] != [other["title"], other["content"]])
    if change in {"description", "summary", "type", "pk", "pair"}:
        assert any(first[key]["content_hash"] != second[key]["content_hash"] for key in first)
    if change in {"rule", "source_format", "operation"}:
        assert all(first[key]["content_hash"] == second[key]["content_hash"] for key in first)
    if change == "operation":
        record_path = changed_out / "build-run.json"
        record = json.loads(record_path.read_bytes())
        record.update(built_at="2026-01-01T00:00:00.000000Z", elapsed_ms=100.0, git_revision=None, git_dirty=None)
        record_path.write_bytes(json_file(record))
        assert {path: raw for path, raw in _files(changed_out).items() if path != "build-run.json"} == {
            path: raw for path, raw in _files(baseline_out).items() if path != "build-run.json"}
    assert verify_knowledge_corpus(changed_out).corpus_id == changed.corpus_id


def test_source_records_match_csv_after_rehash(tmp_path: Path) -> None:
    """检查记录与实际 CSV 一致。输入：临时包；输出：重算摘要仍拒绝的断言。"""
    bundle, semantic = _inputs(tmp_path)
    csv_path = bundle / "derived_source/demo/database_description/items.csv"
    csv_path.write_bytes(csv_path.read_bytes().replace(b"item identifier", b"forged meaning"))
    enriched_path = bundle / "enriched_catalog.json"
    enriched = json.loads(enriched_path.read_bytes())
    next(source for source in enriched["sources"] if source["source_file"].endswith("items.csv"))[
        "source_sha256"] = digest(csv_path.read_bytes())
    _write_json(enriched_path, enriched)
    _rehash_bundle(bundle)
    with pytest.raises(CorpusError) as exc:
        build_knowledge_corpus(bundle, semantic, tmp_path / "corpus", "demo")
    assert exc.value.error_code == "input_invalid"
    assert not (tmp_path / "corpus").exists()
