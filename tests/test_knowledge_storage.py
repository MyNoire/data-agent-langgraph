"""R2 本地读取、秘密配置和数据库回读投影风险；不连接模型或 PostgreSQL。"""

from copy import deepcopy
from datetime import datetime, timezone, timedelta
from uuid import UUID

import pytest
from pydantic import SecretStr

pytest.importorskip("psycopg", reason="R2 本地测试需 knowledge optional extra")

from test_knowledge_corpus import _inputs
from querypilot.knowledge.knowledge_corpus.api import load_corpus as load_verified_corpus, CorpusError
from querypilot.knowledge.knowledge_corpus._corpus import build_knowledge_corpus
from querypilot.knowledge.knowledge_store._postgres import (
    TABLES, corpus_record, verify_rows, verification_digests, expected_structure,
    _constraint_expression,
)
from querypilot.knowledge.knowledge_store.api import KnowledgeStorageSettings, KnowledgeStorageError


class _SearchCatalog:
    """给设施检查注入catalog行；只承载版本/DDL，不模拟真实BM25评分。"""

    def __init__(self, *, index=None, extension=None, preload="pg_search"):
        """输入：可替换catalog状态；返回：本地只读替身；不连接PG。"""
        from querypilot.knowledge.knowledge_store import _search
        self.index = index if index is not None else {
            "oid": 1, "amname": "paradedb", "indisvalid": True,
            "indisready": True, "indislive": True, "indisunique": False,
            "indnkeyatts": 12, "indnatts": 12, "reloptions": None,
            "predicate": None, "correct_table": True,
            "definition": _search.EXPECTED_INDEX_DEFINITION.format(schema=_search.SEARCH_SCHEMA),
        }
        self.extension = extension if extension is not None else {"extversion": "0.26.4", "nspname": "paradedb"}
        self.preload = preload

    def execute(self, statement, parameters=()):
        """输入：管理器只读语句；返回：对应catalog游标；未知写语句主动拒绝。"""
        from types import SimpleNamespace
        if "shared_preload_libraries" in statement:
            raise AssertionError("reader不能读取管理员专属设置")
        if "server_version_num" in statement:
            row = {"version": 180006, "preload": self.preload}
        elif "pg_extension" in statement:
            row = self.extension
        elif "pg_index" in statement:
            row = self.index
        else:
            raise AssertionError("只允许预期catalog读取")
        return SimpleNamespace(fetchone=lambda: row)


class _PrepareConnection(_SearchCatalog):
    """设施准备分支的窄连接替身；事务回滚只模拟设施状态，不冒充真实PG安装。"""

    def __init__(self, *, extension_present=False, index_present=False, drift_on_create=False):
        """输入：缺失/已有设施；返回：可记录DDL和回滚的连接；无数据库副作用。"""
        super().__init__()
        self.extension_present = extension_present
        self.index_present = index_present
        self.drift_on_create = drift_on_create
        self.statements = []
        self.rolled_back = False

    def transaction(self):
        """输入：当前设施状态；返回：显式事务；失败时恢复创建前状态。"""
        from contextlib import contextmanager

        @contextmanager
        def boundary():
            """进入时保存模拟设施，异常时回滚；不吞掉原始错误。"""
            before = self.extension_present, self.index_present
            try:
                yield
            except BaseException:
                self.extension_present, self.index_present = before
                self.rolled_back = True
                raise
        return boundary()

    def execute(self, statement, parameters=()):
        """输入：真实prepare生成的语句；返回：窄catalog行；记录CREATE并模拟其可见性。"""
        from types import SimpleNamespace
        statement = statement.as_string() if hasattr(statement, "as_string") else statement
        self.statements.append(statement)
        row = None
        if statement.startswith("SET ") or "pg_advisory_xact_lock" in statement:
            pass
        elif "shared_preload_libraries" in statement:
            row = {"value": "pg_search"}
        elif statement == "SELECT 1 FROM pg_extension WHERE extname='pg_search'":
            row = {"present": 1} if self.extension_present else None
        elif "pg_available_extensions" in statement:
            row = {"default_version": "0.26.4"}
        elif statement.startswith("CREATE EXTENSION"):
            self.extension_present = True
        elif statement.lstrip().startswith("CREATE INDEX"):
            self.index_present = True
            if self.drift_on_create:
                self.index["definition"] = self.index["definition"].replace("alias=display_name", "alias=other_name")
        elif statement.startswith("SELECT to_regclass"):
            row = {"index": "existing" if self.index_present else None}
        elif "pg_index" in statement and not self.index_present:
            row = None
        else:
            return super().execute(statement, parameters)
        return SimpleNamespace(fetchone=lambda: row)


def _prepare_test_connection(monkeypatch, connection):
    """输入：窄设施连接；返回：None；替换外层连接/R2检查和摘要，不替换prepare控制流。"""
    from contextlib import contextmanager
    from querypilot.knowledge.knowledge_store import _postgres, _search

    @contextmanager
    def connect(settings, role):
        """只允许prepare使用管理员角色；不创建真实连接。"""
        assert role == "admin"
        yield connection

    monkeypatch.setattr(_postgres, "connection", connect)
    monkeypatch.setattr(_postgres, "transaction_limits", lambda conn, settings: None)
    monkeypatch.setattr(_postgres, "check_database", lambda conn: None)
    monkeypatch.setattr(_postgres, "check_permissions", lambda conn: None)
    monkeypatch.setattr(_search, "_corpus_fingerprint", lambda conn: ("a" * 64, 0))


def test_prepare_search_creates_missing_extension_and_index_then_reuses(monkeypatch):
    """输入：缺扩展/索引的窄替身；返回：实际prepare先applied后reused，DDL只发生一次。"""
    from querypilot.knowledge.knowledge_store import _search
    connection = _PrepareConnection()
    _prepare_test_connection(monkeypatch, connection)
    first = _search.prepare(KnowledgeStorageSettings(_env_file=None))
    assert first.outcome == "applied"
    assert connection.extension_present and connection.index_present
    second = _search.prepare(KnowledgeStorageSettings(_env_file=None))
    assert second.outcome == "reused"
    assert first.search_index_profile == second.search_index_profile
    assert sum(statement.startswith("CREATE EXTENSION") for statement in connection.statements) == 1
    assert sum(statement.lstrip().startswith("CREATE INDEX") for statement in connection.statements) == 1


@pytest.mark.parametrize("extension_present", [False, True])
def test_prepare_search_rolls_back_when_created_index_fails_configuration_check(monkeypatch, extension_present):
    """输入：CREATE后catalog发生受控漂移；返回：prepare拒绝并回滚，不返回成功状态。"""
    from querypilot.knowledge.knowledge_store import _search
    connection = _PrepareConnection(extension_present=extension_present, drift_on_create=True)
    _prepare_test_connection(monkeypatch, connection)
    with pytest.raises(KnowledgeStorageError) as caught:
        _search.prepare(KnowledgeStorageSettings(_env_file=None))
    assert caught.value.error_type == "migration_conflict"
    assert connection.rolled_back
    assert not connection.index_present
    assert connection.extension_present == extension_present


def test_prepare_search_refuses_existing_drift_without_rebuilding(monkeypatch):
    """输入：已存在但配置漂移的设施；返回：拒绝且不删除/重建已有索引。"""
    from querypilot.knowledge.knowledge_store import _search
    connection = _PrepareConnection(extension_present=True, index_present=True)
    connection.index["definition"] = connection.index["definition"].replace("pdb.jieba", "pdb.simple")
    _prepare_test_connection(monkeypatch, connection)
    with pytest.raises(KnowledgeStorageError):
        _search.prepare(KnowledgeStorageSettings(_env_file=None))
    assert connection.index_present
    assert not any(statement.lstrip().startswith(("CREATE", "DROP", "ALTER"))
                   for statement in connection.statements)


@pytest.mark.parametrize("mutation", ["missing", "invalid", "partial", "method", "table", "options", "tokenizer", "field"])
def test_search_facilities_reject_missing_or_drifted_index(mutation):
    """输入：缺索引及影响搜索语义的catalog漂移；返回：明确失败，不调用DDL。"""
    from querypilot.knowledge.knowledge_store import _search
    catalog = _SearchCatalog()
    if mutation == "missing": catalog.index = None
    elif mutation == "invalid": catalog.index["indisvalid"] = False
    elif mutation == "partial": catalog.index["predicate"] = "knowledge_type='column'"
    elif mutation == "method": catalog.index["amname"] = "btree"
    elif mutation == "table": catalog.index["correct_table"] = False
    elif mutation == "options": catalog.index["reloptions"] = ["key_field=chunk_id"]
    elif mutation == "tokenizer": catalog.index["definition"] = catalog.index["definition"].replace("pdb.jieba", "pdb.simple")
    elif mutation == "field": catalog.index["definition"] = catalog.index["definition"].replace("((title)::pdb.jieba),", "")
    with pytest.raises(KnowledgeStorageError) as caught:
        _search._index_profile(catalog)
    assert caught.value.error_type == "migration_conflict"


@pytest.mark.parametrize("mutation", ["missing", "version", "namespace"])
def test_search_facilities_reject_unprepared_extension(mutation):
    """输入：插件缺失、错误版本/Schema；返回：reader失败，不降级或安装。"""
    from querypilot.knowledge.knowledge_store import _search
    catalog = _SearchCatalog()
    if mutation == "missing": catalog.extension = None
    elif mutation == "version": catalog.extension["extversion"] = "0.26.3"
    elif mutation == "namespace": catalog.extension["nspname"] = "public"
    with pytest.raises(KnowledgeStorageError) as caught:
        _search._index_profile(catalog)
    assert caught.value.error_type == "migration_conflict"


def test_search_preload_is_checked_only_through_admin_path():
    """输入：管理员可读参数；返回：准确区分预加载名，reader检查不读取受限设置。"""
    from types import SimpleNamespace
    from querypilot.knowledge.knowledge_store import _search
    _search._index_profile(_SearchCatalog())
    valid = SimpleNamespace(execute=lambda statement: SimpleNamespace(fetchone=lambda: {"value": 'other, "pg_search"'}))
    _search._check_preload(valid)
    invalid = SimpleNamespace(execute=lambda statement: SimpleNamespace(fetchone=lambda: {"value": "other_pg_search"}))
    with pytest.raises(KnowledgeStorageError):
        _search._check_preload(invalid)


def test_search_profile_uses_validated_actual_definition_and_protects_literal_options():
    """输入：同义反解析空白与改变的字符串选项；返回：只忽略引号外空白。"""
    from querypilot.knowledge.knowledge_store import _search
    first = _search._index_profile(_SearchCatalog())
    catalog = _SearchCatalog()
    catalog.index["definition"] = catalog.index["definition"].replace("CREATE INDEX", "CREATE\nINDEX")
    assert _search._index_profile(catalog) == first
    assert _search._definition_key("pdb.jieba('alias=a b')") != _search._definition_key("pdb.jieba('alias=ab')")
    assert first.search_fields["title"] == "jieba"
    assert "chunk_key" not in first.search_fields


def test_search_state_detects_configuration_or_full_corpus_changes(monkeypatch):
    """输入：实际状态与旧配置/全索引摘要；返回：可复核状态，变化时明确失败。"""
    from querypilot.knowledge.knowledge_store import _search
    catalog = _SearchCatalog()
    monkeypatch.setattr(_search, "_corpus_fingerprint", lambda conn: ("a" * 64, 20))
    state = _search.inspect_search(catalog)
    assert state.indexed_chunks == 20
    assert _search.inspect_search(catalog, expected_profile=state.search_index_profile,
                                  expected_fingerprint=state.index_corpus_fingerprint) == state
    with pytest.raises(KnowledgeStorageError):
        _search.inspect_search(catalog, expected_fingerprint="b" * 64)
    profile = state.search_index_profile.model_copy(update={"index_definition_sha256": "c" * 64})
    with pytest.raises(KnowledgeStorageError):
        _search.inspect_search(catalog, expected_profile=profile)


@pytest.mark.parametrize("mode", ["reader", "writer", "writable", "isolation", "closed"])
def test_search_session_requires_live_reader_repeatable_read_transaction(monkeypatch, mode):
    """输入：存储会话的实际角色/事务或关闭状态；返回：仅真reader只读一致事务可检查。"""
    from types import SimpleNamespace
    from querypilot.knowledge.api import (
        ReadSession, ReadView, ReadLimits, KnowledgeScope, KnowledgeReadError,
        Counts, check_search_session,
    )
    from querypilot.knowledge.knowledge_store import _search
    actual = {"role": "querypilot_kb_reader", "read_only": "on", "isolation": "repeatable read"}
    if mode == "writer": actual["role"] = "postgres"
    elif mode == "writable": actual["read_only"] = "off"
    elif mode == "isolation": actual["isolation"] = "read committed"
    connection = SimpleNamespace(execute=lambda statement: SimpleNamespace(fetchone=lambda: actual))
    scope = KnowledgeScope(corpus_id="a" * 64, database_id="demo", schema_version="b" * 64)
    counts = Counts(documents=1, chunks=2, relations=0,
                    chunk_types={"table": 1, "column": 1, "relationship": 0})
    session = ReadSession(connection, ReadView(scope=scope, counts=counts, search_bytes=1), ReadLimits())
    if mode == "closed": session._close()
    calls = []

    def inspect(conn, **kwargs):
        """记录通过实际事务检查后才可发生的设施读取；不执行数据库语句。"""
        calls.append(conn)
        return "checked"

    monkeypatch.setattr(_search, "inspect_search", inspect)
    if mode == "reader":
        assert check_search_session(session) == "checked"
        assert calls == [connection]
    else:
        with pytest.raises(KnowledgeReadError) as caught:
            check_search_session(session)
        assert caught.value.error_type == ("knowledge_state_error" if mode == "closed" else "migration_conflict")
        assert calls == []


@pytest.mark.parametrize("action,entry", [("prepare-search", "prepare_search"), ("check-search", "check_search")])
def test_search_cli_dispatches_public_operation_and_keeps_safe_failure(tmp_path, monkeypatch, capsys, action, entry):
    """输入：设施CLI与公开入口受控失败；返回：明确命令/类别，不泄漏cause。"""
    import json
    from querypilot.entrypoints.cli import main
    from querypilot.entrypoints.commands import knowledge
    calls = []

    def fail(settings, *, run_context=None):
        """记录公开调用并模拟缺设施；异常cause不应进入安全产物。"""
        calls.append(run_context is not None)
        raise KnowledgeStorageError("migration_conflict", "设施未准备") from ValueError("private-raw-SQL")

    monkeypatch.setattr(knowledge, entry, fail)
    assert main(["--artifacts-dir", str(tmp_path / "runs"), "knowledge", "store", action]) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["command"] == action
    assert payload["error_type"] == "migration_conflict"
    assert calls == [True]
    assert "private-raw-SQL" not in captured.out + captured.err


@pytest.fixture
def stored_sample(tmp_path):
    """输入：pytest 临时目录；输出：有效源/模拟实际回读；只写原创临时语料。"""
    curated, semantic = _inputs(tmp_path)
    root = tmp_path / "corpus"
    build_knowledge_corpus(curated, semantic, root, "demo")
    corpus = load_verified_corpus(root)
    moment = datetime(2026, 10, 14, 1, 2, 3, 456789, tzinfo=timezone.utc)
    record = {**corpus_record(corpus), "imported_at": moment}
    rows = {}
    for group in TABLES:
        rows[group] = []
        for source in getattr(corpus, group):
            row = source.model_dump(mode="json")
            for field in list(row):
                if field.endswith("_id") and field != "database_id" and field != "corpus_id" and row[field]:
                    row[field] = UUID(row[field])
            row["created_at"] = moment
            rows[group].append(row)
    return corpus, record, rows, root


def test_verified_reader_holds_objects_and_rejects_changed_files(stored_sample):
    """输入：已核验源；输出：原持有对象不变，新读取拒绝磁盘污染；只改临时目录。"""
    corpus, record, rows, root = stored_sample
    before = verify_rows(corpus, record, rows)
    (root / "chunks.jsonl").write_text("{}\n", encoding="utf-8")
    assert verify_rows(corpus, record, rows) == before
    with pytest.raises(CorpusError):
        load_verified_corpus(root)


def test_digests_preserve_ordered_keys_and_ignore_session_representation(stored_sample):
    """输入：实际字段投影；输出：返回行顺序、时区和操作元数据不影响指纹；无 I/O。"""
    corpus, record, rows, _ = stored_sample
    first = verify_rows(corpus, record, rows)
    other = deepcopy(rows)
    zone = timezone(timedelta(hours=8))
    for group in other:
        other[group].reverse()
        for row in other[group]:
            row["created_at"] = row["created_at"].astimezone(zone)
    record = {**record, "imported_at": record["imported_at"].astimezone(zone), "elapsed_ms": 999, "import_run_id": "different"}
    assert verify_rows(corpus, record, other) == first


@pytest.mark.parametrize("mutation", ["content", "metadata", "pair", "time", "missing", "extra", "registry"])
def test_projection_changes_or_target_conflicts_are_not_hidden(stored_sample, mutation):
    """输入：不同污染类别；输出：指纹分离正确且复验拒绝污染；无数据库副作用。"""
    corpus, record, rows, _ = stored_sample
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
    elif mutation == "registry": record["manifest"]["counts"]["chunks"] += 1
    if mutation in {"content", "metadata", "pair", "registry"}:
        after = verification_digests(record, rows)
        assert after.projection_digest != before.projection_digest
        assert after.timestamps_digest == before.timestamps_digest
    with pytest.raises(KnowledgeStorageError, match="不一致"):
        verify_rows(corpus, record, rows)


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
    assert set(expected) == {"schema_migrations", "knowledge_corpora", *TABLES.values(), "chunk_embeddings"}
    # 002 删掉的列和约束不再出现在预期结构里。
    assert not {"embedding", "embedding_profile_id", "embedding_dimensions"} & {c[0] for c in expected["knowledge_chunks"]["columns"]}
    assert "chunks_embedding" not in expected["knowledge_chunks"]["constraints"]
    assert _constraint_expression("CHECK ((status = 'stored'::text))") == _constraint_expression("CHECK (status = 'stored')")
    assert _constraint_expression("CHECK (a AND (b OR c))") != _constraint_expression("CHECK ((a AND b) OR c)")
    assert _constraint_expression("CHECK (public.vector_norm(embedding) > 0::double precision)") == _constraint_expression("CHECK (public.vector_norm(embedding) > 0)")
    assert _constraint_expression("CHECK (public.vector_norm(embedding) > 1::double precision)") != _constraint_expression("CHECK (public.vector_norm(embedding) > 0)")
    assert _constraint_expression("CHECK (amount > 0::double precision)") != _constraint_expression("CHECK (amount > 0)")


def test_cli_bad_source_and_existing_receipt_never_call_database(tmp_path, monkeypatch, capsys):
    """输入：错误源/占用运行根；返回：来源失败或产物失败且无数据库调用；只写临时文件。"""
    import json
    from querypilot.entrypoints.cli import main
    from querypilot.knowledge.knowledge_store import api as store

    def unexpected(*args, **kwargs):
        """输入：任意数据库调用；输出：主动失败，证明前置校验没有越过边界。"""
        raise AssertionError("must not connect")
    monkeypatch.setattr(store, "_driver", unexpected)
    assert main(["--artifacts-dir", str(tmp_path / "runs"), "knowledge", "store", "import",
                 "--corpus-dir", str(tmp_path / "missing")]) == 1
    assert json.loads(capsys.readouterr().out)["error_type"] == "source_invalid"
    target = tmp_path / "existing.json"
    target.write_text("preserved", encoding="utf-8")
    assert main(["--artifacts-dir", str(target), "knowledge", "store", "import",
                 "--corpus-dir", str(tmp_path / "missing")]) == 4
    assert target.read_text(encoding="utf-8") == "preserved"
    assert json.loads(capsys.readouterr().out)["database_outcome"] == "not_started"


def test_receipt_failure_keeps_known_database_outcome(stored_sample, monkeypatch, capsys, tmp_path):
    """输入：成功数据库摘要/结果发布故障；返回：退出4且imported保留，秘密不出现在记录。"""
    import json
    from querypilot.entrypoints.cli import main
    from querypilot.entrypoints.commands import knowledge
    from querypilot.runtime import _artifacts
    from querypilot.knowledge.knowledge_store._importer import _summary
    corpus, record, rows, root = stored_sample
    result = _summary(corpus, verify_rows(corpus, record, rows), "unit", __import__("time").perf_counter(), "imported")
    monkeypatch.setattr(knowledge, "import_corpus", lambda *a, **k: result)
    original_write = _artifacts._write_json

    def fail_result(path, payload, **kwargs):
        """输入：固定产物路径；返回：只在结果发布时模拟私有I/O错误，其他记录仍可写。"""
        if path.name == "result.json":
            raise OSError("private-password raw knowledge")
        return original_write(path, payload, **kwargs)
    monkeypatch.setattr(_artifacts, "_write_json", fail_result)
    output_root = tmp_path / "evidence"
    assert main(["--artifacts-dir", str(output_root), "knowledge", "store", "import",
                 "--corpus-dir", str(root)]) == 4
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["error_type"] == "artifact_write_failed"
    assert payload["database_outcome"] == "imported"
    assert "private-password" not in captured.out + captured.err
    run_file, = output_root.rglob("run.json")
    final = json.loads(run_file.read_text(encoding="utf-8"))
    assert final["operation_outcome"] == "imported"
    assert final["artifact_outcome"] == "failed"
    assert not (run_file.parent / "result.json").exists()
    events = [json.loads(line) for line in (run_file.parent / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events[-1]["event"] == "run_failed"
    assert events[-1]["error_type"] == "RunArtifactError"
    assert "private-password" not in str(events)


def test_atomic_receipt_refuses_overwrite(tmp_path):
    """输入：同目标两次发布；返回：首次完整、第二次排他失败且旧内容不变。"""
    from querypilot.runtime.api import publish_json
    target = tmp_path / "receipt.json"
    publish_json(target, {"outcome": "verified"})
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        publish_json(target, {"outcome": "different"})
    assert target.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("category,expected", [("configuration_error", 1), ("connection_failed", 3)])
def test_store_cli_keeps_protocol_and_service_exit_codes(tmp_path, monkeypatch, capsys, category, expected):
    """输入：管理入口受控失败；返回：协议1/服务3与安全JSON；不连接或输出秘密。"""
    import json
    from querypilot.entrypoints.cli import main
    from querypilot.entrypoints.commands import knowledge

    def fail(*args, **kwargs):
        """抛受控存储错误；底层秘密只能保留cause，默认输出不打印。"""
        raise KnowledgeStorageError(category, "安全分类") from OSError("private-password")
    monkeypatch.setattr(knowledge, "manage_store", fail)
    assert main(["--artifacts-dir", str(tmp_path / "artifacts"), "knowledge", "store", "check"]) == expected
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error_type"] == category
    assert "private-password" not in captured.out + captured.err


def test_commit_confirmation_loss_is_unknown_and_private(stored_sample, monkeypatch):
    """输入：窄 COMMIT 断连注入；输出：unknown/cause 保留；不冒充真实网络实验。"""
    from contextlib import contextmanager
    import psycopg
    from querypilot.knowledge.knowledge_store import _postgres as postgres, _importer as importer
    corpus, record, rows, _ = stored_sample

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
    monkeypatch.setattr(postgres, "read_corpus", lambda *a: (record, rows))
    with pytest.raises(KnowledgeStorageError) as caught:
        importer.import_verified_corpus(corpus, KnowledgeStorageSettings(_env_file=None))
    assert caught.value.error_type == "commit_outcome_unknown"
    assert isinstance(caught.value.__cause__, psycopg.OperationalError)
    assert "private-password" not in str(caught.value)
