"""按冻结清单校验并复制 BIRD 数据库，不改动原始 Mini-Dev 文件。"""

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from ._errors import DataPreparationError

from querypilot.runtime.api import Settings


def sha256_file(path: Path) -> str:
    """流式计算文件哈希。

    参数:
        path: 待校验的 SQLite 文件路径。
    返回:
        小写十六进制 SHA-256 字符串。
    """
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_verified(source: Path, target: Path, expected_hash: str) -> str:
    """校验来源，再原子复制缺失的数据库文件。

    参数:
        source: Mini-Dev 原始 SQLite 文件，仅读取。
        target: 项目数据目录中的目标 SQLite 文件。
        expected_hash: 冻结清单中记录的 SHA-256。
    返回:
        ``existing`` 表示原有目标已通过校验；``copied`` 表示本次复制。
    异常:
        ValueError: 来源、现有目标或复制结果的哈希不符。
    """
    if not source.is_file() or sha256_file(source) != expected_hash:
        raise ValueError(f"source database missing or checksum mismatch: {source}")
    if target.exists():
        if not target.is_file() or sha256_file(target) != expected_hash:
            raise ValueError(f"existing database checksum mismatch: {target}")
        return "existing"

    target.parent.mkdir(parents=True, exist_ok=True)
    # 临时文件始终位于同一目录，校验通过后才能原子替换成正式文件名。
    with tempfile.NamedTemporaryFile(prefix=target.name + ".", suffix=".partial",
                                     dir=target.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        shutil.copyfile(source, temporary_path)
        if sha256_file(temporary_path) != expected_hash:
            raise ValueError(f"copied database checksum mismatch: {target}")
        if target.exists():
            raise ValueError(f"target appeared during copy: {target}")
        temporary_path.replace(target)
    finally:
        temporary_path.unlink(missing_ok=True)
    return "copied"


def prepare_final100(database_source: Path, manifest_path: Path, settings: Settings) -> dict:
    """按冻结清单校验/复制数据库，返回逐库状态，不改原来源。

    参数：独立来源根、清单及数据目录配置；保留路径/空间/哈希保护。
    """

    source_root = database_source.resolve(strict=True)
    data_dir = settings.data_dir.resolve()
    target_root = data_dir / "bird" / "dev_databases"
    if source_root == target_root or not source_root.is_dir():
        raise DataPreparationError("data_input_invalid", "data input does not meet the existing constraints")
    target_root.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = manifest["database_sha256"]
    needed_bytes = 0
    paths = []
    for database_id, expected_hash in expected.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", database_id):
            raise DataPreparationError("data_input_invalid", "data input does not meet the existing constraints")
        source = (source_root / database_id / f"{database_id}.sqlite").resolve(strict=True)
        target = target_root / database_id / f"{database_id}.sqlite"
        if not source.is_relative_to(source_root):
            raise DataPreparationError("path_outside_root", "data path outside configured root")
        if not target.exists():
            needed_bytes += source.stat().st_size
        paths.append((database_id, source, target, expected_hash))
    if shutil.disk_usage(target_root).free < needed_bytes:
        raise DataPreparationError("disk_space_insufficient", "insufficient free space")

    statuses = {}
    for database_id, source, target, expected_hash in paths:
        status = copy_verified(source, target, expected_hash)
        statuses[database_id] = status
    return {"databases": statuses, "count": len(paths), "target": str(target_root)}
