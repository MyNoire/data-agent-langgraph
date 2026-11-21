"""内容匹配：形式不同、内容相同的结果算对；内容有任何不同都不算（ADR 0003）。"""

from querypilot.evaluation.api import content_match, strict_execution_match


def test_extra_columns_and_column_order_do_not_matter():
    """预测多选了 ID 列，姓和名的顺序也反了：内容与金标准相同。"""
    gold = [["Sacha", "Harrison"], ["Elijah", "Allen"]]
    predicted = [["rec1", "Harrison", "Sacha"], ["rec2", "Allen", "Elijah"]]

    assert content_match(predicted, gold)


def test_duplicate_rows_do_not_matter():
    """金标准 JOIN 出了重复行（1387），预测用了 DISTINCT：按行的集合比较。"""
    gold = [["Sacha", "Harrison"], ["Elijah", "Allen"], ["Elijah", "Allen"]]
    predicted = [["Sacha", "Harrison"], ["Elijah", "Allen"]]

    assert content_match(predicted, gold)
    assert content_match(gold, predicted)


def test_a_number_written_as_text_is_the_same_number():
    """226、227、255：百分比写成带 % 的字符串或纯数字字符串，数值完全相同。"""
    assert content_match([["44.315%"]], [[44.315]])
    assert content_match([["39.75203"]], [[39.75203]])
    assert content_match([[" 3.84615% "]], [[3.84615]])


def test_an_integer_and_the_same_float_are_the_same_number():
    """类型不同、数值相同：4 与 4.0。"""
    assert content_match([[4.0]], [[4]])


def test_a_column_joining_several_gold_columns_with_a_space_covers_them():
    """1410：全名拼成一列，金标准是姓、名两列，另有一列金额。"""
    gold = [["Sacha", "Harrison", 866.25]]
    predicted = [["Sacha Harrison", 866.25]]

    assert content_match(predicted, gold)


def test_joined_columns_may_come_in_any_order_and_need_not_be_adjacent():
    """拼接不限顺序和位置：姓在前（"Harrison Sacha"）也能覆盖名、姓两列，中间隔着金额列也一样。"""
    gold = [["Sacha", 866.25, "Harrison"], ["Elijah", 20.2, "Allen"]]
    predicted = [["Harrison Sacha", 866.25], ["Allen Elijah", 20.2]]

    assert content_match(predicted, gold)


def test_numbers_equal_only_after_rounding_do_not_match():
    """数值必须完全相等：45.4545 和 45.45454545 不是同一个值。"""
    assert not content_match([[45.45454545]], [[45.4545]])
    assert not content_match([["45.45%"]], [[45.4545]])


def test_a_ratio_and_the_same_percentage_do_not_match():
    """比例和百分比要换算才相等，数值本身不同。"""
    assert not content_match([[0.44315]], [[44.315]])


def test_a_missing_row_does_not_match():
    """少了一行内容：两笔支出只列出一笔。"""
    assert not content_match([["Posters"]], [["Posters"], ["Pizza"]])


def test_an_extra_row_with_other_content_does_not_match():
    """多出一行金标准里没有的内容（247：多列出了 cl、h）。"""
    assert not content_match([["na"], ["zn"], ["cl"]], [["na"], ["zn"]])


def test_a_different_shape_of_the_same_answer_does_not_match():
    """1322：金标准返回 4 行 1，预测返回 1 行 4；行的集合不同就不算，金标准的问题另行标注。"""
    assert not content_match([[4]], [[1], [1], [1], [1]])


def test_joining_with_another_separator_does_not_cover_the_columns():
    """只认单个空格拼接；"Harrison, Sacha" 这种写法不算。"""
    assert not content_match([["Harrison, Sacha"]], [["Sacha", "Harrison"]])


def test_empty_results_match_only_each_other():
    """两边都为空才算匹配；一边为空另一边不为空都不算。"""
    assert content_match([], [])
    assert not content_match([], [["Meeting"]])
    assert not content_match([["Meeting"]], [])


def test_null_matches_only_null():
    """NULL 只等于 NULL，不等于空字符串或 0。"""
    assert content_match([[None]], [[None]])
    assert not content_match([[""]], [[None]])
    assert not content_match([[0]], [[None]])


def test_results_that_match_strictly_also_match_by_content():
    """严格执行匹配为真（行顺序不同、带重复行）时，内容匹配一定为真。"""
    gold = [["c", 2], ["o", 1], ["o", 1]]
    predicted = [["o", 1], ["c", 2], ["o", 1]]

    assert content_match(predicted, gold)


def test_strict_execution_match_ignores_row_order_but_keeps_duplicates_and_column_layout():
    """严格执行匹配：行顺序无所谓；重复行、列的顺序和值的写法都要相同。"""
    gold = [["a", 1], ["b", 2], ["b", 2]]

    assert strict_execution_match([["b", 2], ["a", 1], ["b", 2]], gold)
    assert not strict_execution_match([["a", 1], ["b", 2]], gold)
    assert not strict_execution_match([[1, "a"], [2, "b"], [2, "b"]], gold)
    assert not strict_execution_match([["a", "1"], ["b", "2"], ["b", "2"]], gold)
