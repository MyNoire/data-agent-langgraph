"""命令行入口：逐题调用单图并保存独立的模型评测结果与进度日志。"""

import argparse
import json
import logging
from pathlib import Path
from uuid import uuid4

from querypilot.config import get_settings
from querypilot.evals.graph import run_graph_eval
from querypilot.evals.naming import result_path
from querypilot.logging import progress_logging


def main() -> None:
    """解析题集路径、运行单图评测，并输出汇总和事件文件路径。

    参数:
        无函数参数；从 ``--cases`` 和可选的 ``--output`` 读取配置。
    返回:
        None；逐题详情写入原始结果 JSON，进度写标准错误和 JSONL，
        汇总写标准输出。
    异常:
        题集、输出路径或模型/数据库配置出现未处理错误时原样抛出。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=Path("evals/bird_smoke.json"))
    parser.add_argument("--output", type=Path, help="原始结果路径；已存在时拒绝覆盖")
    args = parser.parse_args()
    settings = get_settings()
    output = args.output or result_path(args.cases, "graph", settings.model_name, "none")
    if output.exists():
        raise FileExistsError(f"evaluation output already exists: {output}")
    events = output.with_suffix(".events.jsonl")
    run_id = uuid4().hex
    with progress_logging(events):
        try:
            result = run_graph_eval(args.cases, settings, output=output, run_id=run_id)
        except Exception as exc:
            logging.getLogger("querypilot.progress").warning("eval_aborted", extra={
                "run_id": run_id, "component": "evaluation", "provider": "graph",
                "status": "error", "error_type": type(exc).__name__,
            })
            raise
    print(json.dumps({
        "output_file": str(output), "event_file": str(events),
        **{key: value for key, value in result.items() if key != "records"},
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
