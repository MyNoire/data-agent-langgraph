"""R5 八次 SQL 评测运行按严格执行匹配和内容匹配离线重算（一次性脚本，不调用模型）。

重放每个运行保存的预测 SQL 和题集里的金标准 SQL，用与原运行相同的执行上限、经同一个只读执行器重新执行，
再用评测模块的 content_match 比较。严格口径重新算一遍，与原记录不一致时报出来。结果写进一次运行记录
（artifacts 下的 evaluation 运行，私有），并在标准输出打印汇总表。

用法：
    uv run python scripts/rescore_r5_content_match.py [--cases evals/bird_toxicology_student_club.json]
"""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from querypilot.databases.api import QueryLimitError, SqlRejected, execute_sql
from querypilot.evaluation.api import content_match, strict_execution_match
from querypilot.runtime.api import Settings, task_run

RUN_ROOT = Path("artifacts/runs/evaluation/2026-11-17")
R5_RUNS = {
    "① schema": "fbeff481", "② ddl": "aceea710",
    "③ knowledge/columns": "692dce99", "④ knowledge/ddl": "9670f23f",
    "⑤ retrieval/columns hybrid+rerank": "46a797e8", "⑥ retrieval/ddl hybrid+rerank": "6a38d4c3",
    "⑤a retrieval/columns lexical": "d64bbf8c", "⑥a retrieval/ddl lexical": "68dc9080",
}
DATABASES = ("toxicology", "student_club")


def _run_dir(prefix: str) -> Path:
    """运行目录名以 prefix 开头的那一个；不是恰好一个时报错。"""
    matches = [path for path in RUN_ROOT.iterdir() if path.name.startswith(prefix)]
    if len(matches) != 1:
        raise SystemExit(f"run {prefix}: expected one directory, found {len(matches)}")
    return matches[0]


def _rows(database_id: str, sql: str, settings: Settings, context) -> list[list[object]] | str:
    """执行一条 SQL，返回结果行；执行器报的错（被拒、超时或超限、SQLite 执行失败）返回错误类别名。

    这些题按不匹配计，题号和错误类别写进结果；其他异常照常抛出，中止重算。
    """
    try:
        return execute_sql(database_id, sql, settings, run_context=context).rows
    except (SqlRejected, QueryLimitError, sqlite3.DatabaseError) as exc:
        return type(exc).__name__


def _count(records: list[dict], key: str, *, database: str | None = None, frozen: bool | None = None) -> int:
    return sum(record[key] for record in records
               if (database is None or record["database_id"] == database)
               and (frozen is None or record["frozen"] is frozen))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=Path("evals/bird_toxicology_student_club.json"))
    args = parser.parse_args()
    case_bytes = args.cases.read_bytes()
    case_sha256 = hashlib.sha256(case_bytes).hexdigest()
    cases = {case["question_id"]: case for case in json.loads(case_bytes)}

    reports = {}
    for label, prefix in R5_RUNS.items():
        report = json.loads((_run_dir(prefix) / "private" / "result.json").read_text(encoding="utf-8"))
        if report["dataset_sha256"] != case_sha256:
            raise SystemExit(f"run {prefix} used another case set")
        reports[label] = report
    limits = {(r["max_rows"], r["query_timeout_ms"], r["max_response_bytes"]) for r in reports.values()}
    if len(limits) != 1:
        raise SystemExit(f"runs used different execution limits: {limits}")
    max_rows, timeout_ms, max_bytes = limits.pop()
    settings = Settings(max_rows=max_rows, query_timeout_ms=timeout_ms, max_response_bytes=max_bytes)

    config = {"cases_sha256": case_sha256, "runs": R5_RUNS, "max_rows": max_rows,
              "query_timeout_ms": timeout_ms, "max_response_bytes": max_bytes}
    with task_run("evaluation", settings, command="rescore-content-match", config=config, private=True) as context:
        gold_rows = {}
        result = {}
        for label, report in reports.items():
            records, strict_changed, rerun_failed = [], [], []
            for record in report["records"]:
                qid = record["question_id"]
                strict = content = False
                if record["outcome"] != "error":
                    if qid not in gold_rows:
                        gold_rows[qid] = _rows(record["database_id"], cases[qid]["SQL"], settings, context)
                    predicted = _rows(record["database_id"], record["predicted_sql"], settings, context)
                    failed = [rows for rows in (predicted, gold_rows[qid]) if isinstance(rows, str)]
                    if failed:
                        rerun_failed.append({"question_id": qid, "error_type": failed[0]})
                    else:
                        strict = strict_execution_match(predicted, gold_rows[qid])
                        content = content_match(predicted, gold_rows[qid])
                        if content is False and strict:
                            raise SystemExit(f"{label} {qid}: strict match without content match")
                if strict != record["execution_match"]:
                    strict_changed.append(qid)
                records.append({"question_id": qid, "database_id": record["database_id"],
                                "frozen": record["frozen"], "execution_match": strict, "content_match": content})
            result[label] = {
                "run": report["run_id"],
                "strict": _count(records, "execution_match"), "content": _count(records, "content_match"),
                "by_database": {db: {"strict": _count(records, "execution_match", database=db),
                                     "content": _count(records, "content_match", database=db)} for db in DATABASES},
                "non_frozen": {"strict": _count(records, "execution_match", frozen=False),
                               "content": _count(records, "content_match", frozen=False)},
                "content_only_non_frozen": sorted(r["question_id"] for r in records
                                                  if r["content_match"] and not r["execution_match"] and not r["frozen"]),
                "strict_changed": strict_changed, "rerun_failed": rerun_failed,
                "records": records,
            }
        context.complete(result)

    print(f"run {context.run_id}; cases {case_sha256[:8]}; limits {max_rows} rows / {timeout_ms} ms")
    print("| 组 | 严格 | 内容 | toxicology 严格/内容 | student_club 严格/内容 | 非冻结 72 严格/内容 |")
    print("|---|---|---|---|---|---|")
    for label, row in result.items():
        db = row["by_database"]
        print(f"| {label} | {row['strict']}/88 | {row['content']}/88 | "
              f"{db['toxicology']['strict']}/{db['toxicology']['content']} | "
              f"{db['student_club']['strict']}/{db['student_club']['content']} | "
              f"{row['non_frozen']['strict']}/{row['non_frozen']['content']} |")
    for label, row in result.items():
        print(label, "content-only non-frozen:", row["content_only_non_frozen"],
              "strict changed:", row["strict_changed"], "rerun failed:", row["rerun_failed"])


if __name__ == "__main__":
    main()
