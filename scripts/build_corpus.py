"""R1.4 固定输入离线构建；成功/失败摘要不包含知识正文。"""

import argparse
import json
from pathlib import Path
import sys

from querypilot.knowledge.corpus import CorpusError, build_knowledge_corpus


def main() -> int:
    """运行构建 CLI。

    参数: 无函数参数；读取 --database-id/--bundle/--semantics/--output-dir
        及可选 --profile-version。
    返回: 0 成功，1 服务或输出失败；参数错误由 argparse 退出 2。
    副作用: 服务只创建新目标或只读复用；stdout/stderr 输出脱敏 JSON。
    """
    parser = argparse.ArgumentParser(description="构建并验证正式离线知识快照")
    parser.add_argument("--database-id", required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--semantics", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--profile-version", default="r1-4-v1")
    args = parser.parse_args()
    try:
        result = build_knowledge_corpus(args.bundle, args.semantics, args.output_dir,
                                        args.database_id, args.profile_version)
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=True))
        sys.stdout.flush()
        return 0
    except (CorpusError, OSError, UnicodeError) as exc:
        error = exc if isinstance(exc, CorpusError) else CorpusError("write_failed", "无法输出本次调用结果")
        try:
            print(json.dumps(error.as_dict(), ensure_ascii=True), file=sys.stderr)
            sys.stderr.flush()
        except OSError:
            # 错误通道也不可用时仍退出 1，不撤销已写文件或虚报成功。
            return 1
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
