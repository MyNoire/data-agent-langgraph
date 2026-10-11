"""R1.3 离线渲染入口；在新目录中输出三份知识草稿。"""

import argparse
from collections import Counter
import json
from pathlib import Path

from querypilot.knowledge.render import render_knowledge


def main() -> None:
    """读取修正包并导出 JSONL。

    参数:
        无函数参数；CLI 接收 --bundle 输入目录、--output-dir 新输出目录，
        以及可选 --semantics 离线推断摘要配置。
    返回:
        None；向标准输出打印数量摘要。
    副作用:
        创建新输出目录和三份 JSONL；已有目录或无效输入会报错。
    """
    parser = argparse.ArgumentParser(description="将已批准的结构说明渲染为知识草稿")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--semantics", type=Path, help="离线推断的表与关系摘要 JSON")
    args = parser.parse_args()
    draft = render_knowledge(args.bundle, args.semantics)
    groups = {"documents": draft.documents, "chunks": draft.chunks, "relations": draft.relations}
    # 先完成全部序列化，再创建新目录，减少输入错误留下半成品的机会。
    texts = {
        name: "".join(json.dumps(item.model_dump(mode="json"), ensure_ascii=False) + "\n"
                      for item in items)
        for name, items in groups.items()
    }
    args.output_dir.mkdir(parents=True, exist_ok=False)
    for name, content in texts.items():
        with (args.output_dir / f"{name}.jsonl").open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    print(json.dumps({
        "status": draft.status,
        "documents": len(draft.documents), "chunks": len(draft.chunks), "relations": len(draft.relations),
        "chunk_types": dict(Counter(chunk.knowledge_type for chunk in draft.chunks)),
        "max_chunk_chars": max((len(chunk.content) for chunk in draft.chunks), default=0),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
