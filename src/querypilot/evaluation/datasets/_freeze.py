"""校验 BIRD 参考 SQL 后，冻结与 12 题不重叠的 100 题清单。"""

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from ._errors import DataPreparationError

from querypilot.databases.api import execute_readonly

from ._selection import DIFFICULTIES, QUOTAS


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


def check_gold(case: dict, database_path: Path, *, run_context=None) -> tuple[bool, str]:
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
            max_response_bytes=MAX_RESPONSE_BYTES, run_context=run_context,
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if result.row_count >= MAX_ROWS:
        return False, f"reached row cap ({MAX_ROWS})"
    return True, "ok"


def freeze(cases: list[dict], excluded_ids: set[int], database_paths: dict[str, Path], *, run_context=None) -> tuple[list[dict], list[dict]]:
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
                usable, reason = check_gold(case, database_paths[database_id], run_context=run_context)
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


def freeze_final100(database_root: Path, output: Path, source: Path,
                    smoke_manifest: Path, *, run_context=None) -> dict:
    """核对固定来源/库后按原预检冻结新清单，返回计数；旧目标拒绝覆盖。

    参数：库根、新清单、题目与smoke清单及可选批次上下文。
    副作用：只读SQL预检并写新manifest；不运行模型，不自动选择正式输入。
    """
    if output.exists():
        raise DataPreparationError("output_conflict", "refusing to replace frozen manifest")

    smoke = json.loads(smoke_manifest.read_text(encoding="utf-8"))
    source_bytes = source.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    if source_hash != smoke["source_sha256"]:
        raise DataPreparationError("source_version_mismatch", "source differs from baseline")
    cases = json.loads(source_bytes)
    ids = [case["question_id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise DataPreparationError("question_ids_invalid", "duplicate or missing question IDs")

    root = database_root.resolve(strict=True)
    database_paths = {
        database_id: root / database_id / f"{database_id}.sqlite"
        for database_id in QUOTAS
    }
    database_hashes: dict[str, str] = {}
    for database_id, path in database_paths.items():
        if not path.is_file():
            raise DataPreparationError("database_missing", "required database missing or duplicated")
        actual_hash = sha256_file(path)
        expected_hash = smoke["database_sha256"].get(database_id)
        if expected_hash and actual_hash != expected_hash:
            raise DataPreparationError("source_version_mismatch", "source differs from baseline")
        database_hashes[database_id] = actual_hash

    smoke_ids = set(smoke["question_ids"])
    selected, skipped = freeze(cases, smoke_ids, database_paths, run_context=run_context)
    selected_ids = [case["question_id"] for case in selected]
    if len(selected_ids) != 100 or len(selected_ids) != len(set(selected_ids)) or smoke_ids.intersection(selected_ids):
        raise DataPreparationError("data_input_invalid", "data input does not meet the existing constraints")

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
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"count": len(selected_ids), "database_count": len(database_hashes),
            "difficulty": dict(Counter(case["difficulty"] for case in selected)),
            "skipped_preflight": skipped, "manifest": str(output)}
