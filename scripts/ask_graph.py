"""Ask one question through the first linear LangGraph workflow."""

import argparse
import json

from querypilot.config import get_settings
from querypilot.graph.single import build_single_query_graph


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-id", required=True)
    parser.add_argument("--question", required=True)
    args = parser.parse_args()
    graph = build_single_query_graph(get_settings())
    answer = graph.invoke({"database_id": args.database_id, "question": args.question})
    print(json.dumps(answer, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
