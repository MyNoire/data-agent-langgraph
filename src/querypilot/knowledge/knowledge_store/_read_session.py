"""同一只读事务中的范围前置与有界读取端口；不拥有检索策略。"""

from contextlib import contextmanager
import json
import sys
from typing import Any, Iterator
from uuid import uuid4

from pydantic import ValidationError

from querypilot.knowledge.knowledge_corpus.api import CorpusManifest, Counts, ValidationReport
from .settings import KnowledgeStorageError, KnowledgeStorageSettings
from .models import (
    KnowledgeScope, KnowledgeReadError, ReadLimits, ReadView, STORAGE_VERSION,
    SearchIndexProfile, SearchIndexState,
)


def _read_registration(conn: Any, scope: KnowledgeScope, limits: ReadLimits) -> Counts:
    """读取有界登记并核对三元范围、清单和报告。

    参数：conn 为当前只读一致事务；scope 为严格范围。
    返回：三份记录一致的 Counts。
    异常：KnowledgeReadError，区分未知、错配、容量与坏状态；原 cause 不输出。
    副作用：只读 PG；不读取正文或完整对象集，不补写登记。
    """
    info = conn.execute("""
        SELECT database_id, schema_version,
               octet_length(manifest::text) + octet_length(validation::text)
                 + octet_length(counts::text) AS registration_bytes
        FROM querypilot_kb.knowledge_corpora WHERE corpus_id = %s
    """, (scope.corpus_id,)).fetchone()
    if info is None:
        raise KnowledgeReadError("scope_not_found")
    if (info["database_id"], info["schema_version"]) != (scope.database_id, scope.schema_version):
        raise KnowledgeReadError("scope_mismatch")
    # 防止污染后的巨大 JSON 登记绕过传输边界；与命中结果分别检查。
    if info["registration_bytes"] > limits.max_result_bytes:
        raise KnowledgeReadError("capacity_exceeded")
    record = conn.execute("""
        SELECT corpus_id, database_id, schema_version, core_digest, status,
               storage_contract_version, manifest, validation, counts
        FROM querypilot_kb.knowledge_corpora WHERE corpus_id = %s
    """, (scope.corpus_id,)).fetchone()
    try:
        manifest = CorpusManifest.model_validate(record["manifest"])
        validation = ValidationReport.model_validate(record["validation"])
        counts = Counts.model_validate(record["counts"])
    except ValidationError as exc:
        raise KnowledgeReadError("knowledge_state_error") from exc
    descriptor = manifest.identity_descriptor
    if (record["status"] != "stored" or record["storage_contract_version"] != STORAGE_VERSION
            or manifest.corpus_id != scope.corpus_id or validation.corpus_id != scope.corpus_id
            or descriptor.database_scope != [scope.database_id]
            or descriptor.schema_versions != {scope.database_id: scope.schema_version}
            or record["core_digest"] != manifest.core_digest
            or record["core_digest"] != validation.core_digest
            or counts != manifest.counts or counts != validation.counts):
        raise KnowledgeReadError("knowledge_state_error")
    return counts


def _check_objects(conn: Any, scope: KnowledgeScope, expected: Counts, limits: ReadLimits) -> int:
    """核对完整范围的数量、引用与搜索字节，不先按类型或 K 截断。

    参数：conn 为只读一致事务；scope/expected 为已核验登记。
    返回：白名单搜索字段的 UTF-8 总字节数。
    异常：KnowledgeReadError，表示坏状态或容量超限。
    副作用：只读固定聚合和 EXISTS 查询，不传回正文、metadata 或向量。
    """
    actual: dict[str, int] = {}
    # 表名只来自源码常量；用户提供的范围始终绑定为值。
    for group, table in (("documents", "knowledge_documents"), ("relations", "schema_relations")):
        row = conn.execute(f"""
            SELECT count(*) AS count,
                   count(*) FILTER (WHERE database_id <> %s OR schema_version <> %s) AS invalid
            FROM querypilot_kb.{table} WHERE corpus_id = %s
        """, (scope.database_id, scope.schema_version, scope.corpus_id)).fetchone()
        if row["invalid"]:
            raise KnowledgeReadError("knowledge_state_error")
        actual[group] = row["count"]
    row = conn.execute("""
        SELECT count(*) AS count,
            count(*) FILTER (WHERE knowledge_type='table') AS table_count,
            count(*) FILTER (WHERE knowledge_type='column') AS column_count,
            count(*) FILTER (WHERE knowledge_type='relationship') AS relationship_count,
            count(*) FILTER (WHERE database_id <> %s OR schema_version <> %s) AS invalid,
            coalesce(sum(octet_length(convert_to(title,'UTF8'))
                + octet_length(convert_to(content,'UTF8'))
                + octet_length(convert_to(coalesce(schema_name,''),'UTF8'))
                + octet_length(convert_to(coalesce(table_name,''),'UTF8'))
                + octet_length(convert_to(coalesce(column_name,''),'UTF8'))
                + CASE WHEN jsonb_typeof(metadata->'display_name')='string'
                    THEN octet_length(convert_to(metadata->>'display_name','UTF8')) ELSE 0 END),0)::bigint AS search_bytes
        FROM querypilot_kb.knowledge_chunks WHERE corpus_id=%s
    """, (scope.database_id, scope.schema_version, scope.corpus_id)).fetchone()
    actual["chunks"] = row["count"]
    if (row["invalid"] or any(actual[name] != getattr(expected, name) for name in actual)
            or {kind: row[kind + "_count"] for kind in ("table", "column", "relationship")}
            != expected.chunk_types.model_dump()):
        raise KnowledgeReadError("knowledge_state_error")
    if row["count"] > limits.max_scope_chunks or row["search_bytes"] > limits.max_search_bytes:
        raise KnowledgeReadError("capacity_exceeded")
    bad = conn.execute("""
        SELECT EXISTS (
          SELECT 1 FROM querypilot_kb.knowledge_chunks c
          LEFT JOIN querypilot_kb.knowledge_documents d
            ON (d.document_id,d.document_key,d.corpus_id,d.database_id,d.schema_version)
             = (c.document_id,c.document_key,c.corpus_id,c.database_id,c.schema_version)
          LEFT JOIN querypilot_kb.schema_relations r
            ON (r.relation_id,r.relation_key,r.source_document_id,r.source_document_key,
                r.corpus_id,r.database_id,r.schema_version)
             = (c.relation_id,c.relation_key,c.document_id,c.document_key,
                c.corpus_id,c.database_id,c.schema_version)
          WHERE c.corpus_id=%s AND (d.document_id IS NULL
             OR (c.knowledge_type='relationship' AND r.relation_id IS NULL))
        ) OR EXISTS (
          SELECT 1 FROM querypilot_kb.schema_relations r
          LEFT JOIN querypilot_kb.knowledge_documents d
            ON (d.document_id,d.document_key,d.corpus_id,d.database_id,d.schema_version)
             = (r.source_document_id,r.source_document_key,r.corpus_id,r.database_id,r.schema_version)
          WHERE r.corpus_id=%s AND d.document_id IS NULL
        ) AS invalid
    """, (scope.corpus_id, scope.corpus_id)).fetchone()
    if bad["invalid"]:
        raise KnowledgeReadError("knowledge_state_error")
    return row["search_bytes"]


class ReadSession:
    """事务内读取对象；真实连接私有，关闭后拒绝继续使用。"""

    def __init__(self, connection: Any, view: ReadView, limits: ReadLimits):
        """输入：私有连接和已核验视图；返回：会话；不额外执行 SQL。"""
        self._connection = connection
        self._view = view
        self._limits = limits
        self._active = True

    @property
    def scope(self) -> KnowledgeScope:
        """返回：当前固定范围；不执行 I/O。"""
        return self._view.scope

    @property
    def counts(self) -> Counts:
        """返回：登记与实际数量核对结果；不执行 I/O。"""
        return self._view.counts

    @property
    def search_bytes(self) -> int:
        """返回：数据库计算的搜索内容字节数；不读取正文。"""
        return self._view.search_bytes

    def _cursor(self, source_sql: str, bound_parameters):
        """输入：受控源码SQL和绑定值；返回：私有游标；关闭后抛受控错误。"""
        if not self._active:
            raise KnowledgeReadError("knowledge_state_error")
        cursor = self._connection.cursor(name="qp_read_" + uuid4().hex)
        try:
            cursor.execute(source_sql, bound_parameters)
        except BaseException:
            try:
                cursor.close()
            except Exception:
                # 清理失败不能替换原SQL/连接异常；外层仍关闭整个事务与连接。
                pass
            raise
        return cursor

    def fetch_one(self, source_sql: str, bound_parameters=()) -> dict | None:
        """输入：可信只读语句和绑定值；返回：有界单行映射；错误保留外层分类。"""
        return next(iter(self._fetch_rows(source_sql, bound_parameters, self._limits.max_result_bytes,
                                         row_limit=1)), None)

    def fetch_rows(self, source_sql: str, bound_parameters=(), byte_limit: int | None = None) -> list[dict]:
        """有界读取可信源码查询。

        参数：source_sql 只来自项目代码，用户内容必须作为 bound_parameters；byte_limit 不得高于结果预算。
        返回：普通字典列表，无连接或游标；逐行计数和字节检查，不先 fetchall。
        异常：KnowledgeReadError(capacity_exceeded/knowledge_state_error)；PG错误由会话边界转换。
        副作用：同一只读事务内执行查询；范围条件由源码查询显式绑定，不自动添加授权。
        """
        return self._fetch_rows(source_sql, bound_parameters, byte_limit)

    def _fetch_rows(self, source_sql: str, bound_parameters, byte_limit: int | None,
                    *, row_limit: int | None = None) -> list[dict]:
        """输入：受控查询与内部行数限制；返回：有界行集合；负责关闭本次命名游标。"""
        budget = self._limits.max_result_bytes if byte_limit is None else byte_limit
        if isinstance(budget, bool) or not isinstance(budget, int) or not 0 < budget <= self._limits.max_result_bytes:
            raise KnowledgeReadError("capacity_exceeded")
        cursor = self._cursor(source_sql, bound_parameters)
        rows, size = [], 2
        try:
            while (row := cursor.fetchone()) is not None:
                item = dict(row)
                size += len(json.dumps(item, ensure_ascii=False, default=str).encode("utf-8")) + (2 if rows else 0)
                if size > budget or len(rows) >= self._limits.max_scope_chunks:
                    raise KnowledgeReadError("capacity_exceeded")
                rows.append(item)
                if row_limit is not None and len(rows) >= row_limit:
                    break
        finally:
            primary = sys.exception()
            try:
                cursor.close()
            except Exception:
                if primary is None:
                    raise
        return rows

    def _close(self) -> None:
        """使端口失效；底层连接由外层上下文关闭，不允许调用方提交。"""
        self._active = False


def check_search_session(session: ReadSession, *, expected_profile: SearchIndexProfile | None = None,
                         expected_fingerprint: str | None = None) -> SearchIndexState:
    """在已打开的只读范围事务中核对搜索设施和实际全索引语料。

    参数：session来自open_read_scope；可选期望值用于固定同批评测条件。
    返回：当前实际SearchIndexState，不含知识正文。
    异常：KnowledgeReadError(migration_conflict/knowledge_state_error)；不重新连接或改变角色。
    副作用：同一只读事务中只读catalog和全库行摘要，不安装、修复、写库或增权。
    """
    if not isinstance(session, ReadSession) or not session._active:
        raise KnowledgeReadError("knowledge_state_error")
    from ._search import inspect_search
    try:
        actual = session._connection.execute("SELECT current_user AS role, "
            "current_setting('transaction_read_only') AS read_only, "
            "current_setting('transaction_isolation') AS isolation").fetchone()
        if (actual["role"] != "querypilot_kb_reader" or actual["read_only"] != "on"
                or actual["isolation"] != "repeatable read"):
            raise KnowledgeReadError("migration_conflict")
        return inspect_search(session._connection, expected_profile=expected_profile,
                              expected_fingerprint=expected_fingerprint)
    except KnowledgeStorageError as exc:
        raise KnowledgeReadError("migration_conflict") from exc


@contextmanager
def open_read_scope(scope: KnowledgeScope, settings: KnowledgeStorageSettings,
                    limits: ReadLimits | None = None) -> Iterator[ReadSession]:
    """输入：明确三元范围、配置和预算；返回：同一只读事务会话；退出关闭所有资源。

    异常：KnowledgeReadError，原底层 cause 保留；延迟加载可选驱动，无本地兜底。
    """
    limits = limits or ReadLimits()
    try:
        from . import _postgres as postgres
    except ImportError as exc:
        raise KnowledgeReadError("configuration_error") from exc
    try:
        with postgres.connection(settings, "read") as conn, conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            postgres.transaction_limits(conn, settings)
            postgres.check_database(conn)
            counts = _read_registration(conn, scope, limits)
            search_bytes = _check_objects(conn, scope, counts, limits)
            session = ReadSession(conn, ReadView(scope=scope, counts=counts, search_bytes=search_bytes), limits)
            try:
                yield session
            finally:
                session._close()
    except KnowledgeStorageError as exc:
        category = exc.error_type if exc.error_type in {
            "configuration_error", "migration_conflict", "connection_failed", "permission_denied",
            "lock_timeout", "statement_timeout", "database_error",
        } else "database_error"
        raise KnowledgeReadError(category) from exc
