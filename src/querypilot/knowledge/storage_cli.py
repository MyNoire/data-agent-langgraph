"""知识存储 CLI 共用适配层；JSON stdout 与安全进度 stderr 分开。"""

import argparse
from contextlib import nullcontext
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

from pydantic import ValidationError

from querypilot.knowledge.corpus import CorpusError, load_verified_corpus
from querypilot.knowledge import postgres
from querypilot.knowledge.importer import import_verified_corpus, verify_corpus_import, progress
from querypilot.knowledge.storage_config import KnowledgeStorageError, KnowledgeStorageSettings
from querypilot.logging import JsonFormatter, progress_logging


def write_receipt(output: Path, payload: dict) -> None:
    """原子、排他写回执。

    参数：output 为新目标，payload 是安全摘要。
    返回：None。
    异常：OSError；已提交数据不会因回执失败被删除。
    副作用：同目录临时文件 fsync 后硬链接发布，禁止覆盖竞态；仅清理自己的临时文件。
    """
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".receipt-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write((json.dumps(payload, ensure_ascii=True) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _perform(command: str, args, run_id: str):
    """输入：命令/解析参数/标识；输出：模型摘要；安全转译异常，不打印原始内容。"""
    try:
        settings = KnowledgeStorageSettings()
        if command == "manage":
            return getattr(postgres, args.operation)(settings)
        snapshot = load_verified_corpus(args.corpus_dir)
        action = import_verified_corpus if command == "import" else verify_corpus_import
        return action(snapshot, settings, run_id=run_id)
    except KnowledgeStorageError:
        raise
    except CorpusError as exc:
        raise KnowledgeStorageError("source_invalid", "源快照未通过完整核验") from exc
    except ValidationError as exc:
        raise KnowledgeStorageError("configuration_error", "知识库配置不合法") from exc
    except Exception as exc:
        raise KnowledgeStorageError("database_error", "知识库调用失败，需检查配置与服务") from exc


def run_cli(command: str, argv: list[str] | None = None) -> int:
    """执行薄 CLI。

    参数：command 为 manage/import/verify，argv 可供测试注入参数。
    返回：0 成功、1 协议/输入、3 服务失败；argparse 参数错误退出 2。
    副作用：按命令读源/访问 PG、排他写回执和 events；不打印秘密或 traceback。
    """
    parser = argparse.ArgumentParser(description="QueryPilot 知识数据库管理/导入/复验")
    if command == "manage":
        parser.add_argument("operation", choices=("bootstrap", "migrate", "check"))
    else:
        parser.add_argument("--corpus-dir", type=Path, required=True)
        parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    run_id = str(uuid4())
    output = getattr(args, "output", None)
    database_outcome = "not_started"
    failure_logged = False
    logger = logging.getLogger("querypilot.progress")
    old = (logger.handlers[:], logger.level, logger.propagate)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger.handlers, logger.level, logger.propagate = [handler], logging.INFO, False
    try:
        if output:
            if output.resolve().is_relative_to(args.corpus_dir.resolve()):
                raise KnowledgeStorageError("configuration_error", "回执不能写入被核验的源快照目录")
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists() or output.with_suffix(".events.jsonl").exists():
                raise KnowledgeStorageError("receipt_write_failed", "回执或进度文件已存在，拒绝覆盖")
            with tempfile.TemporaryFile(dir=output.parent):
                pass
        context = progress_logging(output.with_suffix(".events.jsonl")) if output else nullcontext()
        with context:
            try:
                # 开始调用之后，未返回成功前不猜测数据库结果；COMMIT 断连尤需复验。
                database_outcome = None
                result = _perform(command, args, run_id)
                database_outcome = result.outcome
                payload = result.model_dump(mode="json")
                if output:
                    try:
                        write_receipt(output, payload)
                    except OSError as exc:
                        raise KnowledgeStorageError("receipt_write_failed", "数据库调用已完成但回执写出失败", database_outcome=database_outcome) from exc
                print(json.dumps(payload, ensure_ascii=True))
                sys.stdout.flush()
            except (KnowledgeStorageError, OSError) as exc:
                # 在文件 handler 退出前记录失败，保证 stderr 与 sidecar 都能定位终态。
                category = exc.error_type if isinstance(exc, KnowledgeStorageError) else "receipt_write_failed"
                progress("knowledge_operation_failed", run_id, status="error", error_type=category)
                failure_logged = True
                raise
        return 0
    except (KnowledgeStorageError, OSError) as exc:
        error = exc if isinstance(exc, KnowledgeStorageError) else KnowledgeStorageError("receipt_write_failed", "当前无法保存进度或输出回执", database_outcome=database_outcome)
        if not failure_logged:
            progress("knowledge_operation_failed", run_id, status="error", error_type=error.error_type)
        payload = {"result_version": "knowledge-operation-error-v1", "outcome": "failed", "import_run_id": run_id,
                   "error_type": error.error_type, "message": str(error), "database_outcome": error.database_outcome or database_outcome}
        if command == "manage": payload["command"] = args.operation
        try:
            print(json.dumps(payload, ensure_ascii=True))
            sys.stdout.flush()
        except OSError:
            pass
        return error.exit_code
    finally:
        logger.handlers, logger.level, logger.propagate = old
        handler.close()
