"""校验用户明确选定的 BIRD 压缩包，再安全解压到本地数据目录。"""
import hashlib
import shutil
import urllib.request
import zipfile
from pathlib import Path
from ._errors import DataPreparationError


def extract_checked(archive: Path, destination: Path) -> None:
    """校验 ZIP 条目路径并解压到目标目录。

    参数:
        archive: 待解压的 ZIP 文件路径。
        destination: 解压目标目录路径。
    返回:
        None；把安全的归档条目写到目标目录。
    异常:
        SystemExit: ZIP 条目解析后越过目标目录。
        zipfile.BadZipFile: 输入不是有效的 ZIP 文件。
    """
    with zipfile.ZipFile(archive) as source:
        for entry in source.infolist():
            resolved = (destination / entry.filename).resolve()
            if not resolved.is_relative_to(destination):
                raise DataPreparationError("archive_unsafe", "unsafe archive path")
        source.extractall(destination)


def fetch_bird(*, url: str | None = None, archive_source: Path | None = None,
               expected_sha256: str, destination: Path) -> dict:
    """获取用户选定的归档并核对哈希、拒绝越界解压，返回目标和摘要。

    参数：URL或本地完整包二选一、预期哈希、目标；错误保留原校验/文件类别。
    副作用：显式下载或复制并解压，不在导入阶段执行。
    """
    if bool(url) == (archive_source is not None):
        raise DataPreparationError("data_input_invalid", "URL与本地包必须二选一")
    dest = destination.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / "mini_dev.zip"
    if url:
        with urllib.request.urlopen(url, timeout=60) as source, archive.open("wb") as target:
            shutil.copyfileobj(source, target)
    else:
        original = archive_source.resolve(strict=True)
        if original != archive:
            shutil.copyfile(original, archive)
    with archive.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    # 校验失败时不解压，避免把来源不明的数据混入评测目录。
    if actual.lower() != expected_sha256.lower():
        archive.unlink()
        raise DataPreparationError("archive_checksum_mismatch", "archive checksum mismatch")
    extract_checked(archive, dest)
    # 部分 BIRD 包把 SQLite 文件另放在 dev_databases.zip 中。
    for nested in dest.rglob("dev_databases.zip"):
        extract_checked(nested, nested.parent)
    (dest / "DOWNLOAD.sha256").write_text(f"{actual}  {archive.name}\n", encoding="utf-8")
    return {"destination": str(dest), "sha256": actual}
