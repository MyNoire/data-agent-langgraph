"""知识存储数据契约；与原离线 corpus 身份、对象字段分开。"""

from typing import Literal

from pydantic import ConfigDict, Field

from querypilot.knowledge.corpus_models import (
    CorpusModel, CorpusManifest, ValidationReport, CorpusDocument, CorpusChunk,
    CorpusRelation, Counts, Hash,
)

STORAGE_VERSION = "knowledge-storage-v1"
DIGEST_VERSION = "knowledge-import-digests-v1"
IMPORT_CHECKS = [
    "source_verified", "registered_manifest_match", "object_set_match",
    "all_original_fields_match", "references_match", "embedding_absent",
]
UNVERIFIED = [
    "embedding", "online_retrieval", "inferred_business_truth",
    "source_authenticity", "text2sql_quality",
]


class VerifiedCorpus(CorpusModel):
    """输入：完整核验的清单/报告/对象；输出：本次调用持有的快照。

    frozen 只禁止字段重绑，不冻结嵌套字典；调用方不得修改或共享嵌套内容。
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    manifest: CorpusManifest
    validation: ValidationReport
    documents: tuple[CorpusDocument, ...]
    chunks: tuple[CorpusChunk, ...]
    relations: tuple[CorpusRelation, ...]


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
    migration_version: Literal["001"] = "001"
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
