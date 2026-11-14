"""独立任务目录、排他结果发布和运行生命周期；不依赖业务模块。"""

import json
import logging
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Iterator, Mapping
from uuid import uuid4

from ._app_logging import log_application
from ._events import RecordingHandler
from ._privacy import clean_value, safe_config
from .models import OPERATIONS, OPERATION_OUTCOMES, RunArtifactError, RunRecord
from .settings import Settings, get_settings
from .version import source_version


def _now() -> str:
    """返回 UTC ISO 时间，不包含业务输入。"""
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: Any, *, replace_owned: bool = False) -> None:
    """同目录暂存、fsync 后发布；仅本次 run.json 允许原子更新。

    参数：path 是调用方已认领目录中的固定路径；value 必须能编码为 JSON。
    异常：已有结果或不支持 hardlink 的文件系统直接失败，不降级为覆盖。
    """
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if replace_owned:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def publish_json(path: Path, value: Any) -> None:
    """将一份领域 JSON 完整、排他发布到显式目标，不覆盖已有文件。

    参数：path 为调用方指定输出，value 为可序列化的领域数据。
    返回：None；建立父目录、同目录暂存并 fsync/hardlink 发布。
    异常：保留 FileExistsError/OSError/序列化错误，不把领域输出失败改称业务成功。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, value)


def sanitize_private(value: Any, *, secrets=()) -> Any:
    """保留详细领域字段并清除凭据，返回 JSON 兼容数据；不修改输入。

    参数：value 可为字典、领域 dataclass/Pydantic 模型；secrets 为明确的敏感值。
    异常：不支持的对象抛 TypeError。此操作不意味着内容可公开分享。
    """
    return clean_value(value, tuple(secrets))


class RunContext:
    """一次操作的独立运行资源，只有创建者负责结果发布和关闭。"""

    def __init__(self, operation: str, settings: Settings, *, command: str = "",
                 config: Mapping[str, Any] | None = None, private: bool = False,
                 secrets: tuple[str, ...] = ()) -> None:
        """排他建立目录和 started/config；失败时业务尚未开始。

        参数：operation 来自固定枚举；command 是受控命令名；private 指定详细结果位置。
        返回：None。异常：RunArtifactError；配置非法 operation 抛 ValueError。
        """
        if operation not in OPERATIONS:
            raise ValueError("unknown runtime operation")
        if not re.fullmatch(r"[a-zA-Z0-9_. -]{0,100}", command):
            raise ValueError("command must be a fixed command name")
        self.run_id = uuid4().hex
        self.operation = operation
        self.settings = settings
        self.started_at = _now()
        self.run_dir = settings.artifacts_dir / "runs" / operation / self.started_at[:10] / self.run_id
        self.private = private
        api_secret = settings.model_api_key
        self.secrets = tuple(secrets) + ((api_secret,) if api_secret and api_secret != "local" else ())
        self._logging_error: BaseException | None = None
        self._completed = False
        self._closed = False
        self._claimed = False
        self._handlers: list[RecordingHandler] = []
        self._streams: list[Any] = []
        self._middleware: list[Any] = []
        self._terminal_streams: set[int] = set()
        self._audit_logger: logging.Logger | None = None
        self._audit_lock = Lock()
        self._last_failure_event: tuple[str, str, str] | None = None
        self.record: RunRecord = {
            "format_version": "querypilot-run-v1", "run_id": self.run_id, "operation": operation,
            "command": command, "status": "started", "operation_outcome": "not_started",
            "artifact_outcome": "pending", "started_at": self.started_at,
            "finished_at": None, "result_path": None, "error_type": None,
        }
        try:
            self.run_dir.parent.mkdir(parents=True, exist_ok=True)
            self.run_dir.mkdir(exist_ok=False)
            self._claimed = True
            _write_json(self.run_dir / "run.json", self.record)
            _write_json(self.run_dir / "config.json", safe_config(config, settings, source_version(), self.secrets))
            self.logger = self._new_logger("querypilot.progress", self.run_dir / "events.jsonl")
            self.event("run_started", status="started")
            self._check_events()
            log_application("run_started", settings=settings, secrets=self.secrets,
                            run_id=self.run_id, operation=operation, status="started")
        except Exception as exc:
            self._failure("RunArtifactError", outcome="not_started")
            self.close()
            raise self._artifact_error() from exc

    def _artifact_error(self, category: str = "artifact_write_failed") -> RunArtifactError:
        """根据已知操作事实创建安全错误，不包含底层异常文本。"""
        return RunArtifactError(category, run_id=self.run_id,
                                operation_outcome=self.record["operation_outcome"])

    def _note_logging_error(self, error: BaseException) -> None:
        """保留第一个日志失败，完成或退出时作为产物错误报告。"""
        if self._logging_error is None:
            self._logging_error = error

    def _new_logger(self, name: str, path: Path, *, audit: bool = False) -> logging.Logger:
        """新建不传播的 logger 和独占文件；不取共享 logger 注册表。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("x", encoding="utf-8", newline="\n")
        self._streams.append(stream)
        handler = RecordingHandler(stream, self._note_logging_error, audit=audit, secrets=self.secrets)
        logger = logging.Logger(name, level=logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        self._handlers.append(handler)
        return logger

    @property
    def audit_logger(self) -> logging.Logger:
        """首次实际 SQL 调用才创建私有审计端，不为知识任务生成空审计文件。

        多个线程同时第一次调用时只创建一次：审计文件是排他新建的，创建两次第二次会失败。
        """
        with self._audit_lock:
            if self._audit_logger is None:
                if self._closed:
                    raise RuntimeError("run context is closed")
                self._audit_logger = self._new_logger("querypilot.sql.audit", self.run_dir / "private" / "sql-audit.jsonl", audit=True)
            return self._audit_logger

    def event(self, event: str, **fields: Any) -> None:
        """写固定安全事件；忽略非白名单正文，写失败在完成时报告。"""
        if self._closed:
            raise RuntimeError("run context is closed")
        self.logger.info(event, extra={"run_id": self.run_id, "operation": self.operation, **fields})

    def attach_stream(self, stream) -> None:
        """为当前任务添加安全进度终端，不接管或关闭调用方的流。

        参数：stream 为 CLI 指定的 stderr 等可写文字流；同一流只添加一次。
        返回：None；写失败由当前任务产物机制处理，不修改共享 logger。
        """
        if self._closed or self._completed:
            raise RuntimeError("cannot attach stream after run completion")
        if id(stream) in self._terminal_streams:
            return
        handler = RecordingHandler(stream, self._note_logging_error, secrets=self.secrets)
        self.logger.addHandler(handler)
        self._handlers.append(handler)
        self._terminal_streams.add(id(stream))

    def graph_middleware(self, *, row_count=None):
        """创建并托管图回调，使用当前事件端；不创建子运行目录。"""
        from ._graph_callbacks import GraphLogMiddleware
        middleware = GraphLogMiddleware(self.logger, row_count=row_count)
        self._middleware.append(middleware)
        return middleware

    def _check_events(self) -> None:
        """刷新自有事件/审计端；若记录失败则抛安全产物错误。"""
        for handler in self._handlers:
            try:
                handler.flush()
            except Exception as exc:
                self._note_logging_error(exc)
        if self._logging_error is not None:
            raise self._artifact_error() from self._logging_error

    def complete(self, result: Any, *, outcome: str = "succeeded") -> None:
        """排他发布唯一领域结果并写终态；保留实际成功、复用或导入事实。

        参数：result 为领域 JSON 兼容结果；outcome 为固定操作结果。
        异常：RunArtifactError；失败不删除数据库内容或覆盖已有结果。
        """
        if self._closed or self._completed:
            raise RuntimeError("run already closed or completed")
        if outcome not in OPERATION_OUTCOMES or outcome in {"not_started", "failed", "unknown"}:
            raise ValueError("complete requires a known successful operation outcome")
        self.record["operation_outcome"] = outcome
        try:
            for middleware in self._middleware:
                middleware.close()
            self._check_events()
            result_path = "private/result.json" if self.private else "result.json"
            (self.run_dir / result_path).parent.mkdir(exist_ok=True)
            _write_json(self.run_dir / result_path, clean_value(result, self.secrets))
            self.record.update(status="completed", artifact_outcome="written", result_path=result_path,
                               finished_at=_now(), error_type=None)
            self.event("run_finished", status="completed", operation_outcome=outcome,
                       artifact_outcome="written")
            self._check_events()
            _write_json(self.run_dir / "run.json", self.record, replace_owned=True)
            self._completed = True
        except Exception as exc:
            self._failure("RunArtifactError")
            if isinstance(exc, RunArtifactError):
                raise
            raise self._artifact_error() from exc

    def _failure(self, error_type: str, *, outcome: str | None = None,
                 artifact_failed: bool = True) -> None:
        """尽力写安全失败记录；次要错误不覆盖外层业务异常。"""
        if outcome is not None:
            self.record["operation_outcome"] = outcome
        self.record.update(status="failed", finished_at=_now(), error_type=error_type,
                           artifact_outcome="failed" if artifact_failed else "not_written")
        signature = (error_type, self.record["operation_outcome"], self.record["artifact_outcome"])
        if hasattr(self, "logger") and not self._closed and signature != self._last_failure_event:
            try:
                self.event("run_failed", status="failed", error_type=error_type,
                           operation_outcome=self.record["operation_outcome"],
                           artifact_outcome=self.record["artifact_outcome"])
                self._last_failure_event = signature
            except Exception:
                # 失败事件同样可能写不出；主异常的类别和提交事实仍保持。
                pass
            if self._logging_error is not None:
                self.record["artifact_outcome"] = "failed"
        try:
            if self._claimed and (self.run_dir / "run.json").is_file():
                _write_json(self.run_dir / "run.json", self.record, replace_owned=True)
        except Exception:
            pass

    def close(self) -> None:
        """幂等关闭自有资源；记录关闭错误，交由 task_run 决定错误优先级。"""
        if self._closed:
            return
        for middleware in self._middleware:
            try:
                middleware.close()
            except Exception as exc:
                self._note_logging_error(exc)
        for handler in self._handlers:
            try:
                handler.flush()
                handler.close()
            except Exception as exc:
                self._note_logging_error(exc)
        for stream in self._streams:
            try:
                stream.close()
            except Exception as exc:
                self._note_logging_error(exc)
        self._closed = True


@contextmanager
def task_run(operation: str, settings: Settings | None = None, *, run_context: RunContext | None = None,
             command: str = "", config: Mapping[str, Any] | None = None, private: bool = False,
             secrets=()) -> Iterator[RunContext]:
    """拥有独立任务或复用外层上下文；原业务异常始终优先。

    参数：已有 run_context 时仅借用；其余参数用于新任务。
    返回：RunContext；新任务由调用方 complete，借用者不得关闭或发布顶级结果。
    异常：业务异常原样传播；无业务异常时产物失败为 RunArtifactError。
    """
    if run_context is not None:
        if run_context._closed:
            raise RuntimeError("cannot reuse closed run context")
        yield run_context
        return
    context = RunContext(operation, settings or get_settings(), command=command, config=config,
                         private=private, secrets=tuple(secrets))
    try:
        yield context
        if not context._completed:
            context._failure("incomplete_run", outcome="unknown")
            raise context._artifact_error("incomplete_run")
    except BaseException as exc:
        if isinstance(exc, RunArtifactError):
            context._failure("RunArtifactError")
        else:
            outcome = getattr(exc, "operation_outcome", None) or getattr(exc, "database_outcome", None)
            category = getattr(exc, "error_type", None) or getattr(exc, "category", None)
            if category == "commit_outcome_unknown":
                outcome = "unknown"
            if outcome not in OPERATION_OUTCOMES:
                outcome = "failed"
            error_type = category if isinstance(category, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,95}", category) else type(exc).__name__
            context._failure(error_type, outcome=outcome, artifact_failed=context._logging_error is not None)
        raise
    finally:
        import sys
        primary = sys.exception()
        context.close()
        if primary is None and context._logging_error is not None:
            context._failure("RunArtifactError")
            raise context._artifact_error() from context._logging_error


def read_run_result(path: Path) -> Any:
    """读取显式完整运行目录的唯一结果；拒绝中断、损坏和越界指针。

    参数：path 为调用者明确提供的运行目录；不选择最新、不修复旧目录。
    返回：原领域 JSON 数据；不可作为完整结果时抛 RunArtifactError。
    """
    path = Path(path).resolve()
    try:
        record_path = path / "run.json"
        if not record_path.resolve().is_relative_to(path):
            raise ValueError("record path escapes run directory")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("format_version") != "querypilot-run-v1":
            raise ValueError("invalid run record")
        if (record.get("operation") not in OPERATIONS
                or not isinstance(record.get("run_id"), str)
                or re.fullmatch(r"[a-f0-9]{32}", record["run_id"]) is None):
            raise ValueError("invalid run identity")
        if record.get("status") != "completed" or record.get("artifact_outcome") != "written":
            raise RunArtifactError("incomplete_run", run_id=record.get("run_id"), operation_outcome="unknown")
        if (record.get("operation_outcome") not in OPERATION_OUTCOMES - {"not_started", "failed", "unknown"}
                or not isinstance(record.get("finished_at"), str)):
            raise ValueError("invalid completed run")
        relative = record.get("result_path")
        if relative not in {"result.json", "private/result.json"}:
            raise ValueError("invalid result pointer")
        result_path = path / relative
        if not result_path.resolve().is_relative_to(path):
            raise ValueError("result path escapes run directory")
        return json.loads(result_path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except RunArtifactError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise RunArtifactError("invalid_run", operation_outcome="unknown") from exc


def _reject_constant(value: str) -> None:
    """拒绝 NaN/Infinity 等非标准 JSON 常量，防止接受损坏结果。"""
    raise ValueError("non-finite JSON value")
