"""完整源核验后从同一只读 PG 视图复验对象与首次时间。"""

from querypilot.knowledge.storage_cli import run_cli


def main(argv: list[str] | None = None) -> int:
    """输入：可选 --corpus-dir/--output；输出：退出码；副作用：只读 PG、可选新回执。"""
    return run_cli("verify", argv)


if __name__ == "__main__":
    raise SystemExit(main())
