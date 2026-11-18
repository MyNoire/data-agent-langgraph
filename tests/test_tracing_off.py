"""查询图默认不向 LangSmith 上传：即使环境变量打开了追踪，编译图之后追踪也是关的。

LangGraph 每次运行都会读 LANGCHAIN_TRACING_V2 等环境变量，打开时把每个节点的输入、状态和输出上传到
LangSmith，其中有问题原文、Schema、Hit 正文、SQL 和结果行。项目约定这些不进日志，更不能发到外部服务。
"""

from concurrent.futures import ThreadPoolExecutor

import langsmith
from langsmith.utils import get_env_var, tracing_is_enabled

from querypilot.querying.api import build_query_graph

from test_graph_context_source import _shop


def test_building_a_query_graph_turns_langsmith_tracing_off_even_when_the_environment_turns_it_on(tmp_path,
                                                                                                    monkeypatch):
    """输入：环境变量 LANGCHAIN_TRACING_V2=true（和用户机器上一样），然后编译一张查询图。

    输出：编译前追踪是开的（确认环境变量确实生效）；编译后主线程和工作线程里追踪都是关的——评测在
    工作线程里跑图。
    """
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    get_env_var.cache_clear()            # langsmith 缓存了环境变量的读取结果
    langsmith.configure(enabled=None)    # 回到"按环境变量决定"
    try:
        assert tracing_is_enabled() is True

        build_query_graph(_shop(tmp_path))

        assert tracing_is_enabled() is False
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(tracing_is_enabled).result() is False
    finally:
        langsmith.configure(enabled=False)
        get_env_var.cache_clear()
