"""知识存储的完整操作；可选 PostgreSQL 驱动仅在选中操作后加载。"""

from pathlib import Path
from hashlib import sha256
from querypilot.runtime.api import RunContext

from querypilot.knowledge.knowledge_corpus.api import CorpusError, load_corpus
from querypilot.knowledge.knowledge_embedding.api import Embedder
from .settings import (
    KnowledgeStorageError,
    KnowledgeStorageSettings,
    ROLE_NAMES,
    Role,
)
from .models import (
    STORAGE_VERSION,
    DIGEST_VERSION,
    IMPORT_CHECKS,
    UNVERIFIED,
    VerificationDigests,
    ImportSummary,
    ImportVerificationSummary,
    ManagementSummary,
    EmbeddingSummary,
    KnowledgeScope,
    ReadLimits,
    ReadView,
    KnowledgeReadError,
    SearchIndexProfile,
    SearchIndexState,
    SearchManagementSummary,
)
from ._read_session import ReadSession, open_read_scope, check_search_session


def _driver():
    """返回：按需驱动模块；缺extra时抛安全配置错误，无默认连接。"""
    try:
        from . import _postgres
        return _postgres
    except ImportError as exc:
        raise KnowledgeStorageError("configuration_error", "知识库可选驱动不可用") from exc


def manage_store(operation: str, settings: KnowledgeStorageSettings,
                 *, run_context: RunContext | None = None) -> ManagementSummary:
    """运行显式选择的知识存储管理操作。

    参数：operation 为 bootstrap/migrate/check，settings 为对应角色配置；run_context 可借用上层运行。
    返回：ManagementSummary；保留 applied/reused/checked 的原结果。
    异常：KnowledgeStorageError 或独立的 RunArtifactError；不自动修库或转换账号。
    副作用：按操作连接管理/读取账号；未传上下文时创建关闭运行，已有上下文仅复用。
    """
    from querypilot.runtime.api import task_run
    owned = run_context is None
    with task_run("knowledge_manage", run_context=run_context, command="knowledge store manage",
                  secrets=_secrets(settings)) as context:
        postgres = _driver()
        if operation == "bootstrap":
            result = postgres.bootstrap(settings)
        elif operation == "migrate":
            result = postgres.migrate(settings)
        elif operation == "check":
            result = postgres.check(settings)
        else:
            raise KnowledgeStorageError("configuration_error", "未知知识管理操作")
        if owned:
            context.complete(result, outcome="reused" if result.outcome == "reused" else "succeeded")
        return result


def prepare_search(settings: KnowledgeStorageSettings,
                   *, run_context: RunContext | None = None) -> SearchManagementSummary:
    """由管理员显式准备BM25设施，保留已有知识和权限；重复调用复用。

    参数：settings为管理配置；run_context可借用上层运行。
    返回：实际准备结果、已核验配置及全索引语料指纹。
    异常：KnowledgeStorageError/RunArtifactError；缺软件或配置漂移不静默修复。
    副作用：缺失时创建固定扩展/索引；不下载插件、不重启PG、不改知识或001。
    """
    return _manage_search("prepare-search", settings, run_context=run_context)


def check_search(settings: KnowledgeStorageSettings,
                 *, run_context: RunContext | None = None) -> SearchManagementSummary:
    """通过reader只读核对BM25设施并记录实际状态，不自动建索引或修复。

    参数：settings为只读配置；run_context可借用上层运行。
    返回：已核验配置及当前全索引语料指纹，供后续评测固定条件。
    异常：KnowledgeStorageError/RunArtifactError；缺设施、版本或索引漂移明确失败。
    副作用：仅数据库只读；拥有运行上下文时保存安全摘要并关闭。
    """
    return _manage_search("check-search", settings, run_context=run_context)


def _manage_search(operation: str, settings: KnowledgeStorageSettings,
                   *, run_context: RunContext | None) -> SearchManagementSummary:
    """输入：固定设施动作与配置；返回：实际摘要；复用统一运行和秘密清理边界。"""
    from querypilot.runtime.api import task_run
    owned = run_context is None
    with task_run("knowledge_manage", run_context=run_context, command="knowledge store " + operation,
                  secrets=_secrets(settings)) as context:
        _driver()
        from . import _search
        result = _search.prepare(settings) if operation == "prepare-search" else _search.check(settings)
        if owned:
            context.complete(result, outcome="reused" if result.outcome == "reused" else "succeeded")
        return result


def embed_corpus(scope: KnowledgeScope, settings: KnowledgeStorageSettings, embedder: Embedder,
                 *, run_context: RunContext | None = None) -> EmbeddingSummary:
    """为一个 Knowledge Scope 的全部 Chunk 生成向量，写进向量表。

    参数：scope 指定 Corpus；settings 需要 reader（读 Chunk）和 importer（写向量）的 DSN；
        embedder 决定 Embedding Profile；run_context 可借用上层运行。
    返回：EmbeddingSummary。这份 Corpus 在该 Profile 下已有向量时为 exists，不调用 embedder；
        否则为 embedded，写入条数等于 Chunk 数。
    异常：KnowledgeReadError（范围不存在或不匹配等）；EmbeddingError；KnowledgeStorageError。
    副作用：Embedding 调用在数据库事务之外；全部向量在一个事务里写入，任何一步失败都不写。
    """
    from querypilot.runtime.api import task_run
    owned = run_context is None
    with task_run("knowledge_embed", run_context=run_context, command="knowledge store embed",
                  secrets=_secrets(settings)) as context:
        _driver()
        from ._embedding import embed_scope
        result = embed_scope(scope, settings, embedder)
        if owned:
            context.complete(result, outcome="reused" if result.outcome == "exists" else "succeeded")
        return result


def _secrets(settings: KnowledgeStorageSettings) -> tuple[str, ...]:
    """输入：存储配置；返回：供清理器匹配的秘密原值；不记录配置、不连接。"""
    return tuple(secret.get_secret_value() for name in ("admin_dsn", "import_dsn", "read_dsn")
                 if (secret := getattr(settings, name)) is not None)


def _load_source(corpus_dir: Path):
    """输入：源路径；返回：本次重读核验对象；来源错误安全转译，不连接PG。"""
    try:
        return load_corpus(corpus_dir)
    except CorpusError as exc:
        raise KnowledgeStorageError("source_invalid", "源语料未通过完整核验") from exc


def _source_hash(corpus_dir: Path) -> str | None:
    """输入：实际源目录；返回：清单原字节指纹或不可得；完整校验在运行内部执行。"""
    try:
        return sha256((Path(corpus_dir) / "manifest.json").read_bytes()).hexdigest()
    except OSError:
        return None


def import_corpus(corpus_dir: Path, settings: KnowledgeStorageSettings,
                    *, run_context: RunContext | None = None) -> ImportSummary:
    """导入实际路径指向的完整知识语料。

    参数：corpus_dir 每次重读，不依赖旧成功摘要；settings 为导入账号配置；run_context 可复用。
    返回：提交已确认的 imported/reused 摘要；源身份不变。
    异常：KnowledgeStorageError，含 source_invalid、target_conflict、commit_outcome_unknown；产物失败为 RunArtifactError。
    副作用：原事务锁和全字段回读保持；不覆盖已存知识，只有运行创建者发布结果并关闭。
    """
    from querypilot.runtime.api import task_run
    owned = run_context is None
    with task_run("knowledge_import", run_context=run_context, command="knowledge store import",
                  secrets=_secrets(settings), config={"input_sha256": _source_hash(corpus_dir)}) as context:
        corpus = _load_source(corpus_dir)
        _driver()
        from ._importer import import_verified_corpus
        result = import_verified_corpus(corpus, settings, run_id=context.run_id, logger=context.logger)
        if owned:
            context.complete(result, outcome=result.outcome)
        return result


def verify_import(corpus_dir: Path, settings: KnowledgeStorageSettings,
                  *, run_context: RunContext | None = None) -> ImportVerificationSummary:
    """用本次核验的源语料只读复验已导入内容。

    参数：corpus_dir 为实际源路径；settings 指定只读账号；run_context 可复用上层运行。
    返回：同一一致事务完整比对后的 verified 摘要。
    异常：KnowledgeStorageError 或 RunArtifactError；来源、登记冲突和服务失败仍可区分。
    副作用：每次重读源与PG，不修复、不重写首次导入时间；只有创建者关闭运行。
    """
    from querypilot.runtime.api import task_run
    owned = run_context is None
    with task_run("knowledge_verify", run_context=run_context, command="knowledge store verify",
                  secrets=_secrets(settings), config={"input_sha256": _source_hash(corpus_dir)}) as context:
        corpus = _load_source(corpus_dir)
        _driver()
        from ._importer import verify_corpus_import
        result = verify_corpus_import(corpus, settings, run_id=context.run_id, logger=context.logger)
        if owned:
            context.complete(result, outcome=result.outcome)
        return result


__all__ = [
    "KnowledgeStorageError",
    "KnowledgeStorageSettings",
    "VerificationDigests",
    "ImportSummary",
    "ImportVerificationSummary",
    "ManagementSummary",
    "EmbeddingSummary",
    "KnowledgeScope",
    "ReadLimits",
    "ReadView",
    "KnowledgeReadError",
    "SearchIndexProfile",
    "SearchIndexState",
    "SearchManagementSummary",
    "ReadSession",
    "open_read_scope",
    "prepare_search",
    "check_search",
    "check_search_session",
    "manage_store",
    "import_corpus",
    "verify_import",
    "embed_corpus",
]
