"""为 direct-prompt 与单图评测提供一致的执行结果比较规则。"""

import json
import re
from collections import Counter
from bisect import bisect_left


def normalized_rows(rows: list[list[object]]) -> Counter[tuple[str, ...]]:
    """把结果行转成忽略行顺序、保留重复行的多重集合。

    参数:
        rows: SQLite 只读执行器返回的二维结果行。
    返回:
        每行序列化后的元组及其出现次数。
    """
    return Counter(tuple(json.dumps(value, sort_keys=True, default=str) for value in row) for row in rows)


NUMBER_TEXT = re.compile(r"[+-]?\d+(\.\d+)?")
"""能当作数字的文本：可带正负号的十进制整数或小数（去掉首尾空白和末尾一个 % 之后）。"""


def _number(value: int | float) -> int | float:
    """数值的统一写法：整数值的浮点数写成整数，4 与 4.0 相等。"""
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _content_value(value: object) -> tuple:
    """一个值在内容比较里的样子：数字按数值；能解析成数字的文本按那个数字；其他值原样。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return ("number", _number(value))
    if isinstance(value, str):
        text = value.strip().removesuffix("%")
        if NUMBER_TEXT.fullmatch(text):
            return ("number", _number(float(text)) if "." in text else int(text))
    return ("value", json.dumps(value, sort_keys=True, default=str))


def _content_rows(rows: list[list[object]]) -> set[tuple]:
    """结果行在内容比较里的样子：每个值按 _content_value 换写，再按行的集合（去重）。"""
    return {tuple(_content_value(value) for value in row) for row in rows}


def _unit_value(row: list[object], unit: tuple[int, ...]) -> object:
    """一行在一个单元上的值：单列取原值，多列用单个空格拼接。"""
    return row[unit[0]] if len(unit) == 1 else " ".join(row[i] for i in unit)


def _joined_units(gold_rows: list[list[object]], predicted_texts: list[str]) -> list[tuple[int, ...]]:
    """可能被预测的某个文本列覆盖的拼接单元：两列及以上、各行都是文本的金标准列，按某个顺序用空格拼接。

    拼接值必须出现在预测结果里，所以只用金标准第一行逐列往后拼：拼出的前缀加一个空格不是某个预测
    文本值的前缀，就不再往下试。predicted_texts 是预测结果里全部文本值，已排序。
    """
    text_columns = [i for i in range(len(gold_rows[0])) if all(isinstance(row[i], str) for row in gold_rows)]
    first = gold_rows[0]
    predicted = set(predicted_texts)

    def continues(prefix: str) -> bool:
        """有没有预测文本值以 prefix 加一个空格开头。"""
        start = prefix + " "
        position = bisect_left(predicted_texts, start)
        return position < len(predicted_texts) and predicted_texts[position].startswith(start)

    units = []

    def extend(unit: tuple[int, ...], prefix: str) -> None:
        if len(unit) > 1 and prefix in predicted:
            units.append(unit)
        if continues(prefix):
            for i in text_columns:
                if i not in unit:
                    extend(unit + (i,), prefix + " " + first[i])

    for i in text_columns:
        extend((i,), first[i])
    return units


def content_match(predicted_rows: list[list[object]], gold_rows: list[list[object]]) -> bool:
    """判断预测结果是否包含了金标准结果的全部内容，只在形式上不同（ADR 0003）。

    参数:
        predicted_rows: 预测 SQL 的结果行（与金标准相同的执行上限）。
        gold_rows: 金标准 SQL 的结果行。
    返回:
        存在一种覆盖方式时为 True：金标准的每一列（或用空格拼接的几列）对应预测的一列，预测的每一列
        最多用一次、多出的列不管，两边按行的集合比较相同。数值和文本数字按数值比较，不取整。
    """
    if not gold_rows or not predicted_rows:
        return not gold_rows and not predicted_rows
    gold_width = len(gold_rows[0])
    predicted_columns = [frozenset(_content_value(row[j]) for row in predicted_rows)
                         for j in range(len(predicted_rows[0]))]
    predicted_texts = sorted({value for row in predicted_rows for value in row if isinstance(value, str)})
    # 行的集合相同，则每对对应列的取值集合也相同：每个单元只保留取值集合与它相同的预测列作候选。
    candidates: dict[tuple[int, ...], list[int]] = {}
    for unit in [(i,) for i in range(gold_width)] + _joined_units(gold_rows, predicted_texts):
        values = frozenset(_content_value(_unit_value(row, unit)) for row in gold_rows)
        columns = [j for j, predicted in enumerate(predicted_columns) if predicted == values]
        if columns:
            candidates[unit] = columns

    def cover(units: list[tuple[int, ...]], used: list[int]) -> bool:
        """为编号最小的未覆盖金标准列选一个单元和一列预测，覆盖完后比较整行。"""
        covered = {i for unit in units for i in unit}
        if len(covered) == gold_width:
            target = _content_rows([[_unit_value(row, unit) for unit in units] for row in gold_rows])
            return _content_rows([[row[j] for j in used] for row in predicted_rows]) == target
        column = min(set(range(gold_width)) - covered)
        return any(cover(units + [unit], used + [j])
                   for unit, columns in candidates.items()
                   if column in unit and covered.isdisjoint(unit)
                   for j in columns if j not in used)

    return cover([], [])
