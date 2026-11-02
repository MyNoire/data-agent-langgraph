"""管理员准备和只读核对搜索设施；知识原字段及001迁移保持独立。"""

from hashlib import sha256
from typing import Any
from uuid import uuid4

from psycopg import sql

from .models import SearchIndexProfile, SearchIndexState, SearchManagementSummary
from .settings import KnowledgeStorageError, KnowledgeStorageSettings

# 生产设施的固定目标；不接受CLI/环境/请求传入的任意Schema。
SEARCH_SCHEMA = "querypilot_kb"
SEARCH_INDEX = "knowledge_chunks_bm25_idx"
SEARCH_EXTENSION_VERSION = "0.26.4"
SEARCH_FIELDS = {
    "title": "jieba", "content": "jieba", "schema_name": "simple",
    "table_name": "simple", "column_name": "simple", "display_name": "jieba",
}
FILTER_FIELDS = ("chunk_key", "corpus_id", "database_id", "schema_version", "knowledge_type")

CREATE_SEARCH_INDEX = """
CREATE INDEX knowledge_chunks_bm25_idx
ON {schema}.knowledge_chunks USING paradedb (
    chunk_id,
    (chunk_key::pdb.literal), (corpus_id::pdb.literal),
    (database_id::pdb.literal), (schema_version::pdb.literal),
    (knowledge_type::pdb.literal),
    (title::pdb.jieba), (content::pdb.jieba),
    (schema_name::pdb.simple), (table_name::pdb.simple), (column_name::pdb.simple),
    ((CASE WHEN jsonb_typeof(metadata->'display_name') = 'string'
       THEN metadata->>'display_name' END)::pdb.jieba('alias=display_name'))
)
"""

# 由已固定PG18/pg_search0.26.4的pg_get_indexdef产生；只忽略引号外空白，
# 不能删除括号或字符串内容，否则不同表达式可能被错误视为同一索引。
EXPECTED_INDEX_DEFINITION = """
CREATE INDEX knowledge_chunks_bm25_idx ON {schema}.knowledge_chunks USING paradedb
(chunk_id, ((chunk_key)::pdb.literal), ((corpus_id)::pdb.literal),
 ((database_id)::pdb.literal), ((schema_version)::pdb.literal),
 ((knowledge_type)::pdb.literal), ((title)::pdb.jieba), ((content)::pdb.jieba),
 ((schema_name)::pdb.simple), ((table_name)::pdb.simple), ((column_name)::pdb.simple),
 ((CASE WHEN (jsonb_typeof((metadata -> 'display_name'::text)) = 'string'::text)
 THEN (metadata ->> 'display_name'::text) ELSE NULL::text END)::pdb.jieba('alias=display_name')))
"""

INDEX_METADATA_SQL = """
SELECT c.oid, a.amname, i.indisvalid, i.indisready, i.indislive, i.indisunique,
       i.indnkeyatts, i.indnatts, c.reloptions,
       pg_get_expr(i.indpred,i.indrelid) AS predicate,
       i.indrelid = to_regclass(%s) AS correct_table,
       pg_get_indexdef(c.oid) AS definition
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
JOIN pg_index i ON i.indexrelid=c.oid JOIN pg_am a ON a.oid=c.relam
WHERE n.nspname=%s AND c.relname=%s
"""

CORPUS_FINGERPRINT_SQL = """
SELECT sha256(convert_to(jsonb_build_array(
    chunk_id::text, chunk_key, corpus_id, database_id, schema_version, knowledge_type,
    title, content, schema_name, table_name, column_name,
    CASE WHEN jsonb_typeof(metadata->'display_name')='string'
         THEN metadata->>'display_name' END
)::text, 'UTF8')) AS row_sha256
FROM {schema}.knowledge_chunks ORDER BY chunk_id
"""


def _conflict(message: str) -> KnowledgeStorageError:
    """输入：受控说明；返回：设施未准备或漂移错误；不含数据库原始异常。"""
    return KnowledgeStorageError("migration_conflict", message)


def _definition_key(definition: str) -> str:
    """输入：可信PG反解析定义；返回：保留引号内容和语义括号的空白规范串。"""
    result, quote, index = [], None, 0
    while index < len(definition):
        char = definition[index]
        if quote is not None:
            result.append(char)
            if char == quote:
                if index + 1 < len(definition) and definition[index + 1] == quote:
                    result.append(definition[index + 1])
                    index += 1
                else:
                    quote = None
        elif char in ("'", '"'):
            quote = char
            result.append(char)
        elif not char.isspace():
            result.append(char)
        index += 1
    return "".join(result)


def _check_runtime(conn: Any) -> dict:
    """输入：当前事务；返回：实际固定扩展信息；只查reader可读catalog，不读受限设置。"""
    server = conn.execute("SELECT current_setting('server_version_num')::int AS version").fetchone()
    extension = conn.execute("SELECT e.extversion,n.nspname FROM pg_extension e "
                             "JOIN pg_namespace n ON n.oid=e.extnamespace "
                             "WHERE e.extname='pg_search'").fetchone()
    if (server["version"] // 10000 != 18 or not extension or extension["extversion"] != SEARCH_EXTENSION_VERSION
            or extension["nspname"] != "paradedb"):
        raise _conflict("BM25需pg_search0.26.4和PostgreSQL18；请先由管理员准备")
    return extension


def _check_preload(conn: Any) -> None:
    """输入：管理员事务；返回：None；预加载缺失即拒绝，reader不读取此受限参数。"""
    preload = conn.execute("SELECT current_setting('shared_preload_libraries') AS value").fetchone()["value"]
    if "pg_search" not in {name.strip().strip('"') for name in preload.split(",")}:
        raise _conflict("pg_search尚未预加载；需先配置并重启数据库")


def _index_profile(conn: Any) -> SearchIndexProfile:
    """输入：当前事务；返回：实际有效索引的受控配置；缺失或表达式漂移即拒绝。"""
    extension = _check_runtime(conn)
    row = conn.execute(INDEX_METADATA_SQL, (
        sql.Identifier(SEARCH_SCHEMA, "knowledge_chunks").as_string(), SEARCH_SCHEMA, SEARCH_INDEX,
    )).fetchone()
    if (not row or not all(row[name] for name in
            ("indisvalid", "indisready", "indislive", "correct_table"))
            or row["amname"] != "paradedb" or row["indisunique"]
            or row["indnkeyatts"] != 12 or row["indnatts"] != 12
            or row["reloptions"] not in (None, []) or row["predicate"] is not None
            or _definition_key(row["definition"]) != _definition_key(
                EXPECTED_INDEX_DEFINITION.format(schema=SEARCH_SCHEMA))):
        raise _conflict("BM25索引缺失、未就绪或配置不兼容；读取请求不会自动修复")
    return SearchIndexProfile(
        extension_version=extension["extversion"],
        index_schema=SEARCH_SCHEMA,
        index_definition_sha256=sha256(_definition_key(row["definition"]).encode("utf-8")).hexdigest(),
        search_fields=dict(SEARCH_FIELDS), filter_fields=FILTER_FIELDS,
        tokenizer_options={"defaults": "pg_search-0.26.4", "display_name": "alias=display_name"},
    )


def _corpus_fingerprint(conn: Any) -> tuple[str, int]:
    """在PG内摘要全索引语料，Python仅接收定长行摘要并流式形成总指纹。

    参数：conn必须处于调用者的一致事务；涵盖全部scope而非请求范围。
    返回：(SHA256十六进制摘要,实际块数)。逐行固定数组保留NULL/空串，按UUID排序，
    总摘要以版本字节开头，依次加入32字节行摘要，最后加入8字节大端块数。
    副作用：只读扫描；正文、标题、metadata不离开数据库，不截断全库成员。
    """
    digest = sha256(b"index-corpus-sha256-v1\n")
    count = 0
    with conn.cursor(name="qp_index_" + uuid4().hex) as cursor:
        cursor.itersize = 256
        cursor.execute(sql.SQL(CORPUS_FINGERPRINT_SQL).format(schema=sql.Identifier(SEARCH_SCHEMA)))
        for row in cursor:
            digest.update(bytes(row["row_sha256"]))
            count += 1
    digest.update(count.to_bytes(8, "big"))
    return digest.hexdigest(), count


def inspect_search(conn: Any, *, expected_profile: SearchIndexProfile | None = None,
                   expected_fingerprint: str | None = None) -> SearchIndexState:
    """输入：同一一致事务和可选期望状态；返回：现场状态；不符则明确失败，不修复。"""
    profile = _index_profile(conn)
    fingerprint, count = _corpus_fingerprint(conn)
    if ((expected_profile is not None and profile != expected_profile)
            or (expected_fingerprint is not None and fingerprint != expected_fingerprint)):
        raise _conflict("搜索配置或全索引语料已经变化，不能复用当前评测条件")
    return SearchIndexState(search_index_profile=profile,
                            index_corpus_fingerprint=fingerprint, indexed_chunks=count)


def prepare(settings: KnowledgeStorageSettings) -> SearchManagementSummary:
    """由管理员显式准备搜索扩展与索引，重复执行复用；不重建漂移索引或改知识。

    参数：settings提供管理账号及有界超时。
    返回：applied/reused及当前配置和全索引语料摘要。
    异常：KnowledgeStorageError；宿主插件未安装、未预加载或旧配置漂移时拒绝。
    副作用：仅在缺失时CREATE EXTENSION/INDEX，不修改001、知识内容或reader权限。
    """
    from . import _postgres as postgres
    with postgres.connection(settings, "admin") as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        postgres.transaction_limits(conn, settings)
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (650277169901,))
        postgres.check_database(conn)
        _check_preload(conn)
        applied = False
        if not conn.execute("SELECT 1 FROM pg_extension WHERE extname='pg_search'").fetchone():
            available = conn.execute("SELECT default_version FROM pg_available_extensions "
                                     "WHERE name='pg_search'").fetchone()
            if not available or available["default_version"] != SEARCH_EXTENSION_VERSION:
                raise _conflict("宿主机尚未安装固定pg_search0.26.4；管理命令不下载系统软件")
            conn.execute("CREATE EXTENSION pg_search VERSION '0.26.4'")
            applied = True
        _check_runtime(conn)
        existing = conn.execute("SELECT to_regclass(%s) AS index", (
            sql.Identifier(SEARCH_SCHEMA, SEARCH_INDEX).as_string(),
        )).fetchone()
        if existing["index"] is None:
            conn.execute(sql.SQL(CREATE_SEARCH_INDEX).format(schema=sql.Identifier(SEARCH_SCHEMA)))
            applied = True
        state = inspect_search(conn)
        postgres.check_permissions(conn)
        return SearchManagementSummary(**state.model_dump(), command="prepare-search",
                                       outcome="applied" if applied else "reused")


def check(settings: KnowledgeStorageSettings) -> SearchManagementSummary:
    """输入：reader配置；返回：实际设施状态；只读重复可读事务内核对，不执行DDL/DML。"""
    from . import _postgres as postgres
    with postgres.connection(settings, "read") as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        postgres.transaction_limits(conn, settings)
        postgres.check_database(conn)
        state = inspect_search(conn)
        return SearchManagementSummary(**state.model_dump(), command="check-search", outcome="checked")
