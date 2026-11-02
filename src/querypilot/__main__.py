"""python -m querypilot 与 console script 共用同一个入口。"""

from querypilot.entrypoints.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
