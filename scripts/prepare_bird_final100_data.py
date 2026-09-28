"""按冻结清单校验并复制 BIRD 数据库，不改动原始 Mini-Dev 文件。"""

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

from querypilot.config import get_settings


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


def main() -> None:
    """把 100 题需要的数据库准备到 QueryPilot 数据目录。

    参数:
        无函数参数；从 --database-source 和 --manifest 读取原始路径及清单。
    返回:
        None；逐库打印复制或复用状态，不生成题目文件或运行模型。
    异常:
        SystemExit: 路径越界、磁盘空间不足、文件缺失或哈希不符。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-source", type=Path, required=True,
                        help="完整 Mini-Dev 的 dev_databases 目录")
    parser.add_argument("--manifest", type=Path,
                        default=Path("evals/bird_final100_manifest.json"))
    args = parser.parse_args()

    source_root = args.database_source.resolve(strict=True)
    data_dir = get_settings().data_dir.resolve()
    target_root = data_dir / "bird" / "dev_databases"
    if source_root == target_root or not source_root.is_dir():
        raise SystemExit("source must be a separate dev_databases directory")
    target_root.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    expected = manifest["database_sha256"]
    needed_bytes = 0
    paths = []
    for database_id, expected_hash in expected.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", database_id):
            raise SystemExit(f"invalid database ID: {database_id}")
        source = (source_root / database_id / f"{database_id}.sqlite").resolve(strict=True)
        target = target_root / database_id / f"{database_id}.sqlite"
        if not source.is_relative_to(source_root):
            raise SystemExit(f"source path escaped dev_databases: {source}")
        if not target.exists():
            needed_bytes += source.stat().st_size
        paths.append((database_id, source, target, expected_hash))
    if shutil.disk_usage(target_root).free < needed_bytes:
        raise SystemExit(f"insufficient free space: need {needed_bytes} bytes")

    for database_id, source, target, expected_hash in paths:
        status = copy_verified(source, target, expected_hash)
        print(f"{status}: {database_id}", flush=True)
    print(f"verified {len(paths)} databases in {target_root}")


if __name__ == "__main__":
    main()
