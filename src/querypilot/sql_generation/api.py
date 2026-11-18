"""SQL 生成的明确公开入口。"""

from typing import Callable
from querypilot.runtime.api import Settings
from ._model import PROMPT_VERSION, GeneratedSql, direct_prompt, generate_sql

# 输入：问题、Schema 文本、evidence、运行配置；输出：候选 SQL 和 token 用量。
SqlGenerator = Callable[[str, str, str, Settings], GeneratedSql]
__all__ = ["PROMPT_VERSION", "direct_prompt", "generate_sql", "GeneratedSql", "SqlGenerator"]
