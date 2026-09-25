# Phase 0 security boundary

- Registration accepts only existing `.db`/`.sqlite`/`.sqlite3` files inside `QUERYPILOT_DATA_DIR`; path resolution blocks traversal and symlink escapes.
- The guard accepts a single SQLite SELECT query, checks referenced tables against the registered schema, rejects known dangerous functions, and applies a maximum LIMIT.
- SQLite opens with `mode=ro`, sets `query_only`, and uses an authorizer that permits only reads, SELECT, and allowed functions. A progress handler limits CPU time, while the response is capped by row count and JSON byte size.
- Structured stdout audit events include SQL guard decisions and successful execution timing/row counts. Logs can contain query literals, so they must be access controlled; durable audit storage and redaction are pending.
- This is a minimum local baseline. It does not yet validate every column reference, block all expensive query plans, guarantee cancellation during lock waits, provide authenticated registry access, or secure arbitrary remote PostgreSQL connections. Do not expose the API to untrusted users.
- Named CTE column lists are rejected because the pinned sqlglot version drops those names when emitting SQLite SQL; Phase 1 must fix or replace this rewrite path before enabling them.
- Evaluation answers and gold SQL are executed under the same limits. Results with more rows than the limit can give misleading execution matches; BIRD scoring requires a separate complete-result or official-harness mode before publication.
