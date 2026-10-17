"""R2 本地读取、秘密配置和数据库回读投影风险；不连接模型或 PostgreSQL。"""

from copy import deepcopy
from datetime import datetime, timezone, timedelta
from uuid import UUID

import pytest
from pydantic import SecretStr

pytest.importorskip("psycopg", reason="R2 本地测试需 knowledge optional extra")

from test_knowledge_corpus import _inputs
from querypilot.knowledge.corpus import build_knowledge_corpus, load_verified_corpus, CorpusError
from querypilot.knowledge.postgres import (
    TABLES, corpus_record, verify_rows, verification_digests, expected_structure,
    _constraint_expression,
)
from querypilot.knowledge.storage_config import KnowledgeStorageSettings, KnowledgeStorageError


@pytest.fixture
def stored_sample(tmp_path):
    """输入：pytest 临时目录；输出：有效源/模拟实际回读；只写原创临时快照。"""
    bundle, semantic = _inputs(tmp_path)
    root = tmp_path / "snapshot"
    build_knowledge_corpus(bundle, semantic, root, "demo")
    snapshot = load_verified_corpus(root)
    moment = datetime(2026, 10, 14, 1, 2, 3, 456789, tzinfo=timezone.utc)
    record = {**corpus_record(snapshot), "imported_at": moment}
    rows = {}
    for group in TABLES:
        rows[group] = []
        for source in getattr(snapshot, group):
            row = source.model_dump(mode="json")
            for field in list(row):
                if field.endswith("_id") and field != "database_id" and field != "corpus_id" and row[field]:
                    row[field] = UUID(row[field])
            row["created_at"] = moment
            if group == "chunks":
                row.update(embedding=None, embedding_profile_id=None, embedding_dimensions=None)
            rows[group].append(row)
    return snapshot, record, rows, root


def test_verified_reader_holds_objects_and_rejects_changed_files(stored_sample):
    """输入：已核验源；输出：原持有对象不变，新读取拒绝磁盘污染；只改临时目录。"""
    snapshot, record, rows, root = stored_sample
    before = verify_rows(snapshot, record, rows)
    (root / "chunks.jsonl").write_text("{}\n", encoding="utf-8")
    assert verify_rows(snapshot, record, rows) == before
    with pytest.raises(CorpusError):
        load_verified_corpus(root)


def test_digests_preserve_ordered_keys_and_ignore_session_representation(stored_sample):
    """输入：实际字段投影；输出：返回行顺序、时区和操作元数据不影响指纹；无 I/O。"""
    snapshot, record, rows, _ = stored_sample
    first = verify_rows(snapshot, record, rows)
    other = deepcopy(rows)
    zone = timezone(timedelta(hours=8))
    for group in other:
        other[group].reverse()
        for row in other[group]:
            row["created_at"] = row["created_at"].astimezone(zone)
    record = {**record, "imported_at": record["imported_at"].astimezone(zone), "elapsed_ms": 999, "import_run_id": "different"}
    assert verify_rows(snapshot, record, other) == first


@pytest.mark.parametrize("mutation", ["content", "metadata", "pair", "time", "missing", "extra", "embedding", "registry"])
def test_projection_changes_or_target_conflicts_are_not_hidden(stored_sample, mutation):
    """输入：不同污染类别；输出：指纹分离正确且复验拒绝污染；无数据库副作用。"""
    snapshot, record, rows, _ = stored_sample
    before = verification_digests(record, rows)
    if mutation == "time":
        rows["documents"][0]["created_at"] += timedelta(seconds=1)
        after = verification_digests(record, rows)
        assert after.projection_digest == before.projection_digest
        assert after.timestamps_digest != before.timestamps_digest
        return
    if mutation == "content": rows["documents"][0]["content"] += " changed"
    elif mutation == "metadata": rows["documents"][0]["metadata"]["extra"] = "changed"
    elif mutation == "pair": rows["relations"][0]["source_columns"].reverse()
    elif mutation == "missing": rows["chunks"].pop()
    elif mutation == "extra": rows["chunks"].append(deepcopy(rows["chunks"][0]))
    elif mutation == "embedding": rows["chunks"][0]["embedding"] = "[1,2]"
    elif mutation == "registry": record["manifest"]["counts"]["chunks"] += 1
    if mutation in {"content", "metadata", "pair", "registry"}:
        after = verification_digests(record, rows)
        assert after.projection_digest != before.projection_digest
        assert after.timestamps_digest == before.timestamps_digest
    with pytest.raises(KnowledgeStorageError, match="不一致|未验收"):
        verify_rows(snapshot, record, rows)


def test_config_is_optional_and_private_and_roles_are_explicit():
    """输入：本机无 DSN/不合法 DSN；输出：安全受控错误且秘密 repr 隐藏；无连接。"""
    settings = KnowledgeStorageSettings(_env_file=None)
    with pytest.raises(KnowledgeStorageError): settings.connection_info("read")
    secret = "postgresql://querypilot_kb_reader:private-password@localhost/querypilot_knowledge_test"
    settings = KnowledgeStorageSettings(_env_file=None, read_dsn=SecretStr(secret))
    assert "private-password" not in repr(settings)
    assert settings.connection_info("read")["user"] == "querypilot_kb_reader"
    settings.read_dsn = SecretStr("invalid-private-password")
    with pytest.raises(KnowledgeStorageError) as caught:
        settings.connection_info("read")
    assert "private-password" not in str(caught.value)


def test_structure_checker_compares_expression_semantics():
    """输入：可信迁移与 PG 常见反解析表达；输出：忽略冗余 cast，但区分逻辑改变。"""
    expected = expected_structure()
    assert set(expected) == {"schema_migrations", "knowledge_corpora", *TABLES.values()}
    assert _constraint_expression("CHECK ((status = 'stored'::text))") == _constraint_expression("CHECK (status = 'stored')")
    assert _constraint_expression("CHECK (a AND (b OR c))") != _constraint_expression("CHECK ((a AND b) OR c)")
    assert _constraint_expression("CHECK (public.vector_norm(embedding) > 0::double precision)") == _constraint_expression("CHECK (public.vector_norm(embedding) > 0)")
    assert _constraint_expression("CHECK (public.vector_norm(embedding) > 1::double precision)") != _constraint_expression("CHECK (public.vector_norm(embedding) > 0)")
    assert _constraint_expression("CHECK (amount > 0::double precision)") != _constraint_expression("CHECK (amount > 0)")


def test_cli_bad_source_and_existing_receipt_never_call_database(tmp_path, monkeypatch, capsys):
    """输入：错误源/已存回执；输出：安全失败且数据库不调用；仅写临时文件。"""
    from querypilot.knowledge import storage_cli

    def unexpected(*args, **kwargs):
        """输入：任意数据库调用；输出：主动失败，证明前置校验没有越过边界。"""
        raise AssertionError("must not connect")
    monkeypatch.setattr(storage_cli, "import_verified_corpus", unexpected)
    assert storage_cli.run_cli("import", ["--corpus-dir", str(tmp_path / "missing")]) == 1
    assert __import__("json").loads(capsys.readouterr().out)["error_type"] == "source_invalid"
    target = tmp_path / "existing.json"
    target.write_text("preserved", encoding="utf-8")
    assert storage_cli.run_cli("import", ["--corpus-dir", str(tmp_path / "missing"), "--output", str(target)]) == 1
    assert target.read_text(encoding="utf-8") == "preserved"
    assert __import__("json").loads(capsys.readouterr().out)["database_outcome"] == "not_started"


def test_receipt_failure_keeps_known_database_outcome(stored_sample, monkeypatch, capsys, tmp_path):
    """输入：成功数据库摘要/回执故障；输出：已知 imported 保留，秘密不出现在默认输出。"""
    from querypilot.knowledge import storage_cli
    from querypilot.knowledge.importer import _summary
    snapshot, record, rows, root = stored_sample
    result = _summary(snapshot, verify_rows(snapshot, record, rows), "unit", __import__("time").perf_counter(), "imported")
    monkeypatch.setattr(storage_cli, "import_verified_corpus", lambda *a, **k: result)

    def fail(*args):
        """输入：回执参数；输出：模拟私有 I/O 错误；不触碰实际数据库。"""
        raise OSError("private-password raw knowledge")
    monkeypatch.setattr(storage_cli, "write_receipt", fail)
    output = tmp_path / "evidence/result.json"
    assert storage_cli.run_cli("import", ["--corpus-dir", str(root), "--output", str(output)]) == 1
    captured = capsys.readouterr()
    payload = __import__("json").loads(captured.out)
    assert payload["error_type"] == "receipt_write_failed"
    assert payload["database_outcome"] == "imported"
    assert "private-password" not in captured.out + captured.err
    assert not output.exists()
    events = [__import__("json").loads(line) for line in output.with_suffix(".events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events[-1]["event"] == "knowledge_operation_failed"
    assert events[-1]["error_type"] == "receipt_write_failed"
    assert "private-password" not in str(events)


def test_atomic_receipt_refuses_overwrite(tmp_path):
    """输入：同目标两次写出；输出：首次完整，第二次排他失败且旧内容不变。"""
    from querypilot.knowledge.storage_cli import write_receipt
    target = tmp_path / "receipt.json"
    write_receipt(target, {"outcome": "verified"})
    before = target.read_bytes()
    with pytest.raises(FileExistsError): write_receipt(target, {"outcome": "different"})
    assert target.read_bytes() == before
    assert not list(tmp_path.glob(".receipt-*.tmp"))


def test_commit_confirmation_loss_is_unknown_and_private(stored_sample, monkeypatch):
    """输入：窄 COMMIT 断连注入；输出：unknown/cause 保留；不冒充真实网络实验。"""
    from contextlib import contextmanager
    import psycopg
    from querypilot.knowledge import postgres, importer
    snapshot, record, rows, _ = stored_sample

    class Transaction:
        """提交退出时注入连接异常。"""
        def __enter__(self):
            """输入：自身；输出：自身；无副作用。"""
            return self
        def __exit__(self, *args):
            """输入：退出信息；输出：无；模拟提交确认丢失。"""
            raise psycopg.OperationalError("private-password")

    class Connection:
        """只支持本例需要的事务/执行接口。"""
        def transaction(self):
            """输入：自身；输出：注入事务；无数据库连接。"""
            return Transaction()
        def execute(self, *args):
            """输入：可信语句参数；输出：None；无副作用。"""
            return None

    @contextmanager
    def connect(*args):
        """输入：配置/角色；输出：模拟连接；无网络。"""
        yield Connection()
    monkeypatch.setattr(postgres, "connection", connect)
    monkeypatch.setattr(postgres, "check_database", lambda c: None)
    monkeypatch.setattr(postgres, "read_snapshot", lambda *a: (record, rows))
    with pytest.raises(KnowledgeStorageError) as caught:
        importer.import_verified_corpus(snapshot, KnowledgeStorageSettings(_env_file=None))
    assert caught.value.error_type == "commit_outcome_unknown"
    assert isinstance(caught.value.__cause__, psycopg.OperationalError)
    assert "private-password" not in str(caught.value)
