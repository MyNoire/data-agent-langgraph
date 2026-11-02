"""管理 database_id 到本地 SQLite 文件的持久化映射。"""

import json
import re
import sqlite3
from pathlib import Path


class RegistryError(ValueError):
    """注册信息无效或请求了未知数据库。"""

    pass


class DatabaseRegistry:
    """只登记配置数据目录内的 SQLite 文件，避免任意文件路径访问。"""

    def __init__(self, data_dir: Path):
        """定位数据库文件和注册表的根目录。

        参数:
            data_dir: 允许登记 SQLite 文件的目录。
        返回:
            None；初始化 data_dir 和 index_path 实例属性。
        """
        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.data_dir / "registry.json"

    def list(self) -> dict[str, str]:
        """读取持久化的数据库登记映射。

        参数:
            无。
        返回:
            database_id 到 SQLite 绝对路径字符串的映射；无登记时为空。
        """
        if not self.index_path.exists():
            return {}
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    def register(self, database_id: str, path: Path) -> Path:
        """验证 ID、路径和 SQLite 文件后持久化登记。

        参数:
            database_id: 仅含小写字母、数字和下划线的数据库 ID。
            path: 必须位于数据目录内的 SQLite 文件路径。
        返回:
            经 resolve 解析后的 SQLite 文件绝对路径。
        异常:
            RegistryError: ID、路径、文件内容无效，或 ID 已绑定其他文件。
            OSError: 文件读写失败。
        """
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", database_id):
            raise RegistryError("database_id must be lowercase letters, digits or underscore")
        # resolve(strict=True) 同时检查文件存在并消除 ../ 与符号链接跳转。
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self.data_dir) or resolved.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
            raise RegistryError("SQLite path must be inside the configured data directory")
        if not resolved.is_file():
            raise RegistryError("SQLite path must be a file")
        try:
            # 以只读方式打开并读取系统表，确认它确实是可用的 SQLite 文件。
            connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
            connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
            connection.close()
        except sqlite3.DatabaseError as exc:
            raise RegistryError("invalid SQLite database") from exc
        entries = self.list()
        if database_id in entries and Path(entries[database_id]) != resolved:
            raise RegistryError("database_id is already registered")
        entries[database_id] = str(resolved)
        # 先写临时文件再替换，避免进程中断留下半份 JSON。
        temp = self.index_path.with_suffix(".tmp")
        temp.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        temp.replace(self.index_path)
        return resolved

    def get(self, database_id: str) -> Path:
        """根据 ID 查找数据库并重新验证路径边界。

        参数:
            database_id: 已登记的数据库 ID。
        返回:
            当前真实的 SQLite 文件绝对路径。
        异常:
            RegistryError: ID 未登记或登记路径逃出数据目录。
            OSError: 登记的文件已不存在或不可访问。
        """
        entry = self.list().get(database_id)
        if entry is None:
            raise RegistryError("unknown database_id")
        path = Path(entry).resolve(strict=True)
        if not path.is_relative_to(self.data_dir):
            raise RegistryError("registered path escaped data directory")
        return path
