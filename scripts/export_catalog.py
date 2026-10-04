"""导出已登记数据库的只读结构目录。"""

import argparse
import json
from pathlib import Path

from querypilot.catalog.sqlite import extract_catalog
from querypilot.config import get_settings
from querypilot.database.registry import DatabaseRegistry

def main() -> None:
    """根据命令行参数导出目录，并打印计数摘要。

    参数:
        无函数参数；命令行要求 --database-id 和 --output。
    返回:
        None；结构快照写入文件，摘要写入标准输出。
    异常:
        RegistryError: 数据库未登记或路径超出配置目录。
        FileExistsError: 输出文件已存在，拒绝覆盖。
        CatalogError、sqlite3.DatabaseError: 结构提取失败。
        OSError: 输出路径不可写。
    副作用:
        创建输出父目录和一个新的 JSON 文件；不调用模型或修改原数据库。
    """
    parser = argparse.ArgumentParser(description="导出SQLite结构目录")
    parser.add_argument("--database-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    settings = get_settings()
    registry = DatabaseRegistry(settings.data_dir)
    database_path = registry.get(args.database_id)
    snapshot = extract_catalog(args.database_id, database_path)
    payload = json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)

    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload + "\n")

    summary = {
        "database_id": snapshot.database_id,
        "tables": len(snapshot.tables),
        "columns": sum(len(table.columns) for table in snapshot.tables),
        "foreign_keys": sum(len(table.foreign_keys) for table in snapshot.tables),
        "output_file": str(output_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()