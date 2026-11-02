"""验证下载脚本拒绝 ZIP 路径穿越。"""

import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path


def test_archive_path_traversal_rejected(tmp_path):
    """检查下载脚本拒绝 ZIP 路径穿越条目。

    参数:
        tmp_path: pytest 提供的隔离临时目录。
    返回:
        None；通过断言报告测试结果。
    """
    archive = tmp_path / "bad.zip"
    destination = tmp_path / "out"
    destination.mkdir()
    with zipfile.ZipFile(archive, "w") as source:
        source.writestr("../escape.txt", "bad")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "querypilot", "--artifacts-dir", str(tmp_path / "artifacts"), "data", "bird", "fetch", "--archive", str(archive),
         "--sha256", digest, "--dest", str(destination)],
        cwd=project, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "archive_unsafe" in result.stderr
    assert not (tmp_path / "escape.txt").exists()
