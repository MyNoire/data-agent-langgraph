"""用临时配置文件验证用户端启动：读一次 Active Corpus，得到各库的 Knowledge Scope。"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import langsmith
import pytest
from langsmith.utils import get_env_var, tracing_is_enabled

from querypilot.querying.api import QueryContext, start_serving
from querypilot.retrieval.api import KnowledgeRetrievalError, KnowledgeScope

SHOP = {"corpus_id": "a" * 64, "schema_version": "b" * 64}
CLUB = {"corpus_id": "c" * 64, "schema_version": "d" * 64}


def test_startup_reads_the_active_corpus_once_into_the_runtime_context(tmp_path):
    """输入：配置里有 shop 和 club 两个库；启动后把文件改成空对照表。

    输出：运行时 context 是启动那一刻两个库各自的 Knowledge Scope，不随文件变化。
    """
    path = tmp_path / "active_corpus.json"
    path.write_text(json.dumps({"shop": SHOP, "club": CLUB}), encoding="utf-8")

    context = start_serving(path)
    path.write_text("{}", encoding="utf-8")

    assert context == QueryContext(knowledge_scopes={
        "shop": KnowledgeScope(corpus_id="a" * 64, database_id="shop", schema_version="b" * 64),
        "club": KnowledgeScope(corpus_id="c" * 64, database_id="club", schema_version="d" * 64),
    })


@pytest.mark.parametrize("content", [
    None,
    "directory",
    b"{not json",
    bytes([0xff, 0xfe, 0x00]),
    b"[]",
    b'{"shop": "latest"}',
    json.dumps({"shop": {"corpus_id": "a" * 64}}).encode(),
    json.dumps({"shop": {**SHOP, "corpus_id": "latest"}}).encode(),
    json.dumps({"shop": {**SHOP, "top_k": 99}}).encode(),
    json.dumps({"shop": {**SHOP, "database_id": "club"}}).encode(),
], ids=["missing", "unreadable", "not_json", "not_utf8", "not_a_mapping", "entry_not_a_mapping", "missing_field",
        "invalid_id", "unknown_field", "database_id_inside_entry"])
def test_startup_fails_when_the_active_corpus_is_missing_or_malformed(tmp_path, content):
    """输入：配置文件缺失、不可读（路径是个目录）或内容不符合格式。

    输出：启动以 configuration_error 失败，不会变成"所有库都没有知识"继续运行。
    """
    path = tmp_path / "active_corpus.json"
    if content == "directory":
        path.mkdir()
    elif content is not None:
        path.write_bytes(content)

    with pytest.raises(KnowledgeRetrievalError) as caught:
        start_serving(path)

    assert caught.value.error_type == "configuration_error"


def test_the_committed_active_corpus_is_the_two_corpora_with_value_samples(monkeypatch):
    """输入：在仓库根目录按默认路径启动。

    输出：toxicology 和 student_club 两个库，各自是带取值样例的那份 Corpus（期望值抄自规格）。
    """
    monkeypatch.chdir(Path(__file__).resolve().parents[1])

    scopes = start_serving().knowledge_scopes

    assert {name: (scope.corpus_id, scope.schema_version) for name, scope in scopes.items()} == {
        "toxicology": ("50c156f8700b7312ea4391384c2a4f7cc64e750e6457edd7815463e08560a768",
                       "45b2e99ae191cbcde4fa377e702dcd498c98abaf0f7193565861601d653ba799"),
        "student_club": ("748167d2fce64e4575d9de96495a4ed0ac1b8c2bbdd1b3dd07f873751b3092b7",
                         "20441054b528d9e52458069d101e8290f2698b873886d66f2e7a9a52803fe6c1"),
    }


def test_startup_turns_langsmith_tracing_off_without_building_a_query_graph(tmp_path, monkeypatch):
    """输入：环境变量打开追踪，只做用户端启动，不编译查询图。

    输出：启动前追踪是开的（确认环境变量确实生效）；启动后主线程和工作线程里都是关的——ToolNode 在
    工作线程里执行 tool。
    """
    path = tmp_path / "active_corpus.json"
    path.write_text(json.dumps({"shop": SHOP}), encoding="utf-8")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    get_env_var.cache_clear()            # langsmith 缓存了环境变量的读取结果
    langsmith.configure(enabled=None)    # 回到"按环境变量决定"
    try:
        assert tracing_is_enabled() is True

        start_serving(path)

        assert tracing_is_enabled() is False
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(tracing_is_enabled).result() is False
    finally:
        langsmith.configure(enabled=False)
        get_env_var.cache_clear()
