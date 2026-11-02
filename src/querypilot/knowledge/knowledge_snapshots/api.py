"""完整知识操作与领域类型的明确公开面。"""

from .draft import KnowledgeDraft
from .models import (
    BuildConfig, BuildSummary, VerificationSummary, VerifiedCorpus,
    CorpusModel, CorpusManifest, ValidationReport, CorpusDocument, CorpusChunk,
    CorpusRelation, Counts, Hash, ObjectID,
)
from .identity import canonical_json, digest, content_hash, parse_json
from ._render import KnowledgeError, render_knowledge
from ._corpus import (
    CorpusError,
    load_verified_corpus as _load_verified_corpus,
    verify_knowledge_corpus as _verify_knowledge_corpus,
    build_knowledge_corpus as _build_knowledge_corpus,
)

from pathlib import Path
from querypilot.runtime.api import RunContext, Settings
def build_snapshot(bundle_dir: Path, semantic_file: Path, output_dir: Path,
                   database_id: str, config: BuildConfig | None = None, *, run_context: RunContext | None = None,
                   settings: Settings | None = None) -> BuildSummary:
    """从已批准来源构建或复用完整快照。

    参数：批准包、语义文件、新目标、数据库标识和 BuildConfig；run_context 可复用上层运行，settings 控制运行根。
    返回：通过发布后核验的 built/reused 摘要。
    异常：CorpusError 或 RunArtifactError；不把快照构建与记录失败混为成功。
    副作用：只创建新快照；已有合法快照只读复用；运行目录独立，只有上下文创建者关闭。
    """
    from querypilot.runtime.api import task_run
    config = config or BuildConfig()
    owned = run_context is None
    with task_run("knowledge_snapshot", settings, run_context=run_context,
                  command="knowledge snapshot build",
                  config={"database_id": database_id, "profile_version": config.profile_version,
                          "input_hashes": _input_hashes(bundle_dir, semantic_file),
                          "config_sha256": digest(canonical_json(config.model_dump(mode="json")))}) as context:
        context.event("snapshot_build_started", status="started")
        result = _build_knowledge_corpus(bundle_dir, semantic_file, output_dir, database_id,
                                        profile_version=config.profile_version)
        context.event("snapshot_build_finished", status="success", corpus_id=result.corpus_id)
        if owned:
            context.complete(result, outcome="reused" if result.outcome == "reused" else "succeeded")
        return result


def _input_hashes(bundle_dir: Path, semantic_file: Path) -> list[str]:
    """输入：来源位置；返回：批准包清单与语义原字节摘要；领域核验负责缺失/污染报错。"""
    hashes = []
    for path in (Path(bundle_dir) / "curation_manifest.json", Path(semantic_file)):
        try:
            hashes.append(digest(path.read_bytes()))
        except OSError:
            continue
    return hashes


def verify_snapshot(corpus_dir: Path) -> VerificationSummary:
    """输入：实际快照目录；返回：完整核验摘要；错误 CorpusError；只读，无隐式任务。"""
    return _verify_knowledge_corpus(corpus_dir)


def load_snapshot(corpus_dir: Path) -> VerifiedCorpus:
    """输入：实际快照目录；返回：本次持有对象；错误 CorpusError；每次重新读取核验。"""
    return _load_verified_corpus(corpus_dir)


__all__ = [
    "KnowledgeDraft",
    "BuildConfig",
    "BuildSummary",
    "VerificationSummary",
    "VerifiedCorpus",
    "CorpusModel",
    "CorpusManifest",
    "ValidationReport",
    "CorpusDocument",
    "CorpusChunk",
    "CorpusRelation",
    "Counts",
    "Hash",
    "ObjectID",
    "CorpusError",
    "KnowledgeError",
    "render_knowledge",
    "build_snapshot",
    "verify_snapshot",
    "load_snapshot",
    "canonical_json",
    "digest",
    "content_hash",
    "parse_json",
]
