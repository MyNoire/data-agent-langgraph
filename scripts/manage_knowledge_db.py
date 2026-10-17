"""管理扩展、最小权限和版本化知识库迁移；不隐式创建数据库。"""

from querypilot.knowledge.storage_cli import run_cli


def main(argv: list[str] | None = None) -> int:
    """输入：可选 CLI 参数；输出：退出码；副作用：按 bootstrap/migrate/check 访问 PG。"""
    return run_cli("manage", argv)


if __name__ == "__main__":
    raise SystemExit(main())
