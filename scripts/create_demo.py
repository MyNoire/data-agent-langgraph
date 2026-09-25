"""创建最小客户与订单数据集，供 API 和 Text2SQL smoke 测试使用。"""

import sqlite3
from pathlib import Path

from querypilot.config import get_settings
from querypilot.database.registry import DatabaseRegistry


def main() -> None:
    """首次运行时创建 demo.sqlite，并把它登记为 `demo` 数据库。"""
    data_dir = get_settings().data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "demo.sqlite"
    # 重复执行不会覆盖已经存在的演示数据库。
    if not path.exists():
        with sqlite3.connect(path) as connection:
            connection.executescript("""
                CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL);
                CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id), amount REAL NOT NULL, status TEXT NOT NULL);
                INSERT INTO customers VALUES (1, 'Alice', 'East'), (2, 'Bob', 'West'), (3, 'Chen', 'East');
                INSERT INTO orders VALUES (1, 1, 120, 'paid'), (2, 1, 80, 'paid'), (3, 2, 50, 'pending'), (4, 3, 200, 'paid');
            """)
    DatabaseRegistry(data_dir).register("demo", path)
    print(f"registered demo: {path}")


if __name__ == "__main__":
    main()
