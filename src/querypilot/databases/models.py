"""数据库结果与错误契约，供本能力内部和公开入口使用。"""

from ._executor import QueryLimitError, QueryResult
from ._registry import RegistryError
from ._guard import SqlRejected

__all__ = ["QueryResult", "QueryLimitError", "RegistryError", "SqlRejected"]
