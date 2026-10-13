"""只读独立核验 R1.4 快照，不要求原机器的源目录。"""

import argparse
import json
from pathlib import Path
import sys

from querypilot.knowledge.corpus import CorpusError, verify_knowledge_corpus


def main() -> int:
    """运行核验 CLI。

    参数: 无函数参数；读取必填 --corpus-dir。
    返回: 0 核验通过，1 核验/输出失败；argparse 参数错误退出 2。
    副作用: 仅文件读取及终端 JSON 输出，不修复或改写快照。
    """
    parser = argparse.ArgumentParser(description="独立核验正式离线知识快照")
    parser.add_argument("--corpus-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify_knowledge_corpus(args.corpus_dir)
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=True))
        sys.stdout.flush()
        return 0
    except (CorpusError, OSError, UnicodeError) as exc:
        error = exc if isinstance(exc, CorpusError) else CorpusError("artifact_integrity", "无法输出核验结果")
        try:
            print(json.dumps(error.as_dict(), ensure_ascii=True), file=sys.stderr)
            sys.stderr.flush()
        except OSError:
            return 1
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
