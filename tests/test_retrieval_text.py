"""固定中文资源、查询字符边界与词项准备；不执行候选匹配。"""

from hashlib import sha256
from importlib import metadata, resources
from dataclasses import FrozenInstanceError
import subprocess
import sys
from types import SimpleNamespace

import pytest

from querypilot.retrieval import _text
from querypilot.retrieval import models
from querypilot.retrieval.api import KnowledgeRetrievalError


@pytest.fixture(autouse=True)
def clear_text_resource_cache():
    """输入：无；返回：隔离的资源缓存生命周期；副作用：仅清除本进程加载缓存。"""
    _text.load_text_resources.cache_clear()
    yield
    _text.load_text_resources.cache_clear()


@pytest.mark.parametrize(("query", "normalized", "fragments", "ascii_terms", "han_fragments"), [
    ("  MAIN.ATOM_ID = CL42  ", "main atom id cl42", ("main", "atom", "id", "cl42"),
     ("main", "atom", "id", "cl42"), ()),
    ("请统计各租户2025年订单销售额，Atom_ID 的 CL 编码？", "请统计各租户2025年订单销售额 atom id 的 cl 编码",
     ("请统计各租户", "2025", "年订单销售额", "atom", "id", "的", "cl", "编码"),
     ("2025", "atom", "id", "cl"), ("请统计各租户", "年订单销售额", "的", "编码")),
    ("A中A_中，A", "a中a 中 a", ("a", "中", "a", "中", "a"), ("a", "a", "a"), ("中", "中")),
    ("K Ａ É İ ATOM_1", "atom 1", ("atom", "1"), ("atom", "1"), ()),
    ("一\u9fff \u3400 \U00020000", "一\u9fff", ("一\u9fff",), (), ("一\u9fff",)),
])
def test_prepare_query_uses_python_ascii_han_boundaries_without_losing_fragment_order(
    query, normalized, fragments, ascii_terms, han_fragments,
):
    """输入：原句/限定名/混合边界；返回：原序片段断言；不访问 PG 或分词器。"""
    prepared = _text.prepare_query(query)
    assert prepared.normalized == normalized
    assert prepared.fragments == fragments
    assert prepared.ascii_terms == ascii_terms
    assert prepared.han_fragments == han_fragments
    assert _text.load_text_resources.cache_info().currsize == 0
    with pytest.raises(FrozenInstanceError):
        prepared.normalized = "other"


@pytest.mark.parametrize("query", ["---", "１２３", "ÉK", "🙂", "\u3400\U00020000"])
def test_prepare_query_rejects_no_supported_characters_without_database(query):
    """输入：无支持字符的已校验文本；返回：input_error 断言；无资源或数据库访问。"""
    with pytest.raises(KnowledgeRetrievalError) as caught:
        _text.prepare_query(query)
    assert caught.value.error_type == "input_error"
    assert _text.load_text_resources.cache_info().currsize == 0


def test_han_terms_use_supplied_tokenizer_and_reject_unsupported_output():
    """输入：受控分词输出与词表；返回：委托配置/有效词边界断言，不访问PG。"""
    class Tokenizer:
        """仅提供分词器公开方法，验证调用方使用已提供资源。"""

        def cut_for_search(self, fragment, HMM):
            """输入：一个中文片段与固定模式；返回：含重复和非支持字符的词项。"""
            assert fragment == "中文片段" and HMM is False
            yield from ("请问", "订单", " ", "😀", "abc", "订单", "氯")

    loaded = SimpleNamespace(tokenizer=Tokenizer(), stopwords=frozenset({"请问"}))
    assert _text._prepare_han_terms("中文片段", loaded) == ["订单", "订单", "氯"]


def test_fixed_resources_load_real_independent_tokenizer_and_exact_wordlist():
    """输入：实际随包资源；返回：版本/摘要/独立实例断言；仅初始化本地中文资源。"""
    import jieba

    loaded = _text.load_text_resources()
    assert metadata.version("jieba") == jieba.__version__ == models.TOKENIZER_VERSION == "0.42.1"
    assert models.TOKENIZER_DICTIONARY_SHA256 == "7197c3211ddd98962b036cdf40324d1ea2bfaa12bd028e68faa70111a88e12a8"
    assert models.STOPWORDS_VERSION == "zh-courtesy-v1"
    assert models.STOPWORDS_SHA256 == "fede53918dc355e74fe89e5070a04ce6a84dd7a1077d96455026b0d9bc7c99bd"
    assert sha256(resources.files(jieba).joinpath("dict.txt").read_bytes()).hexdigest() == models.TOKENIZER_DICTIONARY_SHA256
    wordlist = resources.files("querypilot.retrieval").joinpath("resources", "zh-courtesy-v1.txt")
    assert sha256(wordlist.read_bytes()).hexdigest() == models.STOPWORDS_SHA256
    assert loaded.stopwords == frozenset({
        "请", "请问", "帮", "帮忙", "我", "我们", "一下", "的", "了", "呢", "吗", "吧", "谢谢",
    })
    assert isinstance(loaded.stopwords, frozenset)
    assert loaded.tokenizer is not jieba.dt and loaded.tokenizer.initialized
    # 仅验证真实词典能服务独立实例；不执行 T010 的过滤、去重或匹配。
    terms = list(loaded.tokenizer.cut_for_search("请问帮我查询物品的事件", HMM=False))
    assert {"物品", "事件"}.issubset(terms)
    assert _text.load_text_resources() is loaded


@pytest.mark.parametrize(("fragment", "expected"), [
    ("南京市长江大桥", ["南京", "京市", "南京市", "长江", "大桥", "长江大桥"]),
    ("未完成", ["未", "完成"]),
    ("氯", ["氯"]),
])
def test_fixed_python_tokenizer_preserves_verified_boundary_sample_terms(fragment, expected):
    """输入：实际索引探针使用的边界样本；返回：Python 固定资源词项；不替代实库证明。"""
    loaded = _text.load_text_resources()
    assert list(loaded.tokenizer.cut_for_search(fragment, HMM=False)) == expected


def test_missing_optional_tokenizer_is_safe_configuration_error_with_cause(monkeypatch):
    """输入：缺失的可选依赖；返回：安全配置失败及原始异常链；不初始化分词器。"""
    cause = metadata.PackageNotFoundError("private-package-location")

    def missing_version(name):
        """输入：依赖名；异常：模拟安装元数据缺失；不读取环境。"""
        raise cause

    monkeypatch.setattr(_text.metadata, "version", missing_version)
    with pytest.raises(KnowledgeRetrievalError) as caught:
        _text.load_text_resources()
    assert caught.value.error_type == "configuration_error"
    assert caught.value.__cause__ is cause
    assert "private-package-location" not in str(caught.value)
    assert _text.load_text_resources.cache_info().currsize == 0


@pytest.mark.parametrize("version_source", ["distribution", "module"])
def test_version_drift_is_configuration_error(version_source, monkeypatch):
    """输入：安装元数据或模块版本漂移；返回：配置失败与 cause 断言；不初始化资源。"""
    if version_source == "distribution":
        def changed_version(name):
            """输入：依赖名；返回：不受支持的版本；无 I/O。"""
            return "9.9.9"

        monkeypatch.setattr(_text.metadata, "version", changed_version)
    else:
        def changed_module(name):
            """输入：模块名；返回：只用于版本错误边界的替身；不模拟分词结果。"""
            return SimpleNamespace(__version__="9.9.9")

        monkeypatch.setattr(_text, "import_module", changed_module)
    with pytest.raises(KnowledgeRetrievalError) as caught:
        _text.load_text_resources()
    assert caught.value.error_type == "configuration_error"
    assert isinstance(caught.value.__cause__, ValueError)
    assert _text.load_text_resources.cache_info().currsize == 0


@pytest.mark.parametrize("resource_name", ["dictionary", "stopwords"])
@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_missing_or_changed_resource_fails_safely(resource_name, damage, tmp_path, monkeypatch):
    """输入：临时缺失/损坏词典或词表；返回：安全失败及原始 cause；不修改安装资源。"""
    original_files = _text.resources.files
    if resource_name == "dictionary":
        damaged_path = tmp_path / "dict.txt"
    else:
        damaged_path = tmp_path / "resources" / "zh-courtesy-v1.txt"
    if damage == "changed":
        damaged_path.parent.mkdir(parents=True, exist_ok=True)
        damaged_path.write_bytes(b"private-resource-body\n")

    def files_with_damaged_resource(package):
        """输入：资源所属包；返回：目标包临时目录，其他包走真实资源；只读定位。"""
        package_name = package if isinstance(package, str) else package.__name__
        target = "jieba" if resource_name == "dictionary" else "querypilot.retrieval"
        return tmp_path if package_name == target else original_files(package)

    monkeypatch.setattr(_text.resources, "files", files_with_damaged_resource)
    with pytest.raises(KnowledgeRetrievalError) as caught:
        _text.load_text_resources()
    assert caught.value.error_type == "configuration_error"
    assert isinstance(caught.value.__cause__, FileNotFoundError if damage == "missing" else ValueError)
    assert "private-resource-body" not in str(caught.value)
    assert str(tmp_path) not in str(caught.value)
    assert _text.load_text_resources.cache_info().currsize == 0


def test_tokenizer_initialization_failure_is_safe_and_keeps_original_cause(monkeypatch):
    """输入：初始化故障；返回：受控错误和相同 cause；不回退全局分词器。"""
    import jieba

    cause = RuntimeError("private-tokenizer-failure")

    def failed_initialize(self, dictionary=None):
        """输入：分词器与可选词典；异常：模拟初始化失败；不生成词项。"""
        raise cause

    monkeypatch.setattr(jieba.Tokenizer, "initialize", failed_initialize)
    with pytest.raises(KnowledgeRetrievalError) as caught:
        _text.load_text_resources()
    assert caught.value.error_type == "configuration_error"
    assert caught.value.__cause__ is cause
    assert "private-tokenizer-failure" not in str(caught.value)
    assert _text.load_text_resources.cache_info().currsize == 0


def test_imports_do_not_load_optional_tokenizer_or_touch_text_resources():
    """输入：禁止可选依赖和资源定位的独立进程；返回：导入隔离断言；仅启动本地 Python。"""
    program = r'''
import importlib.abc
from importlib import resources
import sys

blocked = {"jieba", "psycopg", "openai", "langchain_openai"}
original_files = resources.files

class BlockOptionalClients(importlib.abc.MetaPathFinder):
    """拦截可选库；导入数据契约和资源模块不应触发它们。"""
    def find_spec(self, fullname, path=None, target=None):
        """输入：模块定位参数；返回：None；异常：禁止的模块抛出 ModuleNotFoundError。"""
        if fullname.split(".")[0] in blocked:
            raise ModuleNotFoundError("Optional client deliberately unavailable", name=fullname)

def forbid_resource_lookup(package):
    """输入：包名或模块；返回：其他包的定位；异常：导入期间定位中文资源失败。"""
    package_name = package if isinstance(package, str) else package.__name__
    if package_name in {"jieba", "querypilot.retrieval"}:
        raise AssertionError("Text resources must load only on demand")
    return original_files(package)

sys.meta_path.insert(0, BlockOptionalClients())
resources.files = forbid_resource_lookup
import querypilot.retrieval.models
import querypilot.retrieval.api
import querypilot.retrieval._text
assert querypilot.retrieval.models.RETRIEVAL_CONFIG_VERSION == "r3-bm25-v1"
assert not blocked.intersection(sys.modules)
assert querypilot.retrieval._text.load_text_resources.cache_info().currsize == 0
prepared = querypilot.retrieval._text.prepare_query("ATOM_ID")
assert prepared.ascii_terms == ("atom", "id")
assert prepared.han_fragments == ()
assert not blocked.intersection(sys.modules)
'''
    completed = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
