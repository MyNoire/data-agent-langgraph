"""不给取值文件、语义配置也没有新字段时，Corpus 构建的输出与加入取值样例和数据约定之前逐字节相同。

夹具 tests/fixtures/corpus-build-r1-4/ 是一份固定的 demo 输入（整理后的批准包 + 语义配置），
由加入取值样例之前的代码（master 16d14577）生成；下面的摘要也是那份代码用这份输入构建出来的。
渲染或身份规则有意改变时，要连同这里的摘要一起更新，并在提交里说明原因。
"""

from hashlib import sha256
from pathlib import Path

from querypilot.knowledge.knowledge_corpus._corpus import build_knowledge_corpus

FIXTURE = Path(__file__).parent / "fixtures" / "corpus-build-r1-4"
CORPUS_ID = "3af7fa902fe920f4a59dd343409c619b6a659907d83844b6a6d83a5c847fc03d"
DIGESTS = {
    "chunks.jsonl": "417d793f64382c8bbb85a0599b7e7a35bd69e566a6b773357bef7d347f7f0aa4",
    "documents.jsonl": "97e4f5c8ef625b9533dd9ea38a220d4fd9491e8006c38ab11b507a92d285044b",
    "relations.jsonl": "8ae53a933e4ab35e7e97e05964e4b265460c7b143a201f5931ae337d059a7187",
    "manifest.json": "434752ce9584d82734fb3b098860370747dde98595422f9506a733b2926446c4",
    "validation.json": "94bebfa9999ef2793a9533d3df51163f37b908e75fdf8567d2dbebf2b32bbb0b",
    "build-config.json": "2b59b8c135f1f7f1c02243399b9cf58524679a91e9d3a31a3fcdccf8e2953b2a",
}


def test_building_without_new_inputs_gives_the_same_bytes_as_before(tmp_path):
    result = build_knowledge_corpus(FIXTURE / "curated", FIXTURE / "config" / "semantics.json",
                                    tmp_path / "corpus", "demo")

    assert result.corpus_id == CORPUS_ID
    assert {name: sha256((tmp_path / "corpus" / name).read_bytes()).hexdigest() for name in DIGESTS} == DIGESTS
