"""评测命令只组织一次运行，报告与计分归 evaluation。"""

import json
import sys
from collections import Counter
from hashlib import sha256

from querypilot.evaluation.api import (
    RetrievalCaseError, evaluate_retrieval, export_public, prepare_retrieval_cases, retrieval_run_config,
    run_direct, run_graph,
)
from querypilot.runtime.api import task_run


def run(args, settings) -> int:
    """执行 SQL 评测、排他导出、检索评测或检索题集准备；SQL 评测在实际最外层标明 SQL 类别。

    参数：args 为已解析的 direct/graph/export-public/retrieval/retrieval-cases 参数，settings 为运行配置。
    返回：完整批次（包括合法无预测报告）返回 0；检索题集有题无法解析时返回 1；其他异常由统一 CLI 保持原退出码。
    副作用：direct/graph/export-public/retrieval 创建一份运行记录并输出摘要与安全位置，内部操作只借用上下文；
        retrieval-cases 只写题集和 manifest，不建运行记录（manifest 已记下输入 hash 和 Knowledge Scope）。
    """
    if args.action == "retrieval-cases":
        return _retrieval_cases(args)
    if args.action == "retrieval":
        return _retrieval(args, settings)
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


def _retrieval_cases(args) -> int:
    """生成检索题集，打印各库题数和输出位置；有题无法解析时打印失败题号并返回 1。"""
    try:
        prepared = prepare_retrieval_cases(args.questions, args.frozen, args.corpus_dir, args.output)
    except RetrievalCaseError as exc:
        print(json.dumps({"outcome": "failed", "error_type": "retrieval_case_error",
                          "question_ids": exc.question_ids}), file=sys.stderr)
        return 1
    print(json.dumps({
        "cases": dict(Counter(case.db_id for case in prepared.case_set.cases)),
        "frozen_cases": sum(case.frozen for case in prepared.case_set.cases),
        "output_file": str(prepared.case_set_file),
        "manifest_file": str(prepared.manifest_file),
    }, ensure_ascii=False, indent=2))
    return 0


def _retrieval(args, settings) -> int:
    """在一份运行记录里跑检索评测，打印结果文件位置和汇总；进度事件同时写到 stderr。"""
    with task_run("evaluation", settings, command="retrieval", config=retrieval_run_config(args.cases)) as context:
        context.attach_stream(sys.stderr)
        report = evaluate_retrieval(args.cases, settings, run_context=context)
        context.complete(report)
    print(json.dumps({"result_file": str(context.run_dir / "result.json"),
                      "event_file": str(context.run_dir / "events.jsonl"),
                      "summary": report["summary"]}, ensure_ascii=False, indent=2))
    print(json.dumps({"event": "run_saved", "run_id": context.run_id,
                      "run_dir": str(context.run_dir)}), file=sys.stderr)
    return 0
