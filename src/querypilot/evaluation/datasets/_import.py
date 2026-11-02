"""登记本地 BIRD SQLite 数据库，并生成固定顺序的小型 smoke 题集。"""
import hashlib
import json
from pathlib import Path
from ._errors import DataPreparationError

from querypilot.runtime.api import Settings
from querypilot.databases.api import DatabaseRegistry


def import_bird(root: Path, settings: Settings, *, manifest_path: Path | None = None,
                limit: int = 20, questions_only: bool = False,
                output: Path = Path("evals/bird_smoke.json")) -> dict:
    """核对来源与固定清单，登记全部有效依赖再写题集，返回原计数摘要。

    参数：数据根、运行配置、清单/临时上限、仅题目开关与输出。
    副作用：按显式选项登记SQLite/写题集，不调用模型。
    """
    manifest = None
    root = root.resolve(strict=True)
    data_dir = settings.data_dir.resolve()
    if not root.is_relative_to(data_dir):
        raise DataPreparationError("path_outside_root", "data path outside configured root")
    candidates = list(root.rglob("mini_dev_sqlite.json")) + list(root.rglob("mini_dev_sqlite-00000-of-00001.json"))
    if len(candidates) != 1:
        raise DataPreparationError("data_input_invalid", "data input does not meet the existing constraints")
    source_bytes = candidates[0].read_bytes()
    cases = json.loads(source_bytes)
    if not isinstance(cases, list):
        raise DataPreparationError("data_input_invalid", "data input does not meet the existing constraints")
    if manifest_path:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        actual_hash = hashlib.sha256(source_bytes).hexdigest()
        if actual_hash != manifest["source_sha256"].lower():
            raise DataPreparationError("source_checksum_mismatch", "question JSON checksum mismatch")
        by_id = {case["question_id"]: case for case in cases}
        ids = manifest["question_ids"]
        if len(by_id) != len(cases) or len(ids) != len(set(ids)) or any(question_id not in by_id for question_id in ids):
            raise DataPreparationError("question_ids_invalid", "duplicate or missing question IDs")
        selected = [by_id[question_id] for question_id in ids]
    else:
        # 未提供清单时保留临时选取模式；正式对比应使用固定 manifest。
        selected = sorted(cases, key=lambda case: (str(case.get("db_id")), str(case.get("question_id", case.get("question")))))[:limit]
    if not questions_only:
        # 先检查全部依赖文件，避免只登记了一部分数据库就因缺文件退出。
        database_paths = {}
        for database_id in sorted({case["db_id"] for case in selected}):
            databases = list(root.rglob(f"{database_id}.sqlite"))
            if len(databases) != 1:
                raise DataPreparationError("database_missing", "required database missing or duplicated")
            if manifest and database_id in manifest.get("database_sha256", {}):
                with databases[0].open("rb") as stream:
                    actual_hash = hashlib.file_digest(stream, "sha256").hexdigest()
                if actual_hash != manifest["database_sha256"][database_id].lower():
                    raise DataPreparationError("database_checksum_mismatch", "database checksum mismatch")
            database_paths[database_id] = databases[0]
        registry = DatabaseRegistry(data_dir)
        for database_id, path in database_paths.items():
            registry.register(database_id, path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    action = "prepared" if questions_only else "imported"
    return {"action": action, "count": len(selected), "database_count": len({case["db_id"] for case in selected}), "output": str(output)}
