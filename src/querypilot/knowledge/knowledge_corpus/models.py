"""R1.4 正式离线语料契约；R1.3 draft 模型保持原样。"""

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from querypilot.knowledge.knowledge_catalog.api import CatalogModel
from .draft import ChunkDraft, DocumentDraft, RelationDraft

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ObjectID = Annotated[str, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")]
NonEmpty = Annotated[str, Field(min_length=1)]
CheckCode = Literal[
    "artifact_completeness", "content_preservation", "deterministic_identity",
    "input_integrity", "scope_and_references",
]
CHECK_CODES = [
    "artifact_completeness", "content_preservation", "deterministic_identity",
    "input_integrity", "scope_and_references",
]
UNVERIFIED = [
    "embedding", "inferred_business_truth", "online_retrieval",
    "source_authenticity", "text2sql_quality",
]


class CorpusModel(CatalogModel):
    """输入为版本化契约字段；输出严格对象，不做隐式类型转换。"""

    model_config = ConfigDict(extra="forbid", strict=True)


class ScopedIdentity(CorpusModel):
    """输入为语料及结构摘要；输出对象所属范围。"""

    corpus_id: Hash
    schema_version: Hash


class CorpusDocument(DocumentDraft, ScopedIdentity):
    """输入为完整草稿及身份；输出正式文档行。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    document_id: ObjectID
    content_hash: Hash


class CorpusChunk(ChunkDraft, ScopedIdentity):
    """输入为完整块及父引用；输出正式块行，关联一致性由核验器检查。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    chunk_id: ObjectID
    document_id: ObjectID
    relation_id: ObjectID | None
    content_hash: Hash


class CorpusRelation(RelationDraft, ScopedIdentity):
    """输入为声明外键及源文档身份；输出保留有序配对的关系行。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    relation_id: ObjectID
    source_document_id: ObjectID


class SourcePolicy(CorpusModel):
    """输入为固定来源政策；输出禁止评测及额外 SQL 示例的策略。"""

    allow_evaluation_questions: Literal[False] = False
    allow_gold_sql: Literal[False] = False
    allow_oracle_evidence: Literal[False] = False
    allow_predictions: Literal[False] = False
    allow_sql_examples: Literal[False] = False


class BuildConfig(CorpusModel):
    """输入为支持的规则版本；输出可进入 corpus 身份的配置。"""

    format_version: Literal["knowledge-corpus-config-v1"] = "knowledge-corpus-config-v1"
    profile_version: NonEmpty = "r1-4-v1"
    serializer_version: Literal["querypilot-json-v1"] = "querypilot-json-v1"
    schema_identity_version: Literal["collected-sqlite-schema-v1"] = "collected-sqlite-schema-v1"
    object_identity_version: Literal["corpus-uuid-v1"] = "corpus-uuid-v1"
    renderer_version: Literal["r1-3-compact-context-v2"] = "r1-3-compact-context-v2"
    packager_version: Literal["knowledge-corpus-v1"] = "knowledge-corpus-v1"
    validator_version: Literal["knowledge-corpus-validation-v1"] = "knowledge-corpus-validation-v1"
    source_policy: SourcePolicy = Field(default_factory=SourcePolicy)

    @model_validator(mode="after")
    def check_profile(self) -> Self:
        """检查版本标签。输入：当前配置；输出：自身；异常：空白标签 ValueError。"""
        if not self.profile_version.strip():
            raise ValueError("profile_version 不能仅包含空白")
        return self


class SourceEntry(CorpusModel):
    """输入为获准来源角色和原字节摘要；输出逻辑来源条目。"""

    role: Literal[
        "curation_manifest", "enriched_catalog", "correction_report",
        "import_warnings", "derived_csv", "inferred_semantics",
    ]
    logical_path: NonEmpty
    sha256: Hash


class IdentityDescriptor(CorpusModel):
    """输入为范围、源版本和内容规则；输出 corpus 身份描述。"""

    identity_version: Literal["knowledge-corpus-identity-v1"] = "knowledge-corpus-identity-v1"
    database_scope: list[NonEmpty] = Field(min_length=1, max_length=1)
    schema_versions: dict[str, Hash]
    sources: list[SourceEntry] = Field(min_length=1)
    build_config: BuildConfig


class ChunkCounts(CorpusModel):
    """输入为三类块的实际计数；输出严格数量对象。"""

    table: int = Field(gt=0)
    column: int = Field(gt=0)
    relationship: int = Field(ge=0)


class Counts(CorpusModel):
    """输入为实际对象数量；输出满足覆盖关系的计数。"""

    documents: int = Field(gt=0)
    chunks: int = Field(gt=0)
    relations: int = Field(ge=0)
    chunk_types: ChunkCounts

    @model_validator(mode="after")
    def check_totals(self) -> Self:
        """检查总量。输入：当前计数；输出：自身；异常：不一致 ValueError。"""
        if (sum(self.chunk_types.model_dump().values()) != self.chunks
                or self.chunk_types.table != self.documents
                or self.chunk_types.relationship != self.relations):
            raise ValueError("对象数量与块类型不一致")
        return self


class ArtifactEntry(CorpusModel):
    """输入为核心逻辑路径与文件摘要；输出产物条目。"""

    path: NonEmpty
    sha256: Hash


class CheckResult(CorpusModel):
    """输入为已完成的检查代码；输出通过结果，失败由异常表达。"""

    code: CheckCode
    status: Literal["passed"] = "passed"


class HistoricalReference(CorpusModel):
    """输入为历史材料逻辑引用；输出未复查原字节的可追溯记录。"""

    kind: Literal["original_csv", "base_enriched_catalog", "correction_plan"]
    key: NonEmpty
    sha256: Hash


class RecheckedInputs(CorpusModel):
    """输入为已复查来源路径；输出实际原字节检查范围。"""

    status: Literal["rechecked"] = "rechecked"
    files: list[NonEmpty]


class RecheckedLineage(CorpusModel):
    """输出包内来源记录相互一致的检查性质。"""

    status: Literal["rechecked"] = "rechecked"


class HistoricalInputs(CorpusModel):
    """输入为历史引用列表；输出仅引用而未复查原字节的范围。"""

    status: Literal["referenced_not_rechecked"] = "referenced_not_rechecked"
    references: list[HistoricalReference]


class ProvenanceScope(CorpusModel):
    """输入为实际来源检查范围；输出固定三层来源证据。"""

    snapshot_inputs: RecheckedInputs
    package_lineage: RecheckedLineage
    historical_inputs: HistoricalInputs


class ValidationReport(CorpusModel):
    """输入为重算检查及核心摘要；输出确定性报告。"""

    report_version: Literal["knowledge-corpus-report-v1"] = "knowledge-corpus-report-v1"
    corpus_id: Hash
    core_digest: Hash
    status: Literal["passed"] = "passed"
    counts: Counts
    checks: list[CheckResult]
    provenance_scope: ProvenanceScope
    unverified_capabilities: list[str]

    @model_validator(mode="after")
    def check_scope(self) -> Self:
        """检查固定范围。输入：报告；输出：自身；异常：缺项或重排 ValueError。"""
        if [item.code for item in self.checks] != CHECK_CODES or self.unverified_capabilities != UNVERIFIED:
            raise ValueError("检查或未验证能力列表不符合首版协议")
        return self


class CorpusManifest(CorpusModel):
    """输入为通过检查的核心及报告绑定；输出待独立复验的完成标记。"""

    manifest_version: Literal["knowledge-corpus-manifest-v1"] = "knowledge-corpus-manifest-v1"
    status: Literal["validated"] = "validated"
    corpus_id: Hash
    identity_descriptor: IdentityDescriptor
    counts: Counts
    core_artifacts: list[ArtifactEntry]
    core_digest: Hash
    validation_artifact: ArtifactEntry
    verification_scope: list[CheckCode]

    @model_validator(mode="after")
    def check_scope(self) -> Self:
        """检查声明范围。输入：清单；输出：自身；异常：错误范围 ValueError。"""
        if self.verification_scope != CHECK_CODES or self.validation_artifact.path != "validation.json":
            raise ValueError("清单验证范围或报告路径不正确")
        return self


class BuildRunRecord(CorpusModel):
    """输入为本次操作信息；输出不参与身份和验收的记录。"""

    record_version: Literal["knowledge-corpus-run-v1"] = "knowledge-corpus-run-v1"
    corpus_id: Hash
    built_at: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")]
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)
    git_revision: Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")] | None
    git_dirty: bool | None


class BuildSummary(CorpusModel):
    """输入为已完成调用的身份/数量；输出 built 或 reused 摘要。"""

    outcome: Literal["built", "reused"]
    corpus_id: Hash
    status: Literal["validated"] = "validated"
    counts: Counts
    output_dir: str


class VerificationSummary(CorpusModel):
    """输入为实际核验通过结果；输出无正文的 verified 摘要。"""

    outcome: Literal["verified"] = "verified"
    corpus_id: Hash
    status: Literal["validated"] = "validated"
    counts: Counts
    checks: list[CheckResult]


class VerifiedCorpus(CorpusModel):
    """输入：完整核验的清单/报告/对象；输出：本次调用持有的语料。

    frozen 只禁止字段重绑，不冻结嵌套字典；调用方不得修改或共享嵌套内容。
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    manifest: CorpusManifest
    validation: ValidationReport
    documents: tuple[CorpusDocument, ...]
    chunks: tuple[CorpusChunk, ...]
    relations: tuple[CorpusRelation, ...]

