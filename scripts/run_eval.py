"""命令行入口：运行 direct-prompt 模型评测或 fixture 管线自检。"""

import argparse
import json
from pathlib import Path

from querypilot.config import get_settings
from querypilot.evals.baseline import run_eval


def main() -> None:
    """解析评测选项并调用 direct-prompt smoke 评测。

    参数:
        无函数参数；从 --cases、--provider、--evidence-mode、--output 读取输入。
    返回:
        None；写入逐题 JSON 文件，并将汇总打印到标准输出。
    异常:
        题集无效、数据库缺失或输出写入失败时向上抛出对应异常。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=Path("evals/bird_smoke.json"))
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
