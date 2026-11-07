"""知识存储数据契约；与原离线 corpus 身份、对象字段分开。"""

from typing import Literal

from pydantic import ConfigDict, Field

from querypilot.knowledge.knowledge_corpus.api import (
    CorpusModel, CorpusManifest, ValidationReport, CorpusDocument, CorpusChunk,
    CorpusRelation, Counts, Hash,
)

STORAGE_VERSION = "knowledge-storage-v1"
DIGEST_VERSION = "knowledge-import-digests-v1"
IMPORT_CHECKS = [
    "source_verified", "registered_manifest_match", "object_set_match",
    "all_original_fields_match", "references_match",
]
UNVERIFIED = [
    "embedding", "online_retrieval", "inferred_business_truth",
    "source_authenticity", "text2sql_quality",
]




class VerificationDigests(CorpusModel):
    """输入：数据库两个规范载荷的摘要；输出：不含正文的验收指纹。"""

    verification_digest_version: Literal["knowledge-import-digests-v1"] = DIGEST_VERSION
    projection_digest: Hash
    timestamps_digest: Hash


class ImportSummary(VerificationDigests):
    """输入：实际已完成的数据库操作；输出：安全 CLI 成功摘要。"""

    result_version: Literal["knowledge-import-result-v1", "knowledge-import-verification-v1"]
    outcome: Literal["imported", "reused", "verified"]
    corpus_status: Literal["stored"] = "stored"
    source_status: Literal["validated"] = "validated"
    corpus_id: Hash
    database_id: str
    schema_version: Hash
    core_digest: Hash
    counts: Counts
    storage_contract_version: Literal["knowledge-storage-v1"] = STORAGE_VERSION
    migration_version: Literal["002"] = "002"
    import_run_id: str
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)
    checks: list[str] = Field(default_factory=lambda: list(IMPORT_CHECKS))
    unverified_capabilities: list[str] = Field(default_factory=lambda: list(UNVERIFIED))


class ImportVerificationSummary(ImportSummary):
    """输入：只读复验完成结果；输出：固定 verified 协议。"""

    result_version: Literal["knowledge-import-verification-v1"] = "knowledge-import-verification-v1"
    outcome: Literal["verified"] = "verified"


class ManagementSummary(CorpusModel):
    """输入：管理命令的实际版本/结果；输出：不含凭据的结构摘要。"""

    command: Literal["bootstrap", "migrate", "check"]
    outcome: Literal["applied", "reused", "checked"]
    server_version: str
    vector_version: str
    migration_version: str | None


from typing import Annotated
from pydantic import BaseModel, field_validator


class ReadModel(BaseModel):
    """输入：严格读取协议；返回：隐藏错误输入的模型；不连接数据库。"""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


def validate_text(value: str) -> str:
    """输入：范围标识；返回：原字符串；拒绝空白/NUL/编码错误，无 I/O。"""
    if not value.strip() or "\x00" in value:
        raise ValueError("文本必须非空白且不含 NUL")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("文本必须可编码为 UTF-8") from exc
    return value


NonEmpty = Annotated[str, Field(min_length=1)]


class KnowledgeScope(ReadModel):
    """输入：完整三元范围；输出：未改名的范围；不证明授权或登记已存在。"""

    corpus_id: Hash
    database_id: NonEmpty
    schema_version: Hash

    @field_validator("database_id")
    @classmethod
    def check_database_id(cls, value: str) -> str:
        """输入：原业务库标识；返回：原值；异常：无效文本 ValueError；无 I/O。"""
        return validate_text(value)



class ReadLimits(ReadModel):
    """输入：读取方已确认的预算；返回：有界上限，不包含检索策略。"""

    max_scope_chunks: int = Field(default=5000, ge=1, le=5000)
    max_search_bytes: int = Field(default=8388608, ge=1, le=8388608)
    max_result_bytes: int = Field(default=1048576, ge=1, le=1048576)


class ReadView(ReadModel):
    """输入：同一事务的登记范围与容量；返回：临时摘要，不证明授权或完整正文。"""

    scope: KnowledgeScope
    counts: Counts
    search_bytes: int


class SearchIndexProfile(ReadModel):
    """输入：已核对的实际搜索设施；返回：不含知识正文的可复现配置。"""

    profile_version: Literal["pg-search-bm25-v1"] = "pg-search-bm25-v1"
    extension_name: Literal["pg_search"] = "pg_search"
    extension_version: Literal["0.26.4"] = "0.26.4"
    postgres_major: Literal[18] = 18
    index_schema: str = "querypilot_kb"
    index_name: Literal["knowledge_chunks_bm25_idx"] = "knowledge_chunks_bm25_idx"
    index_method: Literal["paradedb"] = "paradedb"
    index_definition_sha256: Hash
    tokenizer_resources: Literal["pg_search-0.26.4-bundled"] = "pg_search-0.26.4-bundled"
    search_fields: dict[str, str]
    filter_fields: tuple[str, ...]
    tokenizer_options: dict[str, str]


class SearchIndexState(ReadModel):
    """输入：现场设施与全索引语料摘要；返回：同一事务内的安全状态。"""

    search_index_profile: SearchIndexProfile
    index_corpus_fingerprint: Hash
    indexed_chunks: int = Field(ge=0)
    fingerprint_version: Literal["index-corpus-sha256-v1"] = "index-corpus-sha256-v1"


class SearchManagementSummary(SearchIndexState):
    """输入：实际完成的设施管理；返回：准备或只读检查结果，不改变R2摘要。"""

    command: Literal["prepare-search", "check-search"]
    outcome: Literal["applied", "reused", "checked"]


class KnowledgeReadError(ValueError):
    """有范围读取的安全错误；调用方可保留原 cause 并映射领域类别。"""

    def __init__(self, error_type: str):
        """输入：受控类别；返回：安全异常；无 I/O。"""
        messages = {
            "scope_not_found": "指定知识语料不存在",
            "scope_mismatch": "知识语料与数据库或结构版本不对应",
            "knowledge_state_error": "知识登记、引用或内容不满足读取前置",
            "capacity_exceeded": "知识范围或结果超过本版本资源预算",
            "configuration_error": "知识读取配置或可选驱动不可用",
            "migration_conflict": "知识存储结构或权限与协议不兼容",
            "connection_failed": "无法连接知识库", "permission_denied": "知识库读取权限不足",
            "lock_timeout": "知识库锁等待超时", "statement_timeout": "知识库语句执行超时",
            "database_error": "知识库读取失败",
        }
        super().__init__(messages[error_type])
        self.error_type = error_type
