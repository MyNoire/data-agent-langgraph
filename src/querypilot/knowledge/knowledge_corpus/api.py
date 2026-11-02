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
def build_corpus(curated_dir: Path, semantic_file: Path, output_dir: Path,
                   database_id: str, config: BuildConfig | None = None, *, run_context: RunContext | None = None,
                   settings: Settings | None = None) -> BuildSummary:
    """从已批准来源构建或复用完整语料。

    参数：整理后目录、语义文件、新目标、数据库标识和 BuildConfig；run_context 可复用上层运行，settings 控制运行根。
    返回：通过发布后核验的 built/reused 摘要。
    异常：CorpusError 或 RunArtifactError；不把语料构建与记录失败混为成功。
    副作用：只创建新语料；已有合法语料只读复用；运行目录独立，只有上下文创建者关闭。
    """
    from querypilot.runtime.api import task_run
    config = config or BuildConfig()
    owned = run_context is None
    with task_run("knowledge_corpus", settings, run_context=run_context,
                  command="knowledge corpus build",
                  config={"database_id": database_id, "profile_version": config.profile_version,
                          "input_hashes": _input_hashes(curated_dir, semantic_file),
                          "config_sha256": digest(canonical_json(config.model_dump(mode="json")))}) as context:
        context.event("corpus_build_started", status="started")
        result = _build_knowledge_corpus(curated_dir, semantic_file, output_dir, database_id,
                                        profile_version=config.profile_version)
        context.event("corpus_build_finished", status="success", corpus_id=result.corpus_id)
        if owned:
            context.complete(result, outcome="reused" if result.outcome == "reused" else "succeeded")
        return result


def _input_hashes(curated_dir: Path, semantic_file: Path) -> list[str]:
    """输入：来源位置；返回：整理后目录清单与语义原字节摘要；领域核验负责缺失/污染报错。"""
    hashes = []
    for path in (Path(curated_dir) / "curation_manifest.json", Path(semantic_file)):
        try:
            hashes.append(digest(path.read_bytes()))
        except OSError:
            continue
    return hashes


def verify_corpus(corpus_dir: Path) -> VerificationSummary:
    """输入：实际语料目录；返回：完整核验摘要；错误 CorpusError；只读，无隐式任务。"""
    return _verify_knowledge_corpus(corpus_dir)


def load_corpus(corpus_dir: Path) -> VerifiedCorpus:
    """输入：实际语料目录；返回：本次持有对象；错误 CorpusError；每次重新读取核验。"""
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
    "build_corpus",
    "verify_corpus",
    "load_corpus",
    "canonical_json",
    "digest",
    "content_hash",
    "parse_json",
]
