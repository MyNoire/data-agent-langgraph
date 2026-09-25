"""Export a reviewable eval artifact without provider connection details.

The full local result remains under ``results/`` and is Git-ignored. The
published artifact retains per-case SQL and outcomes so failures can be audited.
"""

import argparse
import json
from pathlib import Path


TOP_LEVEL = (
    "dataset_sha256", "provider", "status", "evidence_mode", "model",
    "model_temperature", "prompt_version", "count", "max_rows",
    "query_timeout_ms", "valid_sql_rate", "execution_accuracy",
    "p50_latency_ms", "p95_latency_ms",
)
RECORD = (
    "index", "database_id", "evidence_used", "predicted_sql", "valid_sql",
    "execution_match", "latency_ms",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    raw = json.loads(args.input.read_text(encoding="utf-8"))
    public = {key: raw[key] for key in TOP_LEVEL if key in raw}
    public["records"] = [
        {key: record[key] for key in RECORD if key in record}
        for record in raw["records"]
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(public, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
