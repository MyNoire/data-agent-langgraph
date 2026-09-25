"""从 SQLite 系统表读取最小 Schema，并格式化为模型输入。"""

import sqlite3
from pathlib import Path


def inspect_sqlite(path: Path) -> dict[str, list[str]]:
    """只读检查 SQLite 数据库中的用户表和列名。

    参数:
        path: SQLite 文件路径。
    返回:
        表名到列名列表的映射；跳过 sqlite_ 内部表。
    异常:
        sqlite3.DatabaseError: 文件不是可用的 SQLite 数据库。
    """
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        # 表名来自 sqlite_master，但仍需转义双引号后才能放进 PRAGMA 标识符。
        return {
            name: [row[1] for row in connection.execute(f'PRAGMA table_info("{name.replace(chr(34), chr(34) * 2)}")')]
            for (name,) in rows
        }
    finally:
        connection.close()


def format_schema(schema: dict[str, list[str]]) -> str:
    """把表列映射格式化为模型可读的简洁 Schema。

    参数:
        schema: 表名到列名列表的映射。
    返回:
        每表一行的 ``table(column, ...)`` 文本；空映射返回空字符串。
    """
    return "\n".join(f"{table}({', '.join(columns)})" for table, columns in schema.items())
