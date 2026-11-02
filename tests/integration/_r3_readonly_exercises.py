"""显式R3练习共用的只读会话；不初始化、导入或清理现有知识。"""

from pathlib import Path

import pytest

from querypilot.knowledge.api import (
    KnowledgeScope, KnowledgeStorageSettings, check_search_session, load_corpus,
)
from querypilot.retrieval.service import open_scoped_read


@pytest.fixture(scope="module")
def source_and_settings(request):
    """读取固定语料与reader配置；返回配置/语料/范围，不连接或写入数据库。

    显式执行须带--require-postgres；缺环境失败，不能以跳过冒充练习通过。
    """
    if not request.config.getoption("--require-postgres"):
        pytest.fail("执行此练习须加 --require-postgres；当前只允许收集", pytrace=False)
    source_path = Path(__file__).resolve().parents[2] / "data/rag/toxicology/r1-4-corpus"
    if not source_path.is_dir():
        pytest.fail("缺少固定 toxicology/r1-4-corpus 语料；不会自动换语料", pytrace=False)
    source = load_corpus(source_path)
    try:
        settings = KnowledgeStorageSettings()
        target = settings.connection_info("read")["dbname"]
    except Exception:
        pytest.fail("缺少有效 reader 配置或驱动；不会初始化数据库", pytrace=False)
    if target != "querypilot_knowledge":
        pytest.fail("此只读练习仅使用现有 querypilot_knowledge", pytrace=False)
    scope = KnowledgeScope(
        corpus_id=source.manifest.corpus_id,
        database_id="toxicology",
        schema_version=source.manifest.identity_descriptor.schema_versions["toxicology"],
    )
    return settings, source, scope


@pytest.fixture
def search_session(source_and_settings):
    """打开同一范围的只读会话并检查搜索设施；返回会话/语料/范围，结束关闭。"""
    settings, source, scope = source_and_settings
    with open_scoped_read(scope, settings) as session:
        check_search_session(session)
        yield session, source, scope
