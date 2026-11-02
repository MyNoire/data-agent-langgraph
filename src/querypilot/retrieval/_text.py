"""按需准备固定中文资源与查询词项；不执行候选匹配或排名。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from importlib import import_module, metadata, resources
import re
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from querypilot.retrieval.models import (
    STOPWORDS_SHA256,
    STOPWORDS_VERSION,
    TOKENIZER_DICTIONARY_SHA256,
    TOKENIZER_VERSION,
)
from querypilot.retrieval.models import KnowledgeRetrievalError, LexicalProfile

if TYPE_CHECKING:
    from jieba import Tokenizer


_ASCII_LOWERCASE = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_QUERY_BOUNDARIES = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")
_QUERY_FRAGMENTS = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+")


@dataclass(frozen=True)
class PreparedQuery:
    """内部查询边界表示；片段保留原顺序及重复，尚未做中文分词或过滤。"""

    normalized: str
    fragments: tuple[str, ...]
    ascii_terms: tuple[str, ...]
    han_fragments: tuple[str, ...]


def prepare_query(query: str) -> PreparedQuery:
    """在 Python 中规范化已通过 RetrievalRequest 检查的原查询。

    参数：query 为原始查询字符串；只将 ASCII 大写转小写，保留 a-z、
    0-9 和 U+4E00～U+9FFF，其余字符作为词界并合并空格。
    返回：规范化文本、跨语言原顺序片段，以及英文数字词和连续中文片段；
    不过滤、去重、截取词项，也不修改调用方保留的原始查询。
    异常：没有支持字符时抛出 KnowledgeRetrievalError(input_error)。
    副作用：无；不连接数据库、不加载分词资源、不计算查询摘要或检索排名。
    """
    normalized = _QUERY_BOUNDARIES.sub(" ", query.translate(_ASCII_LOWERCASE)).strip()
    fragments = tuple(_QUERY_FRAGMENTS.findall(normalized))
    if not fragments:
        raise KnowledgeRetrievalError("input_error")
    return PreparedQuery(
        normalized=normalized,
        fragments=fragments,
        ascii_terms=tuple(fragment for fragment in fragments if fragment.isascii()),
        han_fragments=tuple(fragment for fragment in fragments if not fragment.isascii()),
    )


@dataclass(frozen=True)
class TextResources:
    """内部固定资源：独立分词器、只读表达词集合和已经核对的配置。"""

    tokenizer: Tokenizer
    stopwords: frozenset[str]
    profile: LexicalProfile


@lru_cache(maxsize=1)
def load_text_resources() -> TextResources:
    """按需加载并校验当前进程使用的固定中文资源。

    参数：无；调用方只在实际准备中文查询词时使用。
    返回：独立于 jieba 全局实例的分词器、只读停用词集合和固定 profile；
    成功结果在进程内缓存，测试可调用 load_text_resources.cache_clear() 清除。
    异常：依赖、版本、资源摘要或初始化失败时抛出
    KnowledgeRetrievalError(configuration_error)，并保留原始异常链；失败不缓存。
    副作用：首次调用导入可选 jieba、读取随包资源并初始化分词器；
    不加载用户词典、不访问网络、不安装全局日志处理器。
    """
    try:
        if metadata.version("jieba") != TOKENIZER_VERSION:
            raise ValueError("jieba 安装版本与固定配置不一致")
        jieba = import_module("jieba")
        if jieba.__version__ != TOKENIZER_VERSION:
            raise ValueError("jieba 模块版本与固定配置不一致")

        dictionary = resources.files(jieba).joinpath("dict.txt")
        if sha256(dictionary.read_bytes()).hexdigest() != TOKENIZER_DICTIONARY_SHA256:
            raise ValueError("jieba 随包词典摘要与固定配置不一致")
        stopwords_resource = resources.files("querypilot.retrieval").joinpath(
            "resources", f"{STOPWORDS_VERSION}.txt"
        )
        stopwords_bytes = stopwords_resource.read_bytes()
        if sha256(stopwords_bytes).hexdigest() != STOPWORDS_SHA256:
            raise ValueError("中文表达词资源摘要与固定配置不一致")
        stopwords = frozenset(stopwords_bytes.decode("utf-8").splitlines())

        with resources.as_file(dictionary) as dictionary_path:
            tokenizer = jieba.Tokenizer(str(dictionary_path))
            # 不信任已有的全局磁盘缓存，确保本次初始化使用刚核对的随包词典。
            with TemporaryDirectory(prefix="querypilot-jieba-") as cache_directory:
                tokenizer.tmp_dir = cache_directory
                tokenizer.initialize()
            tokenizer.tmp_dir = None
        return TextResources(tokenizer, stopwords, LexicalProfile())
    except Exception as exc:
        raise KnowledgeRetrievalError("configuration_error", "检索分词资源不可用") from exc


def _prepare_han_terms(fragment: str, resources: TextResources) -> list[str]:
    """对一个已规范化的中文片段分词并轻过滤。

    参数：fragment 为 prepare_query 提取的一个连续中文片段；
    resources 为 load_text_resources() 已核对的固定独立分词器与表达词集合。
    返回：按固定搜索模式、HMM=False 分词后，逐词精确过滤表达词和
    空白/非支持字符的词列表；保持分词顺序与重复，统一去重由外层负责。
    保留单字、业务词和状态词，不从复合词内部删除表达字。
    异常：分词器运行异常原样上抛，供上层统一处理。
    副作用：仅使用已加载资源，不连接 PG、不改写原查询，
    不在这里判断英文词有效性、拼接 OR、匹配候选或计算排名。
    """
    result: list[str] = []
    for word in resources.tokenizer.cut_for_search(fragment, HMM=False):
        if word not in resources.stopwords and re.fullmatch(r"[\u4e00-\u9fff]+", word):
            result.append(word)
    return result


def _prepare_query_terms(prepared: PreparedQuery) -> list[str]:
    """将已规范化查询的中英文片段组织成完整检索词列表。

    参数：prepared 为 prepare_query 返回的对象；fragments 保留原始片段顺序，
    ascii_terms/han_fragments 是分类视图，不决定最终合并顺序。
    返回：英文数字片段直接保留，中文片段委托 _prepare_han_terms；
    合并后按首次出现全局去重的词列表，不排序、不截取关键词数量。
    异常：过滤后没有有效词时抛 KnowledgeRetrievalError(input_error)；
    中文资源失败沿已有 configuration_error 传播，不舍弃中文后继续成功。
    副作用：只在首次遇到中文片段时加载固定资源，同一次准备复用资源；
    不连接数据库、不判断知识是否匹配、不改写原始查询或公开模型。
    """
    resources: TextResources | None = None
    seen: set[str] = set()
    result: list[str] = []
    for fragment in prepared.fragments:
        if fragment.isascii():
            words = [fragment]
        else:
            if resources is None:
                resources = load_text_resources()
            words = _prepare_han_terms(fragment, resources)

        for word in words:
            if word not in seen:
                seen.add(word)
                result.append(word)

    if not result:
        raise KnowledgeRetrievalError("input_error")
    return result


