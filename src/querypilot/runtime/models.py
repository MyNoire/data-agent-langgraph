"""运行记录的固定状态；业务仍拥有各自的结果类型。"""

from typing import Any, TypedDict

OPERATIONS = frozenset({
    "query", "evaluation", "knowledge_catalog", "knowledge_snapshot",
    "knowledge_import", "knowledge_verify", "knowledge_manage", "data_prepare",
})
OPERATION_OUTCOMES = frozenset({
    "not_started", "unknown", "succeeded", "imported", "reused", "verified", "failed",
})


class RunRecord(TypedDict):
    """安全运行记录，不保存问题、SQL 或领域结果正文。"""

    format_version: str
    run_id: str
    operation: str
    command: str
    status: str
    operation_outcome: str
    artifact_outcome: str
    started_at: str
    finished_at: str | None
    result_path: str | None
    error_type: str | None


class SafeConfig(TypedDict, total=False):
    """仅由明确白名单字段构造的配置快照。"""

    format_version: str
    versions: dict[str, Any]
    parameters: dict[str, Any]


class RunArtifactError(RuntimeError):
    """表示运行产物失败，保留安全的实际操作结果，不携带业务正文。"""

    def __init__(self, category: str = "artifact_write_failed", *, run_id: str | None = None,
                 operation_outcome: str = "not_started", artifact_outcome: str = "failed") -> None:
        """初始化安全错误；参数为受控类别、内部标识与已知事实，返回 None。"""
        self.category = category
        self.error_type = category
        self.run_id = run_id
        self.operation_outcome = operation_outcome
        self.artifact_outcome = artifact_outcome
        super().__init__(f"{category}; run_id={run_id}; operation_outcome={operation_outcome}")
