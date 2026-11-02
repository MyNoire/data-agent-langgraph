"""整份语料原子导入、幂等复用与独立只读复验；不自动迁移或修复。"""

import logging
from time import perf_counter
from uuid import uuid4

import psycopg

from . import _postgres as postgres
from .settings import KnowledgeStorageError, KnowledgeStorageSettings
from .models import ImportSummary, ImportVerificationSummary
from querypilot.knowledge.knowledge_corpus.api import VerifiedCorpus


def progress(event: str, run_id: str, *, status: str = "success", logger=None, **fields) -> None:
    """输入：受控事件/标识/安全字段；输出：None；写进度，禁止传正文或凭据。"""
    (logger or logging.getLogger("querypilot.progress")).info(
        event, extra={"run_id": run_id, "component": "knowledge", "status": status, **fields},
    )


def _summary(corpus: VerifiedCorpus, digests, run_id: str, started: float, outcome: str) -> ImportSummary:
    """输入：核验后数据/操作信息；输出：安全摘要；无 I/O，不更改源身份。"""
    manifest = corpus.manifest
    database = manifest.identity_descriptor.database_scope[0]
    values = {**digests.model_dump(), "corpus_id": manifest.corpus_id, "database_id": database,
              "schema_version": manifest.identity_descriptor.schema_versions[database], "core_digest": manifest.core_digest,
              "counts": manifest.counts, "import_run_id": run_id, "elapsed_ms": (perf_counter() - started) * 1000}
    if outcome == "verified":
        return ImportVerificationSummary(**values)
    return ImportSummary(result_version="knowledge-import-result-v1", outcome=outcome, **values)


def import_verified_corpus(corpus: VerifiedCorpus, settings: KnowledgeStorageSettings,
                           *, run_id: str | None = None, logger=None) -> ImportSummary:
    """原子导入当前调用持有的完整已验证语料。

    参数：corpus 必须由 load_verified_corpus 得到且未修改；settings 为导入配置。
    返回：imported/reused 摘要；只有提交确认成功才返回。
    异常：KnowledgeStorageError；提交断连可为 commit_outcome_unknown，保留 cause。
    副作用：使用写连接/事务锁；只 INSERT 新 corpus，已存在必须全量相同，不覆盖。
    """
    run_id = run_id or str(uuid4())
    started = perf_counter()
    corpus_id = corpus.manifest.corpus_id
    progress("source_verified", run_id, logger=logger)
    with postgres.connection(settings, "import") as conn:
        postgres.check_database(conn)
        commit_pending = False
        try:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                postgres.transaction_limits(conn, settings)
                lock_key = int.from_bytes(bytes.fromhex(corpus_id)[:8], "big", signed=True)
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
                progress("transaction_started", run_id, logger=logger)
                record, rows = postgres.read_corpus(conn, corpus_id)
                if record is not None or any(rows.values()):
                    digests = postgres.verify_rows(corpus, record, rows)
                    outcome = "reused"
                else:
                    postgres.insert_corpus(conn, corpus)
                    progress("objects_written", run_id, logger=logger)
                    digests = postgres.verify_rows(corpus, *postgres.read_corpus(conn, corpus_id))
                    outcome = "imported"
                progress("readback_verified", run_id, logger=logger)
                commit_pending = True
            commit_pending = False
        except psycopg.Error as exc:
            raise postgres.translate_error(exc, commit_pending=commit_pending) from exc
    summary = _summary(corpus, digests, run_id, started, outcome)
    progress("import_finished", run_id, logger=logger, elapsed_ms=summary.elapsed_ms)
    return summary


def verify_corpus_import(corpus: VerifiedCorpus, settings: KnowledgeStorageSettings,
                        *, run_id: str | None = None, logger=None) -> ImportVerificationSummary:
    """独立只读复验。

    参数：corpus 为再次完整加载的源；settings 指定读取账号。
    返回：同一 REPEATABLE READ 视图核验后的 verified 摘要。
    异常：KnowledgeStorageError；缺失/污染/服务失败分别报告。
    副作用：只读 PG，不更新首次时间，不产生模型调用。
    """
    run_id = run_id or str(uuid4())
    started = perf_counter()
    progress("source_verified", run_id, logger=logger)
    with postgres.connection(settings, "read") as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        postgres.transaction_limits(conn, settings)
        postgres.check_database(conn)
        digests = postgres.verify_rows(corpus, *postgres.read_corpus(conn, corpus.manifest.corpus_id))
        progress("readback_verified", run_id, logger=logger)
    summary = _summary(corpus, digests, run_id, started, "verified")
    progress("verification_finished", run_id, logger=logger, elapsed_ms=summary.elapsed_ms)
    return summary
