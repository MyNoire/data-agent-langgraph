"""RRF（Reciprocal Rank Fusion）：按名次融合几路已排好序的检索结果。"""

RRF_K = 60


def reciprocal_rank_fusion(rankings: list[list[dict]]) -> list[dict]:
    """把几路已排好序的检索行融合成一路。

    参数：
        rankings：每一路是按名次排好的行，同一个 chunk_id 在一路里最多出现一次。
    返回：
        每个 Chunk 一行，score 换成 RRF 分 Σ 1/(60 + rank)（rank 从 1 开始，各路权重相同），
        按 RRF 分降序排列，同分按 chunk_key（Python 字符串按码点比较，与 SQL 的 COLLATE "C" 一致）。
        其余字段取该 Chunk 第一次出现的那一行。
    """
    fused: dict[str, dict] = {}
    for ranking in rankings:
        for rank, row in enumerate(ranking, start=1):
            entry = fused.setdefault(row["chunk_id"], {**row, "score": 0.0})
            entry["score"] += 1 / (RRF_K + rank)
    return sorted(fused.values(), key=lambda row: (-row["score"], row["chunk_key"]))
