"""完整验证 R1.4 源后原子导入；同 corpus 仅允许完整相同复用。"""

from querypilot.knowledge.storage_cli import run_cli


def main(argv: list[str] | None = None) -> int:
    """输入：可选 --corpus-dir/--output；输出：退出码；副作用：事务导入及排他回执。"""
    return run_cli("import", argv)


if __name__ == "__main__":
    raise SystemExit(main())
