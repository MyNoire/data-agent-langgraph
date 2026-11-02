"""为 direct-prompt 与单图评测提供一致的执行结果比较规则。"""

import json
from collections import Counter


def normalized_rows(rows: list[list[object]]) -> Counter[tuple[str, ...]]:
    """把结果行转成忽略行顺序、保留重复行的多重集合。

    参数:
        rows: SQLite 只读执行器返回的二维结果行。
    返回:
        每行序列化后的元组及其出现次数。
    """
    return Counter(tuple(json.dumps(value, sort_keys=True, default=str) for value in row) for row in rows)
