"""使用已批准的规则构建带修正记录的正式增强目录。"""

import argparse
import json
from pathlib import Path

from querypilot.catalog.corrections import build_curated_catalog


def main() -> None:
    """解析正式构建参数并打印摘要。

    参数:
        无函数参数；CLI 必须提供 --base、--source-dir、--plan、--output-dir。
    返回:
        None；向新目录写正式修正产物，摘要包含状态、规则数及警告数。
    异常:
        ValidationError、CorrectionError: 计划未批准、批准范围不符或输入变化。
        FileExistsError、OSError: 输出已存在或文件不可读写。
    副作用:
        仅创建派生来源与修正结果，不调用模型或改动原始资料。
    """
    parser = argparse.ArgumentParser(description="应用已批准的字段说明修正规则")
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = build_curated_catalog(args.base, args.source_dir, args.plan, args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
