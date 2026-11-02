"""R3 同库无损夹具；不调用 R2 的专用库清空流程。"""

from contextlib import contextmanager
from uuid import uuid4

import pytest


@pytest.fixture(scope="module")
def live_bm25_settings(request):
    """读取当前库三角色配置；缺环境按显式门禁失败，绝不初始化或换库。"""
    required = request.config.getoption("--require-postgres")
    try:
        import psycopg
        from querypilot.knowledge.api import KnowledgeStorageSettings

        settings = KnowledgeStorageSettings()
        targets = [settings.connection_info(role)["dbname"] for role in ("admin", "import", "read")]
        if targets != ["querypilot_knowledge"] * 3:
            pytest.fail("R3 无损组只允许当前 querypilot_knowledge；不会重定向旧 fixture", pytrace=False)
        with role_connection(settings, "read") as reader:
            version = reader.execute("SELECT extversion FROM pg_extension WHERE extname='pg_search'").fetchone()
            if not version or version["extversion"] != "0.26.4":
                pytest.fail("R3 无损组需要已安装的 pg_search0.26.4；不会自动安装", pytrace=False)
    except Exception:
        if required:
            pytest.fail("R3 实库配置/驱动/服务检查失败；未执行设施验证", pytrace=False)
        pytest.skip("R3 实库环境不可用；跳过不表示 BM25 验收通过")
    return settings


@contextmanager
def role_connection(settings, role):
    """按指定角色建立有限时连接；返回原生连接，仅供无损集成夹具使用。"""
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(**settings.connection_info(role), connect_timeout=settings.connect_timeout_s,
                         row_factory=dict_row, autocommit=True) as conn:
        conn.execute("SELECT set_config('search_path', 'pg_catalog,public', false)")
        conn.execute("SELECT set_config('statement_timeout', %s, false)", (str(settings.statement_timeout_ms),))
        conn.execute("SELECT set_config('lock_timeout', %s, false)", (str(settings.lock_timeout_ms),))
        yield conn


def knowledge_state(settings):
    """只读获取原知识、约束、迁移、角色和原表 ACL 摘要；不返回知识正文。"""
    from psycopg import sql

    with role_connection(settings, "admin") as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        tables = {}
        for table, key in (
            ("knowledge_corpora", "corpus_id"), ("knowledge_documents", "document_id"),
            ("schema_relations", "relation_id"), ("knowledge_chunks", "chunk_id"),
            ("schema_migrations", "version"),
        ):
            tables[table] = conn.execute(sql.SQL(
                "SELECT count(*) AS count, md5(coalesce(string_agg(md5(to_jsonb(t)::text), '' "
                "ORDER BY {}), '')) AS digest FROM {}.{} AS t"
            ).format(sql.Identifier(key), sql.Identifier("querypilot_kb"), sql.Identifier(table))).fetchone()
        roles = conn.execute("""SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication,
            rolbypassrls,rolcanlogin FROM pg_roles
            WHERE rolname IN ('querypilot_kb_reader','querypilot_kb_importer') ORDER BY rolname""").fetchall()
        acl = conn.execute("""SELECT c.relname,c.relowner,c.relacl::text FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='querypilot_kb'
            AND c.relkind='r' ORDER BY c.relname""").fetchall()
        constraints = conn.execute("""SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) AS definition
            FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='querypilot_kb'
            ORDER BY c.relname,k.conname""").fetchall()
        indexes = conn.execute("""SELECT c.oid,c.relname,pg_get_indexdef(c.oid) AS definition,
            i.indisvalid,i.indisready FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='querypilot_kb'
            ORDER BY c.relname""").fetchall()
    return {"tables": tables, "roles": roles, "acl": acl, "constraints": constraints, "indexes": indexes}


@pytest.fixture(scope="module")
def unchanged_live_knowledge(live_bm25_settings):
    """记录测试前原知识状态；全部用例结束后逐项核对，任何变化均失败。"""
    before = knowledge_state(live_bm25_settings)
    yield before
    assert knowledge_state(live_bm25_settings) == before


@pytest.fixture
def unprepared_search_table(live_bm25_settings, unchanged_live_knowledge):
    """只建本次唯一空搜索表，不建索引；供公开管理首次准备流程真实建索引。"""
    from psycopg import sql

    name = "r3_prepare_" + uuid4().hex
    table = sql.Identifier(name, "knowledge_chunks")
    schema = sql.Identifier(name)
    created = False
    try:
        with role_connection(live_bm25_settings, "admin") as conn, conn.transaction():
            assert not conn.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", (name,)).fetchone()
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
            conn.execute(sql.SQL("""CREATE TABLE {} (chunk_id uuid PRIMARY KEY,chunk_key text,
                corpus_id text,database_id text,schema_version text,knowledge_type text,title text,
                content text,schema_name text,table_name text,column_name text,metadata jsonb)""").format(table))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO querypilot_kb_reader").format(schema))
            conn.execute(sql.SQL("GRANT SELECT ON {} TO querypilot_kb_reader").format(table))
        created = True
        yield {"settings": live_bm25_settings, "schema": name, "table": table}
    finally:
        if created:
            with role_connection(live_bm25_settings, "admin") as conn, conn.transaction():
                # 删除自有表同时清理其自有索引；不碰原知识索引，也不 CASCADE。
                conn.execute(sql.SQL("DROP TABLE {}").format(table))
                conn.execute(sql.SQL("DROP SCHEMA {}").format(schema))
            with role_connection(live_bm25_settings, "read") as conn:
                assert not conn.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", (name,)).fetchone()


@pytest.fixture(scope="module")
def bm25_probe(live_bm25_settings, unchanged_live_knowledge):
    """在当前库创建唯一原创词项表；仅给自有表只读权，结束精确清理且不 CASCADE。"""
    from psycopg import sql
    from psycopg.types.json import Jsonb

    name = "r3_bm25_" + uuid4().hex
    table = sql.Identifier(name, "docs")
    schema = sql.Identifier(name)
    created = False
    rows = [
        (1, "a", "scope-a", "db-a", "v1", "table", "南京市长江大桥", "common common common common", "main", "atom_id", None, {}),
        (2, "b", "scope-a", "db-a", "v1", "column", "深圳市科技园", "common ballast ballast ballast", "main", "atom", "cl", {}),
        (3, "e", "scope-a", "db-a", "v1", "column", "上海市人民广场", "rare ballast ballast ballast", "main", "atom", "id", {}),
        (4, "c", "scope-a", "db-a", "v1", "column", "", "common ballast ballast ballast", "main", "atom", "id", {}),
        (5, "d", "scope-a", "db-a", "v1", "relationship", "", "common ballast ballast ballast", None, None, None, {"display_name": 123}),
        (6, "f", "scope-a", "db-a", "v1", "table", "", "ballast ballast ballast ballast", "main", "items", None,
         {"display_name": "库存金额", "not_searchable": "qpmetadatasecret"}),
        (7, "x", "scope-b", "db-a", "v1", "table", "", "common common common common common", "main", "atom", None, {}),
        (8, "y", "scope-a", "db-b", "v1", "table", "", "common common common common common", "main", "atom", None, {}),
        (9, "z", "scope-a", "db-a", "v2", "table", "", "common common common common common", "main", "atom", None, {}),
    ]
    try:
        with role_connection(live_bm25_settings, "admin") as conn, conn.transaction():
            assert not conn.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", (name,)).fetchone()
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
            conn.execute(sql.SQL("""CREATE TABLE {} (chunk_id uuid PRIMARY KEY,chunk_key text,
                corpus_id text,database_id text,schema_version text,knowledge_type text,title text,
                content text,schema_name text,table_name text,column_name text,metadata jsonb)""").format(table))
            with conn.cursor() as cursor:
                cursor.executemany(sql.SQL("INSERT INTO {} VALUES ({})").format(
                    table, sql.SQL(",").join(sql.Placeholder() for _ in range(12))),
                    [(f"00000000-0000-5000-8000-{row[0]:012d}", *row[1:-1], Jsonb(row[-1]))
                     for row in rows])
            conn.execute(sql.SQL("""CREATE INDEX docs_bm25_idx ON {} USING paradedb (
                chunk_id,(chunk_key::pdb.literal),(corpus_id::pdb.literal),(database_id::pdb.literal),
                (schema_version::pdb.literal),(knowledge_type::pdb.literal),(title::pdb.jieba),
                (content::pdb.jieba),(schema_name::pdb.simple),(table_name::pdb.simple),
                (column_name::pdb.simple),((CASE WHEN jsonb_typeof(metadata->'display_name')='string'
                    THEN metadata->>'display_name' END)::pdb.jieba('alias=display_name')))""").format(table))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO querypilot_kb_reader").format(schema))
            conn.execute(sql.SQL("GRANT SELECT ON {} TO querypilot_kb_reader").format(table))
        created = True
        yield {"settings": live_bm25_settings, "schema": name, "table": table}
    finally:
        if created:
            with role_connection(live_bm25_settings, "admin") as conn, conn.transaction():
                conn.execute(sql.SQL("DROP TABLE {}").format(table))
                conn.execute(sql.SQL("DROP SCHEMA {}").format(schema))
            with role_connection(live_bm25_settings, "read") as conn:
                assert not conn.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", (name,)).fetchone()
