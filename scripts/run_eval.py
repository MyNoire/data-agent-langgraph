"""命令行入口：运行 direct-prompt 模型评测或 fixture 管线自检。"""

import argparse
import json
import logging
from pathlib import Path
from uuid import uuid4

from querypilot.config import get_settings
from querypilot.evals.baseline import run_eval
from querypilot.evals.naming import result_path
from querypilot.logging import progress_logging


def main() -> None:
    """解析评测选项并调用 direct-prompt 评测。

    参数:
        无函数参数；从 --cases、--provider、--evidence-mode、--output 读取输入。
    返回:
        None；实时写入进度 JSONL，再将结果文件路径与汇总打印到标准输出。
    异常:
        题集无效、数据库缺失、输出文件已存在或写入失败时抛出对应异常。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=Path("evals/bird_smoke.json"))
    parser.add_argument("--provider", choices=["model", "fixture"], default="model")
    parser.add_argument("--evidence-mode", choices=["none", "oracle"], default="none",
                        help="none: question + schema only; oracle: include annotated case evidence")
    parser.add_argument("--output", type=Path,
                        help="原始结果路径；省略时按题集/模型/证据模式/UTC 时间自动命名")
    args = parser.parse_args()
    settings = get_settings()
    output = args.output or result_path(args.cases, args.provider, settings.model_name,
                                        args.evidence_mode)
    if output.exists():
        raise FileExistsError(f"evaluation output already exists: {output}")
    events = output.with_suffix(".events.jsonl")
    run_id = uuid4().hex
    with progress_logging(events):
        try:
            result = run_eval(args.cases, settings, provider=args.provider,
                              evidence_mode=args.evidence_mode, output=output,
                              run_id=run_id)
        except Exception as exc:
            logging.getLogger("querypilot.progress").warning("eval_aborted", extra={
                "run_id": run_id, "component": "evaluation", "status": "error",
                "error_type": type(exc).__name__,
            })
            raise
    # 终端只显示摘要；逐题 SQL、错误与耗时留在输出文件中。
    print(json.dumps({"output_file": str(output), "event_file": str(events),
                      **{key: value for key, value in result.items() if key != "records"}},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
