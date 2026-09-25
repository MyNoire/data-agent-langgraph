"""在数据库执行之前，以 SQLite 方言解析并限制模型生成的 SQL。"""

import sqlglot
from sqlglot import exp


class SqlRejected(ValueError):
    """SQL 语法或安全策略不符合当前只读基线。"""

    pass


BLOCKED_FUNCTIONS = {"load_extension", "readfile", "writefile", "sqlite_version"}


def guard_sql(sql: str, allowed_tables: set[str], max_rows: int) -> str:
    """解析并校验单条只读查询，必要时收紧 LIMIT。

    参数:
        sql: 用户或模型提供的原始 SQL 文本。
        allowed_tables: 当前数据库 Schema 中允许引用的表名集合。
        max_rows: 查询结果允许的最大行数。
    返回:
        符合 SQLite 方言、最多返回 max_rows 行的 SQL 文本。
    异常:
        SqlRejected: 解析失败、引用越界或使用了不允许的语句结构。
    """
    try:
        # 解析为 AST，避免用正则误判字符串字面量或 SQL 注释。
        statements = sqlglot.parse(sql, read="sqlite")
    except sqlglot.errors.ParseError as exc:
        raise SqlRejected("SQL parse failed") from exc
    if len(statements) != 1 or statements[0] is None:
        raise SqlRejected("exactly one statement is required")
    root = statements[0]
    if not isinstance(root, (exp.Select, exp.Union, exp.Except, exp.Intersect)):
        raise SqlRejected("only SELECT queries are allowed")
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Merge)
    if any(isinstance(node, forbidden) for node in root.walk()):
        raise SqlRejected("write or command expression is forbidden")
    for node in root.walk():
        if isinstance(node, exp.Anonymous) and node.name.lower() in BLOCKED_FUNCTIONS:
            raise SqlRejected("function is forbidden")
    ctes = list(root.find_all(exp.CTE))
    # 当前 sqlglot 版本重写具名 CTE 列时可能丢失列名，因此先保守拒绝。
    if any(cte.args["alias"].args.get("columns") for cte in ctes):
        raise SqlRejected("CTE column alias lists are not supported by the current SQLite transpiler")
    cte_names = {cte.alias_or_name.lower() for cte in ctes}
    # CTE 名称是查询局部别名，不应被误当成真实数据库表。
    referenced = {table.name.lower() for table in root.find_all(exp.Table)} - cte_names
    if not referenced.issubset({name.lower() for name in allowed_tables}):
        raise SqlRejected("query references an unregistered table")
    limit = root.args.get("limit")
    if limit is not None:
        literal = limit.expression
        if not isinstance(literal, exp.Literal) or not literal.is_int:
            raise SqlRejected("LIMIT must be a non-negative integer literal")
        requested = int(literal.this)
        if requested < 0:
            raise SqlRejected("LIMIT must be non-negative")
        if requested <= max_rows:
            return root.sql(dialect="sqlite")
    # 没有 LIMIT 或原 LIMIT 过大时，通过 AST 设置统一上限。
    return root.limit(max_rows).sql(dialect="sqlite")
