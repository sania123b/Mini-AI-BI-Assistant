# Mini AI Business Intelligence Assistant

Ask sales questions in plain English; get an answer plus the SQL, parameters and rows that produced it.

## Setup
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# optional - enables the LLM interpreter (without it, a deterministic rule-based interpreter is used)
export ANTHROPIC_API_KEY=sk-ant-...                    # Windows: set ANTHROPIC_API_KEY=...
```

## Run
```bash
uvicorn app.main:app --reload            # API on http://127.0.0.1:8000  (docs at /docs)
streamlit run ui/streamlit_app.py        # UI on http://localhost:8501
curl -X POST localhost:8000/ask -H 'content-type: application/json' \
     -d '{"question":"Which region generated the highest sales?"}'
```
The database (`data/sales.db`) is built from `data/sales.csv` on first start. Delete it to reload.
Other endpoints: `GET /health`, `GET /data-quality` (load report).

## Tests
```bash
python -m pytest -q        # 42 tests, no network or API key needed
```

## How data is stored and queried
- `sales_raw` - the CSV exactly as read (all text). Nothing is ever dropped.
- `sales_clean` - typed columns + `month`, `is_valid`, `reject_reason`, `revenue`. All analytics read this table.
- Every question maps to one of a fixed set of **parameterised SQL templates** (`app/queries.py`).
  Values are bound parameters; only whitelisted column names are interpolated.
- **Revenue** = `quantity x unit_price - discount_amount`, counting only valid rows with status `Completed`.

### Data issues found and how they are handled
| Row | Issue | Handling |
|---|---|---|
| TXN0034 | quantity 0 | rejected (`zero or negative quantity`) |
| TXN0051 | unit price blank | rejected (`missing unit price`) |
| TXN0033 | status Returned | valid row, excluded from revenue |
| all | `DD-MM-YYYY` dates | parsed with explicit formats (never guessed) |

The loader also rejects: duplicate IDs, impossible dates, negative/non-numeric/non-integer quantities, negative or
oversized discounts, unknown statuses; normalises whitespace/case; handles empty files, missing columns, `;`/tab delimiters.
Every response lists the excluded rows under `excluded`.

## Supported questions
Total revenue (optionally per product/region/category/month), top/lowest product / region / category by revenue or units,
monthly trend (with month-over-month change), and comparison of two months. Ties return all winners.

## Preventing wrong or unsafe answers
1. The LLM only **classifies** the question into a JSON intent (forced tool call). It never writes SQL or the answer text.
2. The intent is **re-validated** (intent whitelist, entities must exist in the DB, months must parse) - a hallucinated value gives a 422, not a guess.
3. Answer text is generated from the returned rows, so numbers cannot be invented.
4. Read-only: connection opened `mode=ro` **and** a SQLite authorizer denies everything except SELECT/READ. Stacked statements are rejected by sqlite3.
5. Input guards: empty/over-long input (400), destructive wording, raw SQL, prompt-injection phrases (422).
6. If the LLM is down or returns garbage, the rule-based interpreter takes over and a warning is shown.
7. Unknown words the rule interpreter skipped are reported in `warnings`; assumptions (year, "sales" = revenue) are shown.

## Known limitations
- Rule-based interpreter is keyword-driven: unusual phrasing may be refused (the LLM path handles more).
- Only 2026 data exists; a month without a year is assumed to be the latest year in the data.
- No quarters/weeks/date ranges, no multi-product or multi-region filters, no "top N" or averages.
- Discount is assumed to be a per-line amount (500 on 6 monitors reads as a line total); confirm with the data owner.
- Currency is not in the data, so no symbol is shown.
- The word "may" is read as the month May.
- `sales.db` is rebuilt from CSV, not incrementally updated.
