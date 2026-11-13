"""评测领域公开类型，保持历史 JSON 字段。"""

from typing import Literal, NotRequired, TypedDict

EvalProvider = Literal["fixture", "model", "graph"]
CaseOutcome = Literal["matched", "mismatched", "error"]
RunStatus = Literal["completed", "model_unavailable", "no_predictions"]


class CaseRecord(TypedDict):
    """一题的原始评测记录；SQL、问题和错误详情只写入非公开结果。"""

    index: int
    question_id: int | str | None
    database_id: str
    question: str
    evidence_used: bool
    valid_sql: bool
    execution_match: bool
    outcome: NotRequired[CaseOutcome]
    latency_ms: NotRequired[float]
    predicted_sql: NotRequired[str]
    error: NotRequired[str]
    error_type: NotRequired[str]
    graph_run_id: NotRequired[str]
    row_count: NotRequired[int]
    frozen: NotRequired[bool]


class EvalSummary(TypedDict):
    """供 CLI、原始结果和进度事件复用的累计计数。"""

    total: int
    success: int
    failure: int
    mismatch: int
    error: int
    no_prediction: int
    valid_sql: int


class EvalReport(TypedDict):
    """保留既有 JSON 字段的整批评测结果。"""

    dataset: str
    dataset_sha256: str
    provider: EvalProvider
    status: RunStatus
    run_id: str
    summary: EvalSummary
    evidence_mode: str | None
    model: str | None
    model_base_url: str | None
    model_temperature: float | None
    prompt_version: str | None
    count: int
    max_rows: int
    query_timeout_ms: int
    max_response_bytes: int
    valid_sql_rate: float | None
    execution_accuracy: float | None
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    records: list[CaseRecord]
    graph_version: NotRequired[str]
    workers: NotRequired[int]

