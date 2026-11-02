"""完整知识操作与领域类型的明确公开面。"""

from .models import (
    CatalogModel,
    ColumnSchema,
    ForeignKeySchema,
    TableSchema,
    Catalog,
)
from .description_models import (
    DescriptionSource,
    SourceRecord,
    ColumnDescription,
    ImportWarning,
    EnrichedCatalog,
    DescriptionImportResult,
)
from ._descriptions import DescriptionError, enrich_catalog
from ._sqlite import CatalogError, extract_catalog
from ._corrections import CorrectionError, build_curated_catalog as _build_curated_catalog
from ._source_verification import SourceVerification, SourceVerificationError, verify_sources

from pathlib import Path
from hashlib import sha256
from querypilot.runtime.api import RunContext, Settings
from .models import CatalogModel


class CurationSummary(CatalogModel):
    """输入：批准修正的实际结果；返回：与原摘要字段等价的类型。"""

    status: str
    database_id: str
    changes: int
    rules: int
    remaining_warnings: int


def _input_hashes(base_file: Path, source_dir: Path, plan_file: Path) -> list[str]:
    """输入：修正来源；返回：可读文件的字节指纹；缺失输入仍由领域操作正式报错。"""
    hashes = []
    for path in [Path(base_file), Path(plan_file), *sorted(Path(source_dir).glob("*.csv"))]:
        try:
            hashes.append(sha256(path.read_bytes()).hexdigest())
        except OSError:
            # 此处只采集可用追溯信息，完整领域验证仍在运行内执行并保留原错误。
            continue
    return hashes


def curate_catalog(base_file: Path, source_dir: Path, plan_file: Path, output_dir: Path,
                   *, run_context: RunContext | None = None, settings: Settings | None = None) -> CurationSummary:
    """执行一次完整的批准修正并记录运行。

    参数：base_file/source_dir/plan_file/output_dir 为原输入和新目标；run_context 可复用上层运行，settings 配置运行目录。
    返回：修正摘要，字段保持原 status/database_id/changes/rules/remaining_warnings。
    异常：CorrectionError 或 I/O 错误；运行产物单独失败为 RunArtifactError。
    副作用：只写新目录；未传上下文时自行创建关闭，传入时不重复发布或关闭。
    """
    from querypilot.runtime.api import task_run
    owned = run_context is None
    with task_run("knowledge_catalog", settings, run_context=run_context,
                  command="knowledge catalog curate",
                  config={"input_hashes": _input_hashes(base_file, source_dir, plan_file)}) as context:
        context.event("curation_started", status="started")
        result = CurationSummary.model_validate(_build_curated_catalog(base_file, source_dir, plan_file, output_dir))
        context.event("curation_finished", status="success")
        if owned:
            context.complete(result)
        return result


__all__ = [
    "CatalogModel",
    "ColumnSchema",
    "ForeignKeySchema",
    "TableSchema",
    "Catalog",
    "DescriptionSource",
    "SourceRecord",
    "ColumnDescription",
    "ImportWarning",
    "EnrichedCatalog",
    "DescriptionImportResult",
    "CatalogError",
    "DescriptionError",
    "CorrectionError",
    "SourceVerification",
    "SourceVerificationError",
    "CurationSummary",
    "extract_catalog",
    "enrich_catalog",
    "curate_catalog",
    "verify_sources",
]
