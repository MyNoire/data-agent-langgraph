"""通过框架回调记录图运行元数据，并为每次日志会话提供独立输出端。"""

import asyncio
import logging
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Any, Callable, Iterator, TextIO
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.callbacks import GraphCallbackHandler, GraphInterruptEvent
from langgraph.errors import GraphInterrupt

from querypilot.logging import JsonFormatter

RowCountReader = Callable[[Any], int | None]

@dataclass
class SpanContext:
    """一次框架调用的计时上下文；只保存安全元数据，不保存输入输出。"""
    started: float
    fields: dict[str, Any]
    interrupt: bool = False

class GraphLogMiddleware(GraphCallbackHandler):
    """把图、节点与嵌套 Runnable 的生命周期转换为结构化进度事件。"""

    # 观察器不主动吞掉业务异常；这里控制的是回调自身出错时的行为。
    raise_error = False
    run_inline = True

    def __init__(self, logger: logging.Logger, *, row_count: RowCountReader | None = None) -> None:
        """初始化中间件。

        参数：logger 为调用者管理的输出端；row_count 为可选行数提取函数。
        返回：None；创建按框架 UUID 隔离的活动调用表和互斥锁。
        """
        self.logger = logger
        self.row_count = row_count
        self._spans: dict[UUID, SpanContext] = {}
        self._lock = RLock()

    def config(self, *, run_id: str, database_id: str, parent_run_id: str | None = None,
               question_id: int | str | None = None, case_index: int | None = None) -> RunnableConfig:
        """构造一次业务请求的回调配置。

        参数：run_id 为业务请求 ID；database_id 为数据库 ID；parent_run_id
            为可选评测批次 ID；question_id、case_index 为可选题号及顺序。
        返回：可传给 invoke/stream 的配置；不把这些关联字段写入图状态。
        """
        metadata = {
            "querypilot_run_id": run_id, "querypilot_parent_run_id": parent_run_id,
            "database_id": database_id, "question_id": question_id, "case_index": case_index,
        }
        return {"callbacks": [self], "metadata":{
            key: value for key, value in metadata.items() if value is not None
        }}

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None,
                       metadata=None, **kwargs) -> None:
        """记录一次框架调用开始。

        参数：serialized 可为 None；inputs 为业务输入，均不记录。run_id 和
            parent_run_id 是框架 UUID；metadata 含安全关联字段；kwargs 含 name。
        返回：None；保存计时上下文，发出 graph_span_started。
        """
        metadata = metadata or {}
        fields = {
            "event_version": 2, "component": "graph",
            "run_id": metadata.get("querypilot_run_id", str(run_id)),
            "span_id": str(run_id),
            "parent_span_id": str(parent_run_id) if parent_run_id else None,
            "span_name": kwargs.get("name") or "unnamed",
        }
        for source, target in (
            ("querypilot_parent_run_id", "parent_run_id"),
            ("database_id", "database_id"), ("question_id", "question_id"),
            ("case_index", "case_index"), ("langgraph_node", "node"),
        ):
            value = metadata.get(source)
            if value is not None and isinstance(value, (str, int)):
                fields[target] = value
        with self._lock:
            self._spans[run_id] = SpanContext(perf_counter(), fields)
            self.logger.info("graph_span_started", extra=fields)

    def _finish(self, run_id: UUID, status: str, error_type: str | None = None,
                row_count: int | None = None) -> None:
        """完成配对、计算耗时并释放上下文。

        参数：run_id 为框架 UUID；status 为结束状态；error_type 仅含类型名；
            row_count 为可选的非负行数。
        返回：None；发出结束事件。未观察到开始时不伪造耗时。
        """
        with self._lock:
            span = self._spans.pop(run_id, None)
            if span is None:
                return
            fields = {**span.fields, "status": "interrupted" if span.interrupt else status,
                      "elapsed_ms": round((perf_counter() - span.started) * 1000, 3)}
            if error_type is not None:
                fields["error_type"] = error_type
            if type(row_count) is int and row_count >= 0:
                fields["row_count"] = row_count
            level = logging.WARNING if fields["status"] in {"error", "incomplete"} else logging.INFO
            self.logger.log(level, "graph_span_finished", extra=fields)

    def on_chain_end(self, outputs: dict[str, Any], *, run_id: UUID, **kwargs) -> None:
        """处理正常结束，仅允许提取可选的行数。

        参数：outputs 为任意业务输出；run_id 为框架 UUID；其余参数由框架提供。
        返回：None；记录结束和耗时，不修改输出。提取失败不会改变业务结果。
        """
        count = None
        if self.row_count is not None:
            try:
                count = self.row_count(outputs)
            except Exception:
                # 提取器是附加观察功能，其异常文本不能进入进度日志。
                pass
        self._finish(run_id, "success", row_count=count)

    def on_chain_error(self, error: BaseException, *, run_id, **kwargs) -> None:
        """处理业务异常、暂停信号或取消。

        参数：error 为框架捕获的异常；run_id 为框架 UUID。
        返回：None；只记录异常类型；传播与重试由框架负责。
        """
        if isinstance(error, GraphInterrupt):
            status = "interrupted"
        elif isinstance(error, (asyncio.CancelledError, GeneratorExit)):
            status = "cancelled"
        else:
            status = "error"
        self._finish(run_id, status, type(error).__name__)

    def on_interrupt(self, event: GraphInterruptEvent) -> None:
        """标记图暂停，防止随后的结束回调把暂停写成成功。

        参数：event 为图暂停事件；不读取其中的业务暂停内容。
        返回：None；更新活动图标记，发出 graph_interrupted。
        """
        with self._lock:
            span = self._spans.get(event.run_id)
            if span is not None:
                span.interrupt = True
                self.logger.info("graph_interrupted", extra=span.fields)

    def on_resume(self, event) -> None:
        """记录图从 checkpoint 恢复。

        参数：event 为恢复事件，只使用运行 ID 查找安全上下文。
        返回：None；存在活动上下文时发出 graph_resumed。
        """
        with self._lock:
            span = self._spans.get(event.run_id)
            if span is not None:
                self.logger.info("graph_resumed", extra=span.fields)

    def close(self) -> None:
        """结束观察会话，标记尚未收到结束回调的调用。

        参数：无；调用前应等待本会话的图任务结束。
        返回：None；残留调用记为 incomplete，不代表能够恢复该运行。
        """
        with self._lock:
            for run_id in list(self._spans):
                self._finish(run_id, "incomplete")

@contextmanager
def graph_logging(path: Path, *, stream: TextIO | None = None,
                  row_count: RowCountReader | None = None) -> Iterator[GraphLogMiddleware]:
    """为一条查询或一批评测创建独立日志输出端。

    参数：path 为新 JSONL 文件；stream 为可选终端流，默认 stderr；
        row_count 为可选行数提取器。
    返回：上下文管理器；进入后得到可传给 graph 的中间件。
    异常：文件已存在或无法创建时直接抛出；退出时关闭自己创建的 handler。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as event_file:
        # 直接构造独立对象，不从全局 logger 注册表取共享实例。
        logger = logging.Logger("querypilot.progress", level=logging.INFO)
        logger.propagate = False
        handlers = [logging.StreamHandler(stream if stream is not None else sys.stderr),
                    logging.StreamHandler(event_file)]
        for handler in handlers:
            handler.setFormatter(JsonFormatter())
            logger.addHandler(handler)
        middleware = GraphLogMiddleware(logger, row_count=row_count)
        try:
            yield middleware
        finally:
            middleware.close()
            for handler in handlers:
                handler.flush()
                logger.removeHandler(handler)
                handler.close()
