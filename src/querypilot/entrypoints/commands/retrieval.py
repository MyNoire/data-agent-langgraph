"""R3 已有开发参数适配；完整命令在 T024/T026 接线后提供。"""

from pathlib import Path

from pydantic import ValidationError

from querypilot.entrypoints._arguments import SafeArgumentParser
from querypilot.knowledge.api import KnowledgeScope, parse_json
from querypilot.retrieval.api import KnowledgeRetrievalError, ScopeFile

MAX_SCOPE_FILE_BYTES = 16 * 1024


def build_retrieval_parser() -> SafeArgumentParser:
    """输入：无；返回：单次检索参数解析器；副作用：无，不启动尚未接线的检索。"""
    parser = SafeArgumentParser(
        prog="retrieve_knowledge", description="QueryPilot 限定范围检索（BM25 或向量）",
        error_message="参数语法错误；请使用 --help 查看支持的参数。\n",
        show_usage_on_error=True,
    )
    parser.add_argument("--query", required=True)
    parser.add_argument("--corpus-id")
    parser.add_argument("--database-id")
    parser.add_argument("--schema-version")
    parser.add_argument("--scope-file", type=Path)
    parser.add_argument("--method", choices=("lexical", "vector", "hybrid"), default="lexical")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--knowledge-type", choices=("table", "column", "relationship"), action="append")
    parser.add_argument("--output", type=Path)
    return parser


def resolve_scope(*, corpus_id: str | None = None, database_id: str | None = None,
                  schema_version: str | None = None, scope_file: Path | None = None) -> KnowledgeScope:
    """解析二选一的受控开发范围，数据库登记检查仍由服务执行。

    参数：三个显式范围字段，或一个明确指定的 scope_file；禁止混用、缺项和隐式搜索。
    返回：严格 KnowledgeScope，内容不被裁剪或重新命名。
    异常：KnowledgeRetrievalError(input_error)，原 I/O/JSON cause 仅保留在异常链。
    副作用：文件模式最多读 16 KiB 加一个探测字节；不写文件、不连接数据库。
    """
    provided = (corpus_id is not None, database_id is not None, schema_version is not None)
    if scope_file is not None and any(provided):
        raise KnowledgeRetrievalError("input_error")
    if scope_file is None and not all(provided):
        raise KnowledgeRetrievalError("input_error")
    try:
        if scope_file is not None:
            with scope_file.open("rb") as handle:
                raw = handle.read(MAX_SCOPE_FILE_BYTES + 1)
            if len(raw) > MAX_SCOPE_FILE_BYTES:
                raise KnowledgeRetrievalError("input_error")
            return ScopeFile.model_validate(parse_json(raw)).scope
        return KnowledgeScope(corpus_id=corpus_id, database_id=database_id, schema_version=schema_version)
    except (OSError, ValidationError, ValueError, UnicodeError) as exc:
        if isinstance(exc, KnowledgeRetrievalError):
            raise
        raise KnowledgeRetrievalError("input_error") from exc
