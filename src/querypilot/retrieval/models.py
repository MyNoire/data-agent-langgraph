"""检索的请求、结果和错误类型。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from querypilot.knowledge.api import Hash, KnowledgeScope, ObjectID

from . import _rerank

# 每种检索方法的配置版本，写进检索结果，评测报告据此追溯。任一部分改变时升对应的版本号：
# r3-bm25-v1：下面的查询词配置 + pg_search BM25；
# r4-vector-v1：按 Embedding Profile 对查询嵌入一次，余弦距离精确全表扫描；
# r4-hybrid-v1：r3-bm25-v1 与 r4-vector-v1 各取 50 个候选，RRF k=60、两路等权、同分按 chunk_key。
# 重排过的结果在方法的版本后面拼上重排的版本，见 _rerank.RERANK_CONFIG_VERSION。
RETRIEVAL_CONFIG_VERSIONS = {"lexical": "r3-bm25-v1", "vector": "r4-vector-v1", "hybrid": "r4-hybrid-v1"}

# 查询词准备的固定配置。分词器加载时会逐项核对，不一致就报 configuration_error，
# 保证同一句查询每次切出相同的词。
NORMALIZATION_VERSION = "ascii-han-boundaries-v1"
TOKENIZER_VERSION = "0.42.1"
TOKENIZER_DICTIONARY_SHA256 = "7197c3211ddd98962b036cdf40324d1ea2bfaa12bd028e68faa70111a88e12a8"
STOPWORDS_VERSION = "zh-courtesy-v1"
STOPWORDS_SHA256 = "fede53918dc355e74fe89e5070a04ce6a84dd7a1077d96455026b0d9bc7c99bd"

KnowledgeType = Literal["table", "column", "relationship"]
# lexical：BM25；vector：查询嵌入后按余弦距离精确扫描当前 Embedding Profile 的向量；
# hybrid：BM25 与向量两路用 RRF 融合。
RetrievalMethod = Literal["lexical", "vector", "hybrid"]
TopK = Annotated[int, Field(strict=True, ge=1, le=50)]


class KnowledgeRetrievalError(ValueError):
    """检索失败。error_type 是读取层的错误类别，或检索自己的 input_error、embedding_missing、
    embedding_error、rerank_error。

    消息是固定的安全文本，不包含查询原文；底层原因通过 raise ... from 保留。
    """

    def __init__(self, error_type: str, message: str = "检索输入不符合协议"):
        super().__init__(message)
        self.error_type = error_type


class RetrievalModel(BaseModel):
    """检索模型的公共配置：拒绝多余字段，错误信息不回显输入。"""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class ScopeFile(RetrievalModel):
    """命令行用的范围文件格式。"""

    format_version: Literal["querypilot-retrieval-scope-v1"] = "querypilot-retrieval-scope-v1"
    scope: KnowledgeScope


class RetrievalRequest(RetrievalModel):
    """一次检索请求：查询原文、Knowledge Scope、检索方法、是否重排、top_k 和要找的知识类型。

    rerank 与 method 互相独立：开启后先按 method 取候选，再交给 Reranker 重新排序。
    """

    query: Annotated[str, Field(min_length=1, max_length=2000)]
    scope: KnowledgeScope
    method: RetrievalMethod = "lexical"
    rerank: bool = False
    top_k: TopK = 10
    knowledge_types: Annotated[list[KnowledgeType], Field(min_length=1, max_length=3)] = Field(
        default_factory=lambda: ["table", "column", "relationship"]
    )

    @field_validator("query")
    @classmethod
    def check_query(cls, value: str) -> str:
        """拒绝空白、NUL 和无法编码为 UTF-8 的查询（它们无法安全传给 PG）；原文不改。"""
        if not value.strip() or "\x00" in value:
            raise ValueError("查询必须非空白且不含 NUL")
        value.encode("utf-8")
        return value

    @field_validator("knowledge_types")
    @classmethod
    def check_unique_types(cls, value: list[KnowledgeType]) -> list[KnowledgeType]:
        """知识类型不能重复。"""
        if len(value) != len(set(value)):
            raise ValueError("知识类型不能重复")
        return value

    @model_validator(mode="after")
    def check_rerank_top_k(self) -> "RetrievalRequest":
        """开启重排时 top_k 不能超过候选数：重排只给这些候选打分，超出的部分没有相关性分数。"""
        if self.rerank and self.top_k > _rerank.RERANK_CANDIDATES:
            raise ValueError(f"开启重排时 top_k 不能超过 {_rerank.RERANK_CANDIDATES}")
        return self


class RetrievalHit(RetrievalModel):
    """一个 Hit：命中的 Chunk 原字段，加上本次的 rank 和分数。

    score 的含义随检索方法而定，不同方法的分数不能直接比较：
    lexical 为 BM25 分（≥ 0）；vector 为余弦相似度（-1 到 1）；hybrid 为 RRF 分 Σ 1/(60 + 名次)。
    开启重排后，三种方法的 score 都是 Reranker 给的相关性分数，检索方法自己的分数不保留。

    三个定位字段（schema_name、table_name、column_name）随 knowledge_type 而定：
    - table：table_name 是这张表，column_name 为 None。
    - column：table_name 是这一列所属的表，column_name 是列名。
    - relationship：三个字段都为 None（一条关系连着两张表）；源表在 chunk_key 里。
    """

    rank: Annotated[int, Field(ge=1)]
    score: Annotated[float, Field(allow_inf_nan=False)]
    chunk_id: ObjectID
    chunk_key: str
    knowledge_type: KnowledgeType
    title: str
    content: str
    content_hash: Hash
    schema_name: str | None = None
    table_name: str | None = None
    column_name: str | None = None


class RetrievalResult(RetrievalModel):
    """一次检索的结果。hits 按 rank 排列；没有 Hit 时 outcome 为 no_match。

    method 是实际使用的检索方法；embedding_profile_id 是向量所属的 Embedding Profile，lexical 时为 None；
    rerank_profile_id 是重排所用的 Rerank Profile，请求没有开启重排时为 None。
    """

    result_version: Literal["querypilot-retrieval-result-v3"] = "querypilot-retrieval-result-v3"
    request_id: str
    outcome: Literal["matched", "no_match"]
    scope: KnowledgeScope
    method: RetrievalMethod = "lexical"
    embedding_profile_id: str | None = None
    rerank_profile_id: str | None = None
    normalization_version: str = NORMALIZATION_VERSION
    query_sha256: Hash
    top_k: TopK
    knowledge_types: list[KnowledgeType]
    hit_count: int
    hits: list[RetrievalHit]
    elapsed_ms: Annotated[float, Field(ge=0)]

    @computed_field
    @property
    def retrieval_config_version(self) -> str:
        """本次检索的配置版本：方法的版本（见 RETRIEVAL_CONFIG_VERSIONS），重排过的结果再拼上重排的版本。"""
        version = RETRIEVAL_CONFIG_VERSIONS[self.method]
        return f"{version}+{_rerank.RERANK_CONFIG_VERSION}" if self.rerank_profile_id else version
