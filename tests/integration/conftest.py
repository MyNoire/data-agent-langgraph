"""R2 PostgreSQL 测试门禁；只允许显式配置的专用测试数据库。"""

import pytest
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class _IntegrationSettings(BaseSettings):
    """输入：三条 TEST DSN；输出：秘密配置；本地 .env 可用，不打印原文。"""
    model_config = SettingsConfigDict(env_prefix="QUERYPILOT_KNOWLEDGE_TEST_", env_file=".env", extra="ignore")
    admin_dsn: SecretStr | None = None
    import_dsn: SecretStr | None = None
    read_dsn: SecretStr | None = None


@pytest.fixture(scope="session")
def pg_settings(request):
    """输入：pytest 配置；输出：已初始化专用库设置；缺环境按门禁失败/skip。"""
    required = request.config.getoption("--require-postgres")
    try:
        from querypilot.knowledge.knowledge_store import _postgres as postgres
        from querypilot.knowledge.knowledge_store.api import KnowledgeStorageSettings
        configured = _IntegrationSettings()
        if any(getattr(configured, role + "_dsn") is None for role in ("admin", "import", "read")):
            if required: pytest.fail("R2 实库门禁缺少三条 TEST DSN", pytrace=False)
            pytest.skip("R2 实库未配置；不作为验收通过")
        settings = KnowledgeStorageSettings(_env_file=None, **configured.model_dump())
        infos = settings.bootstrap_info()
        if any(info["dbname"] != "querypilot_knowledge_test" for info in infos.values()):
            pytest.fail("拒绝对非专用测试数据库执行 fixture", pytrace=False)
        postgres.bootstrap(settings)
        postgres.migrate(settings)
        return settings
    except Exception:
        if required: pytest.fail("R2 实库配置/驱动/服务/迁移检查失败；运行 manage_knowledge_db check 诊断", pytrace=False)
        pytest.skip("R2 实库服务不可用；不作为验收通过")


@pytest.fixture
def pg_database(pg_settings):
    """输入：专用库设置；输出：同设置；只用测试 admin 在前后清理 fixture 数据。"""
    from querypilot.knowledge.knowledge_store._postgres import connection, SCHEMA

    def clear():
        """输入：闭包测试设置；输出：None；仅删除专用测试库知识行，保留迁移。"""
        with connection(pg_settings, "admin") as conn:
            with conn.transaction():
                if conn.execute("SELECT current_database() AS name").fetchone()["name"] != "querypilot_knowledge_test":
                    pytest.fail("测试连接目标发生变化", pytrace=False)
                for table in ("chunk_embeddings", "knowledge_chunks", "schema_relations", "knowledge_documents", "knowledge_corpora"):
                    conn.execute(f"DELETE FROM {SCHEMA}.{table}")
            # 删掉的行在 VACUUM 之前仍计入 BM25 索引的统计量，会改变后面用例的分数；VACUUM 不能放在事务里。
            conn.execute(f"VACUUM {SCHEMA}.knowledge_chunks")
    clear()
    try:
        yield pg_settings
    finally:
        clear()


@pytest.fixture(scope="session")
def pg_search_settings(request, pg_settings):
    """在专用测试库上准备 pg_search 扩展和 BM25 索引，已有则复用；容器里没装插件时按门禁失败/skip。"""
    from querypilot.knowledge.api import KnowledgeStorageError
    from querypilot.knowledge.knowledge_store import _search

    try:
        _search.prepare(pg_settings)
    except KnowledgeStorageError:
        if request.config.getoption("--require-postgres"):
            pytest.fail("专用测试库的 BM25 设施准备失败；pg_search 的安装见 docs/operations/pg-search.md", pytrace=False)
        pytest.skip("专用测试库没有 BM25 设施；跳过不表示检索验收通过")
    return pg_settings


@pytest.fixture
def pg_search_database(pg_search_settings, pg_database):
    """带 BM25 索引的专用测试库；每个用例前后清空知识行并清理索引，规则同 pg_database。

    需要 BM25 的测试都用它，不往正式知识库里写：BM25 的统计量覆盖整个索引，
    在正式库里导入再删除一份测试 Corpus，也会改变正式 Corpus 的分数。
    """
    return pg_database
