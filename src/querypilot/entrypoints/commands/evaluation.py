"""评测命令只组织一次运行，报告与计分归 evaluation。"""

import json
import sys
from hashlib import sha256

from querypilot.evaluation.api import export_public, run_direct, run_graph
from querypilot.runtime.api import task_run


def run(args, settings) -> int:
    """执行 SQL 评测或排他导出，并在实际最外层标明 SQL 类别。

    参数：args 为已解析的 direct/graph/export-public 参数，settings 为运行配置。
    返回：完整批次（包括合法无预测报告）返回 0；异常由统一 CLI 保持原退出码。
    副作用：创建一份运行记录、输出摘要与安全位置，内部操作只借用上下文。
    """
    config = {"mode": "sql"} if args.action == "export-public" else {
        "mode": "sql",
        "provider": getattr(args, "provider", "graph"),
        "evidence_mode": getattr(args, "evidence_mode", "none"),
        "dataset_sha256": sha256(args.cases.read_bytes()).hexdigest(),
        "prompt_version": "direct-v2" if getattr(args, "provider", "model") != "fixture" else None,
        "graph_version": "single-v1" if args.action == "graph" else None,
    }
    with task_run("evaluation", settings, command=args.action, private=True, config=config) as context:
        context.attach_stream(sys.stderr)
        if args.action == "export-public":
            result = export_public(args.input, args.output)
            context.complete({"output_file": str(args.output), "records": len(result["records"])})
            summary = str(args.output)
        else:
            if args.action == "direct":
                result = run_direct(args.cases, settings, provider=args.provider,
                                    evidence_mode=args.evidence_mode, run_context=context)
            else:
                result = run_graph(args.cases, settings, run_context=context)
            context.complete(result)
            summary = json.dumps({"output_file": str(context.run_dir / "private/result.json"),
                                  "event_file": str(context.run_dir / "events.jsonl"),
                                  **{key: value for key, value in result.items() if key != "records"}},
                                 ensure_ascii=False, indent=2)
    print(summary)
    print(json.dumps({"event": "run_saved", "run_id": context.run_id,
                      "run_dir": str(context.run_dir)}), file=sys.stderr)
    return 0
