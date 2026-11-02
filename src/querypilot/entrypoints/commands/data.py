"""BIRD命令仅解析到evaluation公开数据操作，不自动下载或重新冻结。"""

import json
import sys

from querypilot.evaluation.api import (
    fetch_bird, import_bird, select_final100, freeze_final100, prepare_final100,
)
from querypilot.runtime.api import task_run


def run(args, settings) -> int:
    """执行用户明确选定的数据准备命令，返回0；失败不打印题目或原始异常。"""
    with task_run("data_prepare", settings, command=args.action, private=True) as context:
        context.attach_stream(sys.stderr)
        if args.action == "fetch":
            result = fetch_bird(url=args.url, archive_source=args.archive,
                                expected_sha256=args.sha256, destination=args.dest)
            text = f"verified and extracted: {result['destination']}"
        elif args.action == "import":
            result = import_bird(args.root, settings, manifest_path=args.manifest, limit=args.limit,
                                 questions_only=args.questions_only, output=args.output)
            text = f"{result['action']} {result['count']} cases from {result['database_count']} databases"
        elif args.action == "select-final100":
            result = select_final100(args.source, args.smoke_manifest)
            text = f"题数: {result['count']}\n难度: {result['difficulty']}\n数据库数: {result['database_count']}\n与 12 题重叠: {result['overlap']}\n前 10 个题号: {result['first_ids']}"
        elif args.action == "freeze-final100":
            result = freeze_final100(args.database_root, args.output, args.source, args.smoke_manifest,
                                     run_context=context)
            text = f"frozen {result['count']} cases across {result['database_count']} databases\ndifficulty: {result['difficulty']}\nskipped by reference-SQL preflight: {result['skipped_preflight']}\nmanifest: {result['manifest']}"
        else:
            result = prepare_final100(args.database_source, args.manifest, settings)
            lines = [f"{status}: {database_id}" for database_id, status in result["databases"].items()]
            text = "\n".join([*lines, f"verified {result['count']} databases in {result['target']}"])
        context.complete(result)
    print(text)
    print(json.dumps({"event": "run_saved", "run_id": context.run_id,
                      "run_dir": str(context.run_dir)}), file=sys.stderr)
    return 0
