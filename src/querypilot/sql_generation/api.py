"""SQL 生成的明确公开入口。"""

from typing import Callable
from querypilot.runtime.api import Settings
from ._model import PROMPT_VERSION, direct_prompt, generate_sql

SqlGenerator = Callable[[str, str, str, Settings], str]
__all__ = ["PROMPT_VERSION", "direct_prompt", "generate_sql", "SqlGenerator"]
