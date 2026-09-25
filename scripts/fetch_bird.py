"""校验用户明确选定的 BIRD 压缩包，再安全解压到本地数据目录。"""
import argparse
import hashlib
import shutil
import urllib.request
import zipfile
from pathlib import Path


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
                raise SystemExit(f"unsafe archive path: {entry.filename}")
        source.extractall(destination)


def main() -> None:
    """获取并校验 BIRD 归档，再解压外层和嵌套数据库包。

    参数:
        无函数参数；从 --url/--archive、--sha256 和 --dest 读取输入。
    返回:
        None；写入下载文件、解压数据和 DOWNLOAD.sha256，并打印目标目录。
    异常:
        SystemExit: 下载校验和不一致或归档路径不安全。
        OSError: 下载、读取或写入文件失败。
    """
    parser = argparse.ArgumentParser()
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--url", help="Canonical archive URL selected from BIRD Mini-Dev")
    source_group.add_argument("--archive", type=Path, help="Previously downloaded complete SQLite ZIP")
    parser.add_argument("--sha256", required=True, help="Expected SHA-256 from your download manifest")
    parser.add_argument("--dest", type=Path, default=Path("data/bird"))
    args = parser.parse_args()
    dest = args.dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / "mini_dev.zip"
    if args.url:
        with urllib.request.urlopen(args.url, timeout=60) as source, archive.open("wb") as target:
            shutil.copyfileobj(source, target)
    else:
        original = args.archive.resolve(strict=True)
        if original != archive:
            shutil.copyfile(original, archive)
    with archive.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    # 校验失败时不解压，避免把来源不明的数据混入评测目录。
    if actual.lower() != args.sha256.lower():
        archive.unlink()
        raise SystemExit(f"checksum mismatch: {actual}")
    extract_checked(archive, dest)
    # 部分 BIRD 包把 SQLite 文件另放在 dev_databases.zip 中。
    for nested in dest.rglob("dev_databases.zip"):
        extract_checked(nested, nested.parent)
    (dest / "DOWNLOAD.sha256").write_text(f"{actual}  {archive.name}\n", encoding="utf-8")
    print(f"verified and extracted: {dest}")


if __name__ == "__main__":
    main()
