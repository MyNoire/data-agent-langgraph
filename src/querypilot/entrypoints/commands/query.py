"""自然语言命令只适配参数；图机制由 querying 拥有。"""

import json
import sys

from querypilot.querying.api import ask
from querypilot.runtime.api import task_run


def run(args, settings) -> int:
    """执行已解析 ask；返回0，业务/产物错误交统一入口映射。"""
    with task_run("query", settings, command="ask", private=True,
                  config={"graph_version": "single-v1", "prompt_version": "direct-v2"}) as context:
        context.attach_stream(sys.stderr)
        result = ask(args.database_id, args.question, settings, run_context=context)
        context.complete(result)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    print(json.dumps({"event": "run_saved", "run_id": context.run_id,
                      "run_dir": str(context.run_dir)}), file=sys.stderr)
    return 0
