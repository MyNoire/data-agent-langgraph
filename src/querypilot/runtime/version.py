"""定位源码版本，不依赖某个模块在目录中的固定深度。"""

import hashlib
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any


def source_version(start: Path | None = None) -> dict[str, Any]:
    """返回 Git 版本、脏状态和源码指纹；安装包没有仓库时明确标记不可得。

    参数：start 为可选起始文件或目录；默认从本模块向上定位 .git。
    返回：安全版本字典，不读取 .env 或原始数据；不修改工作树。
    """
    result: dict[str, Any] = {
        "version_source": "unavailable", "git_revision": None,
        "worktree_dirty": None, "source_fingerprint": None, "package_version": None,
    }
    try:
        result["package_version"] = version("querypilot")
    except PackageNotFoundError:
        pass
    location = (start or Path(__file__)).resolve()
    location = location if location.is_dir() else location.parent
    root = next((candidate for candidate in (location, *location.parents)
                 if (candidate / ".git").exists()), None)
    if root is None:
        return result
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                  capture_output=True, text=True, check=True, timeout=5)
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=normal"],
                               cwd=root, capture_output=True, text=True, check=True, timeout=5)
        digest = hashlib.sha256()
        for path in sorted((root / "src" / "querypilot").rglob("*.py")):
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        result.update(version_source="git", git_revision=revision.stdout.strip(),
                      worktree_dirty=bool(dirty.stdout), source_fingerprint=digest.hexdigest())
    except (OSError, subprocess.SubprocessError):
        # 不把无法定位版本误报为干净工作树。
        pass
    return result
