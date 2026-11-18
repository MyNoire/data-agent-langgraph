"""从 BIRD 题目文件取出指定数据库的全部题，生成 SQL 题集。"""

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path


class SqlCaseSetError(ValueError):
    """SQL 题集无法生成。error_type 是固定的错误类别，消息不含题目和路径。"""

    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type


def select_database_cases(source: Path, database_ids: list[str], frozen_manifest: Path, output: Path) -> dict:
    """生成指定数据库的 SQL 题集。

    参数：
        source：BIRD Mini-Dev 题目文件。
        database_ids：要取的数据库。
        frozen_manifest：冻结题集的 manifest，提供冻结题号、来源文件的 sha256 和数据集信息。
        output：题集输出位置。
    返回：
        摘要：题数、各库题数、冻结题数，以及题集文件和 manifest 的位置。
    副作用：
        写出题集文件（题目按来源文件里的顺序，每题多一个 frozen 标记），并在旁边写出可提交的
        `<题集文件名>_manifest.json`：题号、冻结题号、来源文件和题集文件的 sha256，不含原题和 SQL。
    异常：
        SqlCaseSetError：来源文件的 sha256 和冻结 manifest 记录的不一致（source_version_mismatch），
        或某个指定的库在来源文件里没有题（data_input_invalid）；此时不写任何文件。
    """
    frozen = json.loads(frozen_manifest.read_text(encoding="utf-8"))
    frozen_ids = set(frozen["question_ids"])
    source_bytes = source.read_bytes()
    source_hash = sha256(source_bytes).hexdigest()
    if source_hash != frozen["source_sha256"]:
        raise SqlCaseSetError("source_version_mismatch", "source differs from baseline")
    cases = [{**case, "frozen": case["question_id"] in frozen_ids}
             for case in json.loads(source_bytes) if case["db_id"] in database_ids]
    # 库名写错时这个库一道题都取不到，不能悄悄生成一份变小的题集。
    if {case["db_id"] for case in cases} != set(database_ids):
        raise SqlCaseSetError("data_input_invalid", "case set input is invalid")
    output.parent.mkdir(parents=True, exist_ok=True)
    # 固定用 LF：manifest 记了题集文件的 sha256，换行符随系统变会让同一份来源得到不同的值。
    output.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

    question_ids: dict[str, list[int]] = {database_id: [] for database_id in database_ids}
    for case in cases:
        question_ids[case["db_id"]].append(case["question_id"])
    manifest = {
        "dataset": frozen["dataset"], "revision": frozen["revision"], "source_url": frozen["source_url"],
        "source_sha256": source_hash,
        "case_set_file": output.name,
        "case_set_sha256": sha256(output.read_bytes()).hexdigest(),
        "selection": "来源文件中指定数据库的全部题目，按来源文件里的顺序；frozen 标记题号在冻结清单里的题。",
        "question_ids": question_ids,
        "frozen_question_ids": [case["question_id"] for case in cases if case["frozen"]],
    }
    manifest_file = output.with_name(f"{output.stem}_manifest.json")
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
                             newline="\n")
    return {"count": len(cases), "databases": dict(Counter(case["db_id"] for case in cases)),
            "frozen": sum(case["frozen"] for case in cases),
            "output": str(output), "manifest": str(manifest_file)}
