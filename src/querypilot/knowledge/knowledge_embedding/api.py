"""Embedding 的公开入口：Embedder 接口、错误类型和硅基流动客户端。"""

from ._openai import EmbeddingSettings, OpenAIEmbedder
from .models import Embedder, EmbeddingError

__all__ = ["Embedder", "EmbeddingError", "EmbeddingSettings", "OpenAIEmbedder"]
