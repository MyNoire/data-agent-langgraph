"""验证下载脚本拒绝 ZIP 路径穿越。"""

import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path


def test_archive_path_traversal_rejected(tmp_path):
    archive = tmp_path / "bad.zip"
    destination = tmp_path / "out"
    destination.mkdir()
    with zipfile.ZipFile(archive, "w") as source:
        source.writestr("../escape.txt", "bad")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(project / "scripts" / "fetch_bird.py"), "--archive", str(archive),
         "--sha256", digest, "--dest", str(destination)],
        cwd=project, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "unsafe archive path" in result.stderr
    assert not (tmp_path / "escape.txt").exists()
