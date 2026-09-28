"""从固定 BIRD 来源中，确定性选出与 12 题不重叠的 100 题候选集。"""
import hashlib
from collections import Counter, defaultdict
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/bird/mini_dev_sqlite-00000-of-00001.json"
SMOKE_MANIFEST = ROOT / "evals/bird_smoke_manifest.json"
DIFFICULTIES = ("simple", "moderate", "challenging")

QUOTAS = {
    "california_schools": (3, 4, 1),
    "card_games": (3, 5, 2),
    "codebase_community": (3, 5, 2),
    "debit_card_specializing": (3, 4, 1),
    "european_football_2": (3, 5, 2),
    "financial": (2, 4, 2),
    "formula_1": (3, 5, 2),
    "student_club": (2, 4, 2),
    "superhero": (3, 5, 2),
    "thrombosis_prediction": (3, 5, 2),
    "toxicology": (2, 4, 2),
}

def select_cases(cases: list[dict], excluded_ids: set[int]) -> list[dict]:
    """按数据库和难度配额，稳定地选择候选题。

    参数:
        cases: 固定来源文件中的全部 BIRD 题目。
        excluded_ids: 已用于 12 题 smoke 的 question_id。
    返回:
        100 道候选题；同一来源文件和规则下顺序始终相同。
    """

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for case in cases:
        if case["question_id"] not in excluded_ids:
            groups[(case["db_id"], case["difficulty"])].append(case)

    selected = []
    for database_id, quotas in QUOTAS.items():
        for difficulty, count in zip(DIFFICULTIES, quotas):
            group = groups[(database_id, difficulty)]
            group.sort(
                key = lambda case: hashlib.sha256(
                    f"querypilot-bird-final100-v1:{case['question_id']}".encode()
                ).hexdigest()
            )
            if len(group) < count:
                raise ValueError(f"{database_id}/{difficulty} 题目不足")
            selected.extend(group[:count])

    return selected

def main() -> None:
    """校验来源，打印 100 题候选集的组成。

    参数:
        无；读取项目内固定的题目文件和 12 题清单。
    返回:
        None；将题数、难度分布、重叠数和前十个题号打印到终端。
    """

    source_bytes = SOURCE.read_bytes()
    smoke = json.loads(SMOKE_MANIFEST.read_text(encoding="utf-8"))
    actual_hash = hashlib.sha256(source_bytes).hexdigest()
    if actual_hash != smoke["source_sha256"]:
        raise ValueError("BIRD 题目来源与现有 12 题的版本不一致")

    cases = json.loads(source_bytes)
    smoke_ids = set(smoke["question_ids"])
    selected = select_cases(cases, smoke_ids)
    ids = [case["question_id"] for case in selected]

    print("题数:", len(selected))
    print("难度:", dict(Counter(case["difficulty"] for case in selected)))
    print("数据库数:", len({case["db_id"] for case in selected}))
    print("与 12 题重叠:", len(set(ids) & smoke_ids))
    print("前 10 个题号:", ids[:10])


if __name__ == "__main__":
    main()