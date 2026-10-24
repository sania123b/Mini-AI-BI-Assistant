# Architecture

```
 Streamlit UI ──POST /ask──▶ FastAPI (app/main.py)
                                  │
                         service.ask (app/service.py)
   1 input guards ─▶ 2 interpret ─▶ 3 validate ─▶ 4 SQL template ─▶ 5 narrate from rows
        │                │              │               │
   empty/long/       LLM tool-call   intent whitelist,  read-only SQLite
   destructive/SQL/  (or rule-based  entity + month     (mode=ro + authorizer)
   injection         fallback)       resolution          sales_raw / sales_clean
```

Why not free-form text-to-SQL: the five required questions are fixed analyses, so templates give deterministic,
testable, injection-proof results. The LLM adds natural-language flexibility without being able to touch SQL.
If text-to-SQL were added, it would go through an AST validator (sqlglot: single SELECT, table/column allowlist, forced LIMIT)
on top of the read-only connection already in place.

Agentic view: the interpreter is a router choosing a "tool" (intent); each intent is a deterministic tool; the
service validates the call, executes it and reports the observation.

## Uploads
```
CSV bytes -> ingest.parse (limits, delimiter, encoding) -> ingest.auto_map (header aliases + value sniffing)
          -> ingest.prepare (date format, number/status cleaning, canonical rows) -> db.load_rows -> <random id>.db
```
Everything downstream (queries, interpreter, validator, tests) is unchanged: uploads are *adapted to* the canonical schema,
and the per-dataset `meta.config` (labels, has_units, revenue definition) drives wording and what can be asked.
