"""用户端启动：读一次 Active Corpus，得到运行时 context，并关闭追踪。"""

from pathlib import Path

import langsmith
from pydantic import BaseModel, ConfigDict, TypeAdapter

from querypilot.retrieval.api import KnowledgeRetrievalError, KnowledgeScope

from .models import QueryContext

ACTIVE_CORPUS_PATH = Path("config/active_corpus.json")
"""知识管理员维护的 Active Corpus 配置；相对当前工作目录，与 data_dir 的默认值一致。"""


class _ActiveCorpus(BaseModel):
    """配置里一个库的取值；库名是它的键，所以不在条目里重复。"""

    model_config = ConfigDict(extra="forbid")

    corpus_id: str
    schema_version: str


def start_serving(path: Path = ACTIVE_CORPUS_PATH) -> QueryContext:
    """用户端启动时调用一次：读入各库的 Active Corpus。

    参数:
        path: Active Corpus 配置文件，内容是 database_id → corpus_id + schema_version。
    返回:
        运行时 context：各库的 Knowledge Scope。之后文件再变，也不影响这次启动的结果。
    异常:
        KnowledgeRetrievalError: 文件缺失、不可读或不符合格式时为 configuration_error；消息不含文件内容。
    副作用:
        关闭本进程的 LangSmith 追踪；只读配置文件一次；不连接知识库、不调用外部服务。
    """
    # tool 可以不经过查询图使用，而关闭追踪不能指望编译查询图时顺带发生：否则查询文本和知识正文会被上传。
    langsmith.configure(enabled=False)
    try:
        entries = TypeAdapter(dict[str, _ActiveCorpus]).validate_json(path.read_bytes())
        scopes = {database_id: KnowledgeScope(database_id=database_id, **entry.model_dump())
                  for database_id, entry in entries.items()}
    except (OSError, ValueError) as exc:  # pydantic 的 ValidationError 是 ValueError 的子类
        raise KnowledgeRetrievalError("configuration_error", "Active Corpus 配置缺失、不可读或不符合格式") from exc
    return QueryContext(knowledge_scopes=scopes)
