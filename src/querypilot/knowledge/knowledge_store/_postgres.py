"""可信 PostgreSQL 连接、迁移、权限及原字段回读；不执行模型生成 SQL。"""

from contextlib import contextmanager
from datetime import datetime, timezone
from importlib.resources import files
from typing import Any, Iterator
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from sqlglot import exp, parse, parse_one

from querypilot.knowledge.knowledge_corpus.api import canonical_json, digest
from querypilot.knowledge.knowledge_corpus.api import CorpusDocument, CorpusChunk, CorpusRelation
from .settings import KnowledgeStorageError, KnowledgeStorageSettings, ROLE_NAMES, Role
from querypilot.knowledge.knowledge_corpus.api import VerifiedCorpus
from .models import (
    DIGEST_VERSION, STORAGE_VERSION, ManagementSummary, VerificationDigests,
)

SCHEMA = "querypilot_kb"
TABLES = {"documents": "knowledge_documents", "relations": "schema_relations", "chunks": "knowledge_chunks"}
MODELS = {"documents": CorpusDocument, "relations": CorpusRelation, "chunks": CorpusChunk}
CORPUS_FIELDS = (
    "corpus_id", "database_id", "schema_version", "core_digest", "manifest",
    "validation", "counts", "status", "storage_contract_version",
)


def translate_error(exc: psycopg.Error, *, commit_pending: bool = False) -> KnowledgeStorageError:
    """输入：底层 PG 错误/提交阶段；输出：受控异常；无副作用，不输出原 SQL。"""
    if commit_pending and isinstance(exc, psycopg.OperationalError) and (
        not exc.sqlstate or exc.sqlstate.startswith("08") or exc.sqlstate in {"57P01", "57P02", "57P03"}
    ):
        return KnowledgeStorageError("commit_outcome_unknown", "提交确认连接中断，需新连接复验")
    category = {"42501": "permission_denied", "55P03": "lock_timeout", "57014": "statement_timeout"}.get(exc.sqlstate)
    if category is None:
        category = "connection_failed" if isinstance(exc, psycopg.OperationalError) else "database_error"
    messages = {"permission_denied": "知识库权限不足", "lock_timeout": "知识库锁等待超时",
                "statement_timeout": "知识库语句执行超时", "connection_failed": "无法连接知识库",
                "database_error": "知识库操作失败"}
    return KnowledgeStorageError(category, messages[category])


@contextmanager
def connection(settings: KnowledgeStorageSettings, role: Role) -> Iterator[psycopg.Connection]:
    """建立按需连接。

    参数：settings 为秘密配置，role 指定账号职责。
    返回：autocommit 连接上下文；使用者负责显式事务。
    异常：KnowledgeStorageError；PG cause 保留，默认不打印。
    副作用：建立/关闭连接，限定安全 search_path 和会话语句超时。
    """
    info = settings.connection_info(role)
    try:
        with psycopg.connect(**{**info, "connect_timeout": settings.connect_timeout_s},
                             autocommit=True, row_factory=dict_row) as conn:
            conn.execute("SELECT set_config('search_path', 'pg_catalog,public', false)")
            conn.execute("SELECT set_config('statement_timeout', %s, false)", (str(settings.statement_timeout_ms),))
            if conn.execute("SELECT current_user AS name").fetchone()["name"] != info["user"]:
                raise KnowledgeStorageError("configuration_error", "实际连接角色与配置不一致")
            yield conn
    except psycopg.Error as exc:
        raise translate_error(exc) from exc


def transaction_limits(conn: psycopg.Connection, settings: KnowledgeStorageSettings) -> None:
    """输入：显式事务连接/配置；输出：None；设置本事务有界等待和语句时间。"""
    for name, value in (("lock_timeout", settings.lock_timeout_ms), ("statement_timeout", settings.statement_timeout_ms)):
        conn.execute("SELECT set_config(%s, %s, true)", (name, str(value)))


def migration_bytes() -> bytes:
    """输入：无；输出：包内固定迁移字节；异常：资源 I/O 错误；只读。"""
    return files("querypilot.knowledge.knowledge_store").joinpath("migrations/001_corpus.sql").read_bytes()


def _normalize_expression(node: exp.Expression) -> str:
    """输入：可信 DDL/PG 反解析 AST；输出：语义保序表达式；无副作用。"""
    def normalize(item: exp.Expression) -> exp.Expression:
        """输入：一个 AST 节点；输出：去冗余包装/已知隐式 cast 的节点，保留逻辑结构。"""
        if isinstance(item, exp.Paren):
            return item.this
        if (isinstance(item, exp.Cast) and isinstance(item.this, (exp.Literal, exp.Null))
                and item.args["to"].is_type(exp.DataType.Type.TEXT)):
            return item.this
        # PG 将 vector_norm(...) > 0 反解析为 > 0::double precision。
        # 只合并这个已知函数的精确零阈值，保留其他数值 cast 的语义。
        if (isinstance(item, exp.Cast) and isinstance(item.this, exp.Literal)
                and not item.this.is_string and item.this.this == "0"
                and item.args["to"].is_type(exp.DataType.Type.DOUBLE)
                and isinstance(item.parent, exp.GT) and item.parent.expression is item):
            left = item.parent.this
            if isinstance(left, exp.Dot) and left.this.name == "public":
                left = left.expression
            if isinstance(left, exp.Anonymous) and left.name.lower() == "vector_norm":
                return item.this
        if isinstance(item, exp.Dot) and item.this.name == "public":
            return item.expression
        return item
    # 多遍只去表达层包装，保留 AND/OR 子树和复合键成员顺序。
    while True:
        previous = node.dump()
        node = node.transform(normalize)
        if node.dump() == previous:
            break
    for item in node.walk():
        item.meta.clear()
    # 比较 AST 树而非移除括号后的 SQL 文本；后者会丢失 AND/OR 分组语义。
    return canonical_json(node.dump()).decode("utf-8")


def _constraint_expression(definition: str) -> str:
    """输入：PG 约束定义；输出：规范 AST；异常：解析错误；无副作用。"""
    statement = parse_one("ALTER TABLE t ADD CONSTRAINT c " + definition, read="postgres")
    return _normalize_expression(statement.find(exp.Constraint).expressions[0])


def expected_structure() -> dict[str, Any]:
    """输入：无；输出：从受控迁移生成的列/约束预期；只读，不信任数据库成功标记。"""
    result = {}
    for statement in parse(migration_bytes().decode("utf-8"), read="postgres"):
        if not isinstance(statement, exp.Create) or statement.args.get("kind") != "TABLE":
            continue
        columns, constraints = [], {}
        for item in statement.this.expressions:
            if isinstance(item, exp.ColumnDef):
                kinds = [c.kind for c in item.constraints]
                primary = any(isinstance(k, exp.PrimaryKeyColumnConstraint) for k in kinds)
                default = next((k.this.sql(dialect="postgres") for k in kinds if isinstance(k, exp.DefaultColumnConstraint)), None)
                type_name = item.kind.sql(dialect="postgres").lower().replace("public.", "")
                type_name = {"timestamptz": "timestamp with time zone", "int": "integer"}.get(type_name, type_name)
                columns.append((item.name, type_name, primary or any(isinstance(k, exp.NotNullColumnConstraint) for k in kinds), default))
                for constraint in item.constraints:
                    if isinstance(constraint.kind, exp.PrimaryKeyColumnConstraint):
                        constraints[constraint.name] = _constraint_expression(f"PRIMARY KEY ({item.name})")
            elif isinstance(item, exp.Constraint):
                constraints[item.name] = _normalize_expression(item.expressions[0])
        result[statement.this.this.name] = {"columns": columns, "constraints": constraints}
    return result


def versions(conn: psycopg.Connection) -> tuple[str, str]:
    """输入：连接；输出：实际 PG/扩展版本；异常：不兼容时受控错误；只读。"""
    server = conn.execute("SELECT current_setting('server_version') AS v, current_setting('server_version_num')::int AS n").fetchone()
    vector = conn.execute("SELECT e.extversion, n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='vector'").fetchone()
    if server["n"] // 10000 != 18 or not vector or vector["extversion"] != "0.8.7" or vector["nspname"] != "public":
        raise KnowledgeStorageError("migration_conflict", "需 PostgreSQL18 与 public 中的 pgvector0.8.7")
    return server["v"], vector["extversion"]


def check_permissions(conn: psycopg.Connection, *, tables: bool = True) -> None:
    """输入：连接/表是否已建；输出：None；异常：有效权限越界；只读实际 ACL/成员路径。"""
    for role in ROLE_NAMES.values():
        attrs = conn.execute("SELECT oid, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
        if not attrs or any(attrs[k] for k in ("rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication", "rolbypassrls")) or not attrs["rolcanlogin"]:
            raise KnowledgeStorageError("migration_conflict", "运行角色属性不符合最小权限")
        db = conn.execute("SELECT has_database_privilege(%s,current_database(),'CREATE') AS c, has_database_privilege(%s,current_database(),'TEMPORARY') AS t, datdba=%s AS owner FROM pg_database WHERE datname=current_database()", (role, role, attrs["oid"])).fetchone()
        # 使用字面前缀比较，避免 LIKE 的通配符语义和 psycopg 参数中的 % 转义混淆。
        writable = conn.execute("SELECT 1 FROM pg_namespace WHERE left(nspname,3)<>'pg_' AND nspname<>'information_schema' AND (has_schema_privilege(%s,oid,'CREATE') OR nspowner=%s) LIMIT 1", (role, attrs["oid"])).fetchone()
        memberships = conn.execute("SELECT 1 FROM pg_roles WHERE oid<>%s AND pg_has_role(%s,oid,'MEMBER') LIMIT 1", (attrs["oid"], role)).fetchone()
        owned = conn.execute("SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relowner=%s AND left(n.nspname,3)<>'pg_' LIMIT 1", (attrs["oid"],)).fetchone()
        if db["c"] or db["t"] or db["owner"] or writable or memberships or owned:
            raise KnowledgeStorageError("migration_conflict", "运行角色存在额外 CREATE/TEMPORARY/所有权或成员权限")
        if tables:
            for table in ("schema_migrations", *TABLES.values(), "knowledge_corpora"):
                name = f"{SCHEMA}.{table}"
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN"):
                    actual = conn.execute("SELECT has_table_privilege(%s,%s,%s) AS allowed", (role, name, privilege)).fetchone()["allowed"]
                    required = privilege == "SELECT" or (privilege == "INSERT" and role == ROLE_NAMES["import"] and table != "schema_migrations")
                    if actual != required:
                        raise KnowledgeStorageError("migration_conflict", "知识表有效权限与角色契约不一致")
                # 列级授权也能赋予写权限，仅查表级 ACL 会漏掉 UPDATE(column)。
                for privilege in ("INSERT", "UPDATE", "REFERENCES"):
                    actual = conn.execute("SELECT has_any_column_privilege(%s,%s,%s) AS allowed", (role, name, privilege)).fetchone()["allowed"]
                    required = privilege == "INSERT" and role == ROLE_NAMES["import"] and table != "schema_migrations"
                    if actual != required:
                        raise KnowledgeStorageError("migration_conflict", "知识列存在额外有效权限")


def check_structure(conn: psycopg.Connection) -> None:
    """输入：连接；输出：None；异常：列/约束/迁移内容漂移；只读。"""
    expected = expected_structure()
    tables = conn.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind IN ('r','p')", (SCHEMA,)).fetchall()
    if {r["relname"] for r in tables} != set(expected):
        raise KnowledgeStorageError("migration_conflict", "知识 Schema 的表集合不兼容")
    for table, wanted in expected.items():
        columns = conn.execute("SELECT a.attname, format_type(a.atttypid,a.atttypmod) AS type, a.attnotnull, pg_get_expr(d.adbin,d.adrelid) AS def FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE n.nspname=%s AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum", (SCHEMA, table)).fetchall()
        if [(r["attname"], r["type"].replace("public.", ""), r["attnotnull"], r["def"]) for r in columns] != wanted["columns"]:
            raise KnowledgeStorageError("migration_conflict", "知识表列类型/空值/默认值发生漂移")
        constraints = conn.execute("SELECT con.conname, pg_get_constraintdef(con.oid) AS def, con.convalidated, con.condeferrable, con.condeferred, con.conenforced FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=%s AND con.contype IN ('p','u','f','c')", (SCHEMA, table)).fetchall()
        actual = {r["conname"]: _constraint_expression(r["def"]) for r in constraints}
        if actual != wanted["constraints"] or any(not r["convalidated"] or not r["conenforced"] or r["condeferrable"] or r["condeferred"] for r in constraints):
            raise KnowledgeStorageError("migration_conflict", "知识表主外键/唯一/CHECK 约束发生漂移")
    migration = conn.execute(f"SELECT version,checksum FROM {SCHEMA}.schema_migrations ORDER BY version").fetchall()
    if migration != [{"version": "001", "checksum": digest(migration_bytes())}]:
        raise KnowledgeStorageError("migration_conflict", "迁移版本或文件摘要不兼容")


def check_database(conn: psycopg.Connection) -> tuple[str, str]:
    """输入：连接；输出：实际版本；异常：不兼容；只读结构与有效权限。"""
    result = versions(conn)
    check_structure(conn)
    check_permissions(conn)
    return result


def bootstrap(settings: KnowledgeStorageSettings) -> ManagementSummary:
    """输入：三个同目标秘密 DSN；输出：管理摘要；建立角色/扩展/Schema 与受控 ACL。"""
    infos = settings.bootstrap_info()
    with connection(settings, "admin") as conn, conn.transaction():
        transaction_limits(conn, settings)
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (650277169901,))
        initial = conn.execute("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=%s) AS present", (SCHEMA,)).fetchone()["present"]
        # 不覆盖同名未知所有者，也不轮换已存在的全实例角色密码。
        namespace = conn.execute("SELECT pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname=%s", (SCHEMA,)).fetchone()
        if namespace and namespace["owner"] != infos["admin"]["user"]:
            raise KnowledgeStorageError("migration_conflict", "知识 Schema 所有者不符合当前管理配置")
        for role in ("import", "read"):
            name = ROLE_NAMES[role]
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)).fetchone():
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}").format(sql.Identifier(name), sql.Literal(infos[role]["password"])))
        if not conn.execute("SELECT 1 FROM pg_extension WHERE extname='vector'").fetchone():
            conn.execute("CREATE EXTENSION vector WITH SCHEMA public")
        server, vector = versions(conn)
        if not initial:
            conn.execute(sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(sql.Identifier(SCHEMA), sql.Identifier(infos["admin"]["user"])))
        database = conn.execute("SELECT current_database() AS name").fetchone()["name"]
        conn.execute(sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM PUBLIC, {}, {}").format(sql.Identifier(database), *(sql.Identifier(n) for n in ROLE_NAMES.values())))
        conn.execute(sql.SQL("REVOKE CREATE ON SCHEMA {} FROM PUBLIC").format(sql.Identifier(SCHEMA)))
        for name in ROLE_NAMES.values():
            conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), sql.Identifier(name)))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(SCHEMA), sql.Identifier(name)))
        conn.execute(sql.SQL("ALTER ROLE {} IN DATABASE {} SET default_transaction_read_only=on").format(sql.Identifier(ROLE_NAMES["read"]), sql.Identifier(database)))
        check_permissions(conn, tables=False)
        if conn.execute("SELECT to_regclass(%s) AS t", (f"{SCHEMA}.schema_migrations",)).fetchone()["t"]:
            check_database(conn)
        elif conn.execute("SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s LIMIT 1", (SCHEMA,)).fetchone():
            raise KnowledgeStorageError("migration_conflict", "已有知识 Schema 含未知或部分初始化对象")
        return ManagementSummary(command="bootstrap", outcome="reused" if initial else "applied", server_version=server, vector_version=vector, migration_version=None)


def migrate(settings: KnowledgeStorageSettings) -> ManagementSummary:
    """输入：管理配置；输出：applied/reused；事务内应用迁移/授予最小权限，漂移拒绝。"""
    with connection(settings, "admin") as conn, conn.transaction():
        transaction_limits(conn, settings)
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (650277169901,))
        server, vector = versions(conn)
        check_permissions(conn, tables=False)
        exists = conn.execute("SELECT to_regclass(%s) AS table", (f"{SCHEMA}.schema_migrations",)).fetchone()["table"]
        if exists:
            check_database(conn)
            outcome = "reused"
        else:
            if conn.execute("SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s LIMIT 1", (SCHEMA,)).fetchone():
                raise KnowledgeStorageError("migration_conflict", "拒绝在部分初始化的知识 Schema 上补写")
            for statement in parse(migration_bytes().decode("utf-8"), read="postgres"):
                conn.execute(statement.sql(dialect="postgres"))
            for name in ROLE_NAMES.values():
                conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(sql.Identifier(SCHEMA), sql.Identifier(name)))
            for table in ("knowledge_corpora", *TABLES.values()):
                conn.execute(sql.SQL("GRANT INSERT ON {}.{} TO {}").format(sql.Identifier(SCHEMA), sql.Identifier(table), sql.Identifier(ROLE_NAMES["import"])))
            conn.execute(f"INSERT INTO {SCHEMA}.schema_migrations(version,checksum) VALUES (%s,%s)", ("001", digest(migration_bytes())))
            check_database(conn)
            outcome = "applied"
        return ManagementSummary(command="migrate", outcome=outcome, server_version=server, vector_version=vector, migration_version="001")


def check(settings: KnowledgeStorageSettings) -> ManagementSummary:
    """输入：读取配置；输出：checked 版本摘要；只读检查，无 DDL。"""
    with connection(settings, "read") as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        server, vector = check_database(conn)
        return ManagementSummary(command="check", outcome="checked", server_version=server, vector_version=vector, migration_version="001")


def corpus_record(corpus: VerifiedCorpus) -> dict[str, Any]:
    """输入：已验证语料；输出：不含操作时间的登记投影；无副作用。"""
    manifest = corpus.manifest
    database = manifest.identity_descriptor.database_scope[0]
    return {"corpus_id": manifest.corpus_id, "database_id": database,
            "schema_version": manifest.identity_descriptor.schema_versions[database], "core_digest": manifest.core_digest,
            "manifest": manifest.model_dump(mode="json"), "validation": corpus.validation.model_dump(mode="json"),
            "counts": manifest.counts.model_dump(mode="json"), "status": "stored", "storage_contract_version": STORAGE_VERSION}


def _insert(conn: psycopg.Connection, table: str, payload: dict[str, Any]) -> None:
    """输入：连接/可信表名/原字段；输出：None；参数化 INSERT，JSON 字典使用 Jsonb。"""
    names = list(payload)
    query = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
        sql.Identifier(SCHEMA), sql.Identifier(table),
        sql.SQL(",").join(map(sql.Identifier, names)), sql.SQL(",").join(sql.Placeholder() for _ in names))
    conn.execute(query, [Jsonb(payload[n]) if isinstance(payload[n], dict) else payload[n] for n in names])


def insert_corpus(conn: psycopg.Connection, corpus: VerifiedCorpus) -> None:
    """输入：写事务/已验证对象；输出：None；先登记再文档/关系/块，默认向量全 NULL。"""
    _insert(conn, "knowledge_corpora", corpus_record(corpus))
    for group, table in TABLES.items():
        for row in getattr(corpus, group):
            _insert(conn, table, row.model_dump(mode="json"))


def read_corpus(conn: psycopg.Connection, corpus_id: str) -> tuple[dict | None, dict[str, list[dict]]]:
    """输入：同一事务连接/corpus；输出：实际登记及全量行；只读不截断，保留创建时间。"""
    record = conn.execute(f"SELECT * FROM {SCHEMA}.knowledge_corpora WHERE corpus_id=%s", (corpus_id,)).fetchone()
    groups = {group: conn.execute(sql.SQL("SELECT * FROM {}.{} WHERE corpus_id=%s").format(sql.Identifier(SCHEMA), sql.Identifier(table)), (corpus_id,)).fetchall() for group, table in TABLES.items()}
    return record, groups


def original_row(group: str, row: dict) -> dict:
    """输入：组名/数据库行；输出：原模型全部字段，UUID 转字符串；缺字段抛受控冲突。"""
    try:
        return {key: str(row[key]) if isinstance(row[key], UUID) else row[key] for key in MODELS[group].model_fields}
    except KeyError as exc:
        raise KnowledgeStorageError("target_conflict", "数据库对象缺少原字段") from exc


def _utc(value: datetime) -> str:
    """输入：实际 TIMESTAMPTZ；输出：UTC 六位小数；空值/无时区报冲突；无副作用。"""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise KnowledgeStorageError("target_conflict", "数据库首次时间无效")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def verification_digests(record: dict, rows: dict[str, list[dict]]) -> VerificationDigests:
    """输入：同视图实际登记/原始行；输出：规范内容和首次时间指纹；纯编码，无 I/O。"""
    content = {"digest_version": DIGEST_VERSION, "kind": "projection", "corpus": {k: record[k] for k in CORPUS_FIELDS}}
    times = {"digest_version": DIGEST_VERSION, "kind": "timestamps", **{k: record[k] for k in ("corpus_id", "database_id", "schema_version")}, "imported_at": _utc(record["imported_at"])}
    for group in TABLES:
        prefix = {"documents": "document", "relations": "relation", "chunks": "chunk"}[group]
        ordered = sorted(rows[group], key=lambda row: row[prefix + "_key"])
        content[group] = [original_row(group, row) for row in ordered]
        times[group] = [{prefix + "_id": str(row[prefix + "_id"]), prefix + "_key": row[prefix + "_key"], "created_at": _utc(row["created_at"])} for row in ordered]
    return VerificationDigests(projection_digest=digest(canonical_json(content)), timestamps_digest=digest(canonical_json(times)))


def verify_rows(corpus: VerifiedCorpus, record: dict | None, rows: dict[str, list[dict]]) -> VerificationDigests:
    """输入：源语料/实际回读；输出：核验后指纹；缺登记、污染、缺/多行均明确失败。"""
    if record is None:
        raise KnowledgeStorageError("target_conflict" if any(rows.values()) else "not_imported", "知识语料缺失或登记不完整")
    if canonical_json({k: record[k] for k in CORPUS_FIELDS}) != canonical_json(corpus_record(corpus)):
        raise KnowledgeStorageError("target_conflict", "数据库语料登记与源不一致")
    for group in TABLES:
        key = {"documents": "document_key", "relations": "relation_key", "chunks": "chunk_key"}[group]
        actual = sorted((original_row(group, row) for row in rows[group]), key=lambda row: row[key])
        expected = sorted((row.model_dump(mode="json") for row in getattr(corpus, group)), key=lambda row: row[key])
        if canonical_json(actual) != canonical_json(expected):
            raise KnowledgeStorageError("target_conflict", "数据库原字段或对象集合与源不一致")
    if any(row[k] is not None for row in rows["chunks"] for k in ("embedding", "embedding_profile_id", "embedding_dimensions")):
        raise KnowledgeStorageError("target_conflict", "R2 语料含未验收向量")
    return verification_digests(record, rows)
