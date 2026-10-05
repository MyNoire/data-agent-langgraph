"""把明确选定的 CSV 字段说明合并到结构目录草稿。"""

import argparse
from collections import Counter
import json
from pathlib import Path

from querypilot.catalog.descriptions import enrich_catalog
from querypilot.catalog.models import CatalogSnapshot


def main() -> None:
    """解析参数，保存增强目录和导入警告。

    参数:
        无函数参数；命令行要求 --catalog、--description-dir、--output-dir。
    返回:
        None；两个 JSON 写入新目录，计数摘要写到标准输出。
    异常:
        FileExistsError: 输出目录已存在，拒绝覆盖。
        DescriptionError、ValidationError、csv.Error: 输入契约或解析失败。
        OSError: 输入不可读或结果不可写。
    副作用:
        创建一个草稿输出目录；不调用模型、不修改来源和原结构文件。
    """
    parser = argparse.ArgumentParser(description="合并字段说明并保留来源")
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--description-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    catalog = CatalogSnapshot.model_validate_json(args.catalog.read_text(encoding="utf-8"))
    result = enrich_catalog(catalog, args.description_dir)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    payloads = {
        "enriched_catalog.json": result.enriched_catalog.model_dump(mode="json"),
        "import_warnings.json": {
            "format_version": "description-warnings-v1",
            "database_id": catalog.database_id,
            "warnings": [warning.model_dump(mode="json") for warning in result.warnings],
        },
    }
    for name, payload in payloads.items():
        with (output_dir / name).open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    descriptions = result.enriched_catalog.descriptions
    print(json.dumps({
        "database_id": catalog.database_id,
        "source_files": len(result.enriched_catalog.sources),
        "catalog_columns": sum(len(table.columns) for table in catalog.tables),
        "bound_columns": len(descriptions),
        "columns_with_meaning_text": sum(
            item.description is not None or item.value_description is not None for item in descriptions
        ),
        "warnings": len(result.warnings),
        "warning_counts": dict(sorted(Counter(item.code for item in result.warnings).items())),
        "output_dir": str(output_dir),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()