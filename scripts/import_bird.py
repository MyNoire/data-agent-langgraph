"""登记本地 BIRD SQLite 数据库，并生成固定顺序的小型 smoke 题集。"""
import argparse
import hashlib
import json
from pathlib import Path

from querypilot.config import get_settings
from querypilot.database.registry import DatabaseRegistry


def main() -> None:
    """读取题目 JSON，选取前 N 题并登记所需数据库文件。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/bird"))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--manifest", type=Path,
                        help="Fixed question IDs and source SHA-256 for reproducible evaluation")
    parser.add_argument("--questions-only", action="store_true",
                        help="Prepare the case file before SQLite databases have been downloaded")
    parser.add_argument("--output", type=Path, default=Path("evals/bird_smoke.json"))
    args = parser.parse_args()
    manifest = None
    root = args.root.resolve(strict=True)
    data_dir = get_settings().data_dir.resolve()
    if not root.is_relative_to(data_dir):
        raise SystemExit("BIRD root must be inside configured data directory")
    candidates = list(root.rglob("mini_dev_sqlite.json")) + list(root.rglob("mini_dev_sqlite-00000-of-00001.json"))
    if len(candidates) != 1:
        raise SystemExit("expected exactly one Mini-Dev SQLite question JSON")
    source_bytes = candidates[0].read_bytes()
    cases = json.loads(source_bytes)
    if not isinstance(cases, list):
        raise SystemExit("unexpected BIRD JSON format")
    if args.manifest:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        actual_hash = hashlib.sha256(source_bytes).hexdigest()
        if actual_hash != manifest["source_sha256"].lower():
            raise SystemExit(f"BIRD question JSON checksum mismatch: {actual_hash}")
        by_id = {case["question_id"]: case for case in cases}
        ids = manifest["question_ids"]
        if len(by_id) != len(cases) or len(ids) != len(set(ids)) or any(question_id not in by_id for question_id in ids):
            raise SystemExit("duplicate or missing BIRD question IDs")
        selected = [by_id[question_id] for question_id in ids]
    else:
        # 未提供清单时保留临时选取模式；正式对比应使用固定 manifest。
        selected = sorted(cases, key=lambda case: (str(case.get("db_id")), str(case.get("question_id", case.get("question")))))[:args.limit]
    if not args.questions_only:
        # 先检查全部依赖文件，避免只登记了一部分数据库就因缺文件退出。
        database_paths = {}
        for database_id in sorted({case["db_id"] for case in selected}):
            databases = list(root.rglob(f"{database_id}.sqlite"))
            if len(databases) != 1:
                raise SystemExit(f"expected one SQLite database for {database_id}; found {len(databases)}")
            if manifest and database_id in manifest.get("database_sha256", {}):
                with databases[0].open("rb") as stream:
                    actual_hash = hashlib.file_digest(stream, "sha256").hexdigest()
                if actual_hash != manifest["database_sha256"][database_id].lower():
                    raise SystemExit(f"BIRD database checksum mismatch: {database_id} {actual_hash}")
            database_paths[database_id] = databases[0]
        registry = DatabaseRegistry(data_dir)
        for database_id, path in database_paths.items():
            registry.register(database_id, path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    action = "prepared" if args.questions_only else "imported"
    print(f"{action} {len(selected)} cases from {len({case['db_id'] for case in selected})} databases")


if __name__ == "__main__":
    main()
