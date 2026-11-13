"""Embedding 的公开入口：Embedder 接口、错误类型、硅基流动客户端和查询的任务指令。"""

from ._openai import QUERY_TASK, EmbeddingSettings, OpenAIEmbedder
from .models import Embedder, EmbeddingError

__all__ = ["Embedder", "EmbeddingError", "EmbeddingSettings", "OpenAIEmbedder", "QUERY_TASK"]
