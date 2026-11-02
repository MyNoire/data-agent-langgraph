"""R3 严格数据契约；不导入可选数据库驱动，不负责搜索或推断来源。"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from querypilot.knowledge.api import content_hash
from querypilot.knowledge.api import Hash, ObjectID, KnowledgeScope, SearchIndexProfile
from ._profile import (
    NORMALIZATION_VERSION,
    RETRIEVAL_CONFIG_VERSION,
    STOPWORDS_SHA256,
    STOPWORDS_VERSION,
    TOKENIZER_DICTIONARY_SHA256,
    TOKENIZER_HMM,
    TOKENIZER_MODE,
    TOKENIZER_VERSION,
)

KnowledgeType = Literal["table", "column", "relationship"]
PathPart = str | int
TopK = Annotated[int, Field(strict=True, ge=1, le=50)]
Elapsed = Annotated[float, Field(ge=0, allow_inf_nan=False)]
NonEmpty = Annotated[str, Field(min_length=1)]
RequestID = Annotated[str, Field(pattern=r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$")]
ErrorType = Literal[
    "input_error", "scope_not_found", "scope_mismatch", "knowledge_state_error",
    "capacity_exceeded", "configuration_error", "migration_conflict",
    "connection_failed", "permission_denied", "lock_timeout", "statement_timeout",
    "database_error", "result_write_failed",
]
SAFE_MESSAGES: dict[str, str] = {
    "input_error": "检索输入不符合协议",
    "scope_not_found": "指定知识快照不存在",
    "scope_mismatch": "知识快照与数据库或结构版本不对应",
    "knowledge_state_error": "知识登记、引用或内容不满足读取前置",
    "capacity_exceeded": "知识范围或结果超过本版本资源预算",
    "configuration_error": "知识读取配置或可选驱动不可用",
    "migration_conflict": "知识存储结构或权限与协议不兼容",
    "connection_failed": "无法连接知识库",
    "permission_denied": "知识库读取权限不足",
    "lock_timeout": "知识库锁等待超时",
    "statement_timeout": "知识库语句执行超时",
    "database_error": "知识库读取失败",
    "result_write_failed": "检索结果或事件无法输出",
}


class RetrievalModel(BaseModel):
    """输入：协议字段；输出：严格模型；错误文本隐藏输入，仍禁止直接记录 errors()。"""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


def validate_text(value: str) -> str:
    """校验可安全传给 PG 的文本。

    参数：value 为已由严格类型检查确认的字符串。
    返回：原值，不裁剪、不改大小写。
    异常：ValueError 表示空白、NUL 或不能编码为 UTF-8；无副作用。
    """
    if not value.strip() or "\x00" in value:
        raise ValueError("文本必须非空白且不含 NUL")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("文本必须可编码为 UTF-8") from exc
    return value




class ScopeFile(RetrievalModel):
    """输入：显式固定范围配置；输出：完整 scope；不包含凭据或查询。"""

    format_version: Literal["querypilot-retrieval-scope-v1"] = "querypilot-retrieval-scope-v1"
    scope: KnowledgeScope


class RetrievalRequest(RetrievalModel):
    """输入：查询、完整范围与限制；输出：检索请求；有效词项由 Python 准备。"""

    query: Annotated[str, Field(min_length=1, max_length=2000)]
    scope: KnowledgeScope
    top_k: TopK = 10
    knowledge_types: Annotated[list[KnowledgeType], Field(min_length=1, max_length=3)] = Field(
        default_factory=lambda: ["table", "column", "relationship"]
    )

    @field_validator("query")
    @classmethod
    def check_query(cls, value: str) -> str:
        """输入：原查询；返回：原文；异常：编码/空白 ValueError；不执行分词。"""
        return validate_text(value)

    @field_validator("knowledge_types")
    @classmethod
    def check_unique_types(cls, value: list[KnowledgeType]) -> list[KnowledgeType]:
        """输入：类型列表；返回：原顺序；异常：重复类型 ValueError；无副作用。"""
        if len(value) != len(set(value)):
            raise ValueError("知识类型不能重复")
        return value


class LexicalProfile(RetrievalModel):
    """输入：BM25 固定查询配置；输出：不可变配置；不读取或加载可选依赖。"""

    model_config = ConfigDict(frozen=True)

    retrieval_config_version: Literal["r3-bm25-v1"] = RETRIEVAL_CONFIG_VERSION
    normalization_version: Literal["ascii-han-boundaries-v1"] = NORMALIZATION_VERSION
    tokenizer_version: Literal["0.42.1"] = TOKENIZER_VERSION
    tokenizer_mode: Literal["search"] = TOKENIZER_MODE
    tokenizer_hmm: Literal[False] = TOKENIZER_HMM
    tokenizer_dictionary_sha256: Hash = TOKENIZER_DICTIONARY_SHA256
    stopwords_version: Literal["zh-courtesy-v1"] = STOPWORDS_VERSION
    stopwords_sha256: Hash = STOPWORDS_SHA256
    max_scope_chunks: Literal[5000] = 5000
    max_search_bytes: Literal[8388608] = 8388608
    max_result_bytes: Literal[1048576] = 1048576

    @field_validator("tokenizer_hmm", mode="before")
    @classmethod
    def check_hmm_type(cls, value: object) -> object:
        """要求显式 HMM 配置为布尔值；返回原值，非布尔值抛出 ValueError。"""
        if type(value) is not bool:
            raise ValueError("分词 HMM 配置必须是布尔值")
        return value

    @field_validator("tokenizer_dictionary_sha256")
    @classmethod
    def check_dictionary_hash(cls, value: str) -> str:
        """校验调用方摘要等于固定词典摘要；返回原值，否则抛出 ValueError；无 I/O。"""
        if value != TOKENIZER_DICTIONARY_SHA256:
            raise ValueError("分词词典摘要不属于当前固定配置")
        return value

    @field_validator("stopwords_sha256")
    @classmethod
    def check_stopwords_hash(cls, value: str) -> str:
        """校验调用方摘要等于固定词表摘要；返回原值，否则抛出 ValueError；无 I/O。"""
        if value != STOPWORDS_SHA256:
            raise ValueError("停用词摘要不属于当前固定配置")
        return value


class RetrievalExecutionProfile(LexicalProfile):
    """内部安全配置：固定查询资源与实际核对的索引配置、全索引语料摘要。

    参数：search_index_profile、index_corpus_fingerprint 来自知识存储的
    SearchIndexState，不由查询内容或请求范围推断。
    返回：不含原查询、词项、知识正文或凭据的配置模型；不改变公开结果字段。
    异常：配置类型、摘要或固定查询版本不符时抛出 ValidationError。
    副作用：无；模型不证明现场状态仍一致，也不写入 runtime，调用方须先核验设施。
    """

    search_index_profile: SearchIndexProfile
    index_corpus_fingerprint: Hash


class SemanticProfileRef(RetrievalModel):
    """输入：实际存在的推断配置白名单；输出：精简引用；不补造缺失项。"""

    file: str | None = None
    version: str | None = None
    sha256: Hash | None = None


class ProvenanceRef(RetrievalModel):
    """输入：实际关系来源白名单；输出：资料引用；不携带原始记录。"""

    artifact: str | None = None
    artifact_sha256: Hash | None = None
    locator: list[PathPart] | None = None


class SourceRef(RetrievalModel):
    """输入：SQL 摘取的来源字段；输出：可回查指针；不读取或推断来源。"""

    document_id: ObjectID
    document_key: NonEmpty
    source_kind: Literal["derived_catalog"]
    structure_locator: list[PathPart] | None = None
    semantic_origin: str | None = None
    review_status: str | None = None
    semantic_profile: SemanticProfileRef | None = None
    column_source_path: list[PathPart] | None = None
    correction_ids: list[str] = Field(default_factory=list)
    provenance_ref: ProvenanceRef | None = None


class RetrievalHit(RetrievalModel):
    """输入：一条原知识及本次排名；输出：身份、正文和精简来源；不做上下文扩展。"""

    rank: Annotated[int, Field(ge=1)]
    score: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    chunk_id: ObjectID
    chunk_key: NonEmpty
    scope: KnowledgeScope
    knowledge_type: KnowledgeType
    title: NonEmpty
    content: NonEmpty
    content_hash: Hash
    document_id: ObjectID
    document_key: NonEmpty
    schema_name: str | None = None
    table_name: str | None = None
    column_name: str | None = None
    relation_id: ObjectID | None = None
    relation_key: str | None = None
    source_ref: SourceRef

    @model_validator(mode="after")
    def check_original_projection(self) -> Self:
        """输入：当前命中；返回：自身；异常：内容/引用/定位不自洽 ValueError；无 I/O。"""
        if content_hash(self.title, self.content) != self.content_hash:
            raise ValueError("命中原正文与摘要不自洽")
        if (self.document_id, self.document_key) != (self.source_ref.document_id, self.source_ref.document_key):
            raise ValueError("来源父引用不一致")
        if self.knowledge_type == "relationship":
            if any(v is not None for v in (self.schema_name, self.table_name, self.column_name)):
                raise ValueError("关系块必须保留空表列定位")
            if not self.relation_id or not self.relation_key:
                raise ValueError("关系块必须有关系引用")
        else:
            if self.relation_id is not None or self.relation_key is not None:
                raise ValueError("非关系块不能携带关系引用")
            if not self.schema_name or not self.table_name:
                raise ValueError("表列块缺少定位")
            if ((self.knowledge_type == "column" and not self.column_name)
                    or (self.knowledge_type == "table" and self.column_name is not None)):
                raise ValueError("列定位与知识类型不符")
        return self


class RetrievalResult(RetrievalModel):
    """输入：成功检索投影；输出：matched/no_match；大小预算由服务最终序列化检查。"""

    result_version: Literal["querypilot-retrieval-result-v1"] = "querypilot-retrieval-result-v1"
    request_id: RequestID
    outcome: Literal["matched", "no_match"]
    scope: KnowledgeScope
    method: Literal["lexical"] = "lexical"
    retrieval_config_version: Literal["r3-bm25-v1"] = RETRIEVAL_CONFIG_VERSION
    normalization_version: Literal["ascii-han-boundaries-v1"] = NORMALIZATION_VERSION
    query_sha256: Hash
    top_k: TopK
    knowledge_types: Annotated[list[KnowledgeType], Field(min_length=1, max_length=3)]
    hit_count: Annotated[int, Field(ge=0, le=50)]
    hits: list[RetrievalHit]
    elapsed_ms: Elapsed

    @model_validator(mode="after")
    def check_hits(self) -> Self:
        """输入：当前结果；返回：自身；异常：数量/范围/类型/排名错误 ValueError；无 I/O。"""
        if self.hit_count != len(self.hits) or self.hit_count > self.top_k:
            raise ValueError("命中数量不符合请求")
        if (self.outcome == "matched") != bool(self.hits):
            raise ValueError("结果状态与命中不一致")
        if len(set(self.knowledge_types)) != len(self.knowledge_types):
            raise ValueError("结果类型重复")
        if len({hit.chunk_id for hit in self.hits}) != self.hit_count:
            raise ValueError("命中身份重复")
        for rank, hit in enumerate(self.hits, 1):
            if hit.rank != rank or hit.scope != self.scope or hit.knowledge_type not in self.knowledge_types:
                raise ValueError("命中排名、范围或类型不一致")
        return self


class RetrievalError(RetrievalModel):
    """输入：受控失败与操作信息；输出：不含原输入/底层 cause 的错误对象。"""

    result_version: Literal["querypilot-retrieval-error-v1"] = "querypilot-retrieval-error-v1"
    request_id: RequestID
    outcome: Literal["failed"] = "failed"
    error_type: ErrorType
    message: str
    elapsed_ms: Elapsed
    scope: KnowledgeScope | None = None


class KnowledgeRetrievalError(ValueError):
    """受控异常；只允许固定安全消息，原始错误使用 raise ... from exc 保留。"""

    def __init__(self, error_type: ErrorType):
        """输入：已知错误类别；返回：异常对象；异常：未知类别 KeyError；无副作用。"""
        super().__init__(SAFE_MESSAGES[error_type])
        self.error_type = error_type

    @property
    def exit_code(self) -> int:
        """输入：自身；返回：运行失败 3 或协议失败 1；副作用：无。"""
        return 3 if self.error_type in {
            "connection_failed", "permission_denied", "lock_timeout",
            "statement_timeout", "database_error",
        } else 1
