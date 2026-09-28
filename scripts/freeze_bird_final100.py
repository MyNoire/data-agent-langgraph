"""校验 BIRD 参考 SQL 后，冻结与 12 题不重叠的 100 题清单。"""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from querypilot.database.executor import execute_readonly

from select_bird_final100 import DIFFICULTIES, QUOTAS, SMOKE_MANIFEST, SOURCE


MAX_ROWS = 10_000
TIMEOUT_MS = 20_000
MAX_RESPONSE_BYTES = 10_485_760
SALT = "querypilot-bird-final100-v1"


def sha256_file(path: Path) -> str:
    """计算文件的 SHA-256，避免一次将大型 SQLite 文件读入内存。

    参数:
        path: 要校验的现有文件。
    返回:
        小写十六进制 SHA-256 字符串。
    """
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ranked_groups(cases: list[dict], excluded_ids: set[int]) -> dict[tuple[str, str], list[dict]]:
    """按数据库与难度分组，并为每组生成固定的候选顺序。

    参数:
        cases: 已校验来源版本的全部 BIRD 题目。
        excluded_ids: 已用于 12 题快速评测的题号。
    返回:
        (数据库 ID, 难度) 到候选题列表的映射；列表按稳定哈希排序。
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for case in cases:
        if case["question_id"] not in excluded_ids:
            groups[(case["db_id"], case["difficulty"])].append(case)
    for group in groups.values():
        group.sort(key=lambda case: hashlib.sha256(
            f"{SALT}:{case['question_id']}".encode("utf-8")
        ).hexdigest())
    return groups


def check_gold(case: dict, database_path: Path) -> tuple[bool, str]:
    """用当前只读执行器确认参考 SQL 能在评测限制内完整返回。

    参数:
        case: 包含 db_id、question_id 和参考 SQL 的 BIRD 题目。
        database_path: 该题对应的本地 SQLite 文件。
    返回:
        (可用, 原因)；达到行数上限的题目保守地视为可能截断。
    """
    try:
        result = execute_readonly(
            database_path, case["SQL"], database_id=case["db_id"],
            max_rows=MAX_ROWS, timeout_ms=TIMEOUT_MS,
            max_response_bytes=MAX_RESPONSE_BYTES,
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if result.row_count >= MAX_ROWS:
        return False, f"reached row cap ({MAX_ROWS})"
    return True, "ok"


def freeze(cases: list[dict], excluded_ids: set[int], database_paths: dict[str, Path]) -> tuple[list[dict], list[dict]]:
    """按固定配额选出参考 SQL 可完整执行的题目。

    参数:
        cases: 固定来源中的全部题目。
        excluded_ids: 12 题快速评测使用的题号。
        database_paths: 数据库 ID 到已验证 SQLite 路径的映射。
    返回:
        (选中题目, 跳过记录)；跳过记录包含题号和客观的执行失败原因。
    异常:
        ValueError: 某个数据库与难度分组无法满足配额。
    """
    groups = ranked_groups(cases, excluded_ids)
    selected: list[dict] = []
    skipped: list[dict] = []
    for database_id, quotas in QUOTAS.items():
        for difficulty, count in zip(DIFFICULTIES, quotas):
            accepted = 0
            for case in groups[(database_id, difficulty)]:
                usable, reason = check_gold(case, database_paths[database_id])
                if usable:
                    selected.append(case)
                    accepted += 1
                    if accepted == count:
                        break
                else:
                    skipped.append({"question_id": case["question_id"], "reason": reason})
            if accepted != count:
                raise ValueError(f"{database_id}/{difficulty}: only {accepted}/{count} usable cases")
    return selected, skipped


def main() -> None:
    """冻结 manifest，并打印抽样与参考 SQL 自检摘要。

    参数:
        无函数参数；从命令行读取 --database-root 和 --output。
    返回:
        None；仅在全部检查成功后写入正式 manifest。
    异常:
        SystemExit: 来源版本、数据库文件、旧库哈希或输出路径有问题。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-root", type=Path, required=True,
                        help="完整 Mini-Dev 的 dev_databases 目录")
    parser.add_argument("--output", type=Path,
                        default=Path("evals/bird_final100_manifest.json"))
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to replace frozen manifest: {args.output}")

    smoke = json.loads(SMOKE_MANIFEST.read_text(encoding="utf-8"))
    source_bytes = SOURCE.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    if source_hash != smoke["source_sha256"]:
        raise SystemExit("BIRD question source differs from the 12-case baseline")
    cases = json.loads(source_bytes)
    ids = [case["question_id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate BIRD question_id")

    root = args.database_root.resolve(strict=True)
    database_paths = {
        database_id: root / database_id / f"{database_id}.sqlite"
        for database_id in QUOTAS
    }
    database_hashes: dict[str, str] = {}
    for database_id, path in database_paths.items():
        if not path.is_file():
            raise SystemExit(f"missing database: {path}")
        actual_hash = sha256_file(path)
        expected_hash = smoke["database_sha256"].get(database_id)
        if expected_hash and actual_hash != expected_hash:
            raise SystemExit(f"database differs from 12-case baseline: {database_id}")
        database_hashes[database_id] = actual_hash

    smoke_ids = set(smoke["question_ids"])
    selected, skipped = freeze(cases, smoke_ids, database_paths)
    selected_ids = [case["question_id"] for case in selected]
    if len(selected_ids) != 100 or len(selected_ids) != len(set(selected_ids)) or smoke_ids.intersection(selected_ids):
        raise SystemExit("the final set must contain 100 unique questions disjoint from smoke")

    manifest = {
        "dataset": smoke["dataset"], "revision": smoke["revision"],
        "source_url": smoke["source_url"], "source_sha256": source_hash,
        "database_sha256": database_hashes,
        "selection": "Per-database/difficulty quotas from scripts/select_bird_final100.py; "
                     "sort by SHA-256 of querypilot-bird-final100-v1:<question_id>; "
                     "skip only reference SQL that fails the recorded read-only preflight; "
                     "exclude the 12 smoke question IDs. Selected before the 100-case model run.",
        "preflight": {"max_rows": MAX_ROWS, "timeout_ms": TIMEOUT_MS,
                      "max_response_bytes": MAX_RESPONSE_BYTES},
        "skipped_preflight": skipped,
        "question_ids": selected_ids,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"frozen {len(selected_ids)} cases across {len(database_hashes)} databases")
    print(f"difficulty: {dict(Counter(case['difficulty'] for case in selected))}")
    print(f"skipped by reference-SQL preflight: {skipped}")
    print(f"manifest: {args.output}")


if __name__ == "__main__":
    main()
