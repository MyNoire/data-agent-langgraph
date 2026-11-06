"""检索冒烟 L01–L10：只读访问正式知识库里已有的 toxicology 知识，作为检索行为的回归保护。

不导入 Corpus，也不进评测报告。Knowledge Scope 取自本地 toxicology Corpus 的 manifest；
期望值来自 R3 规格的样例表（specs/003-r3-lexical-retrieval/spec.md）。实际结果和期望不一致时
先报告，不改期望值。
"""

import json
from pathlib import Path

import pytest

from querypilot.knowledge.api import KnowledgeScope, load_corpus
from querypilot.retrieval.api import RetrievalRequest, retrieve_knowledge
from _bm25_fixtures import live_bm25_settings  # noqa: F401  （pytest 夹具）

CORPUS_DIR = Path("data/rag/toxicology/r1-4-corpus")
ATOM_TO_MOLECULE = json.dumps(["relationship", "toxicology", "main", "atom", 0], separators=(",", ":"))


@pytest.fixture(scope="module")
def toxicology(live_bm25_settings, request):  # noqa: F811
    """正式知识库的读取配置和 toxicology 的 Knowledge Scope；本地没有 Corpus 时按门禁失败或跳过。"""
    if not (CORPUS_DIR / "manifest.json").exists():
        if request.config.getoption("--require-postgres"):
            pytest.fail(f"缺少本地 toxicology Corpus：{CORPUS_DIR}", pytrace=False)
        pytest.skip("本地没有 toxicology Corpus；跳过不表示冒烟通过")
    manifest = load_corpus(CORPUS_DIR).manifest
    scope = KnowledgeScope(corpus_id=manifest.corpus_id, database_id="toxicology",
                           schema_version=manifest.identity_descriptor.schema_versions["toxicology"])
    return live_bm25_settings, scope


def _top5(toxicology, query: str, knowledge_type: str) -> list[tuple]:
    """一次只读检索，返回前 5 个 Hit 的身份：关系类用 chunk_key，其余用（类型, 表, 列）。"""
    settings, scope = toxicology
    result = retrieve_knowledge(RetrievalRequest(query=query, scope=scope, top_k=5, knowledge_types=[knowledge_type]),
                                settings)
    return [("relationship", hit.chunk_key, hit.table_name, hit.column_name) if hit.knowledge_type == "relationship"
            else (hit.knowledge_type, hit.table_name, hit.column_name) for hit in result.hits]


@pytest.mark.parametrize(("case", "query", "knowledge_type", "targets"), [
    ("L01", "atom", "table", [("table", "atom", None)]),
    ("L02", "atom_id", "column", [("column", "atom", "atom_id")]),
    ("L03", '"main"."atom"."element"', "column", [("column", "atom", "element")]),
    ("L04", "  MAIN.ATOM.ELEMENT  ", "column", [("column", "atom", "element")]),   # 大小写和首尾空白
    ("L05", "molecule id", "column", [("column", "atom", "molecule_id"), ("column", "bond", "molecule_id"),
                                      ("column", "molecule", "molecule_id")]),
    ("L06", "chlorine", "column", [("column", "atom", "element")]),
    ("L07", "cl", "column", [("column", "atom", "element")]),
    ("L08", "元素组成", "table", [("table", "atom", None)]),
    # atom → molecule 关系块；关系类的表列定位字段为空
    ("L09", "原子属于该分子", "relationship", [("relationship", ATOM_TO_MOLECULE, None, None)]),
])
def test_target_chunks_are_in_the_top_5_of_their_type(toxicology, case, query, knowledge_type, targets):
    top5 = _top5(toxicology, query, knowledge_type)

    assert [target for target in targets if target not in top5] == [], f"{case} 前 5 名：{top5}"


def test_l10_query_with_no_known_term_is_a_normal_no_match(toxicology):
    settings, scope = toxicology
    result = retrieve_knowledge(RetrievalRequest(query="qpnomatch9f8e7d6c", scope=scope), settings)

    assert (result.outcome, result.hits) == ("no_match", [])
