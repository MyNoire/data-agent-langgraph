"""命令行入口：运行 direct-prompt 模型评测或 fixture 管线自检。"""

import argparse
import json
from pathlib import Path

from querypilot.config import get_settings
from querypilot.evals.baseline import run_eval


def main() -> None:
    """解析评测参数、写入逐题 JSON，并在终端显示汇总。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=Path("evals/demo_smoke.json"))
    parser.add_argument("--provider", choices=["model", "fixture"], default="model")
    parser.add_argument("--evidence-mode", choices=["none", "oracle"], default="none",
                        help="none: question + schema only; oracle: include annotated case evidence")
    parser.add_argument("--output", type=Path, default=Path("results/latest.json"))
    args = parser.parse_args()
    result = run_eval(args.cases, get_settings(), provider=args.provider,
                      evidence_mode=args.evidence_mode, output=args.output)
    # 终端只显示摘要；逐题 SQL、错误与耗时留在输出文件中。
    print(json.dumps({key: value for key, value in result.items() if key != "records"}, indent=2))


if __name__ == "__main__":
    main()
