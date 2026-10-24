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

## Upload your own CSV
In the UI choose **Upload my own CSV** (or call the API). Any sales-like CSV works if it has a **date** column and a
**revenue/amount** column (or **quantity + unit price**). Product / category / region / status / ID / discount are optional.
```bash
curl -F file=@my_sales.csv localhost:8000/datasets            # -> {"dataset_id": "...", "labels": ..., "warnings": ...}
curl -X POST localhost:8000/datasets/<id>/ask -H 'content-type: application/json' \
     -d '{"question":"Which city generated the highest revenue?"}'
```
| Endpoint | Purpose |
|---|---|
| `POST /datasets` (multipart `file`, optional `mapping`, `day_first`, `completed_values`) | upload; returns id + how columns were understood |
| `POST /datasets/{id}/ask` | ask about that upload |
| `GET /datasets/{id}` / `GET /datasets/{id}/data-quality` | profile / rejected rows |
| `DELETE /datasets/{id}` | remove the data |

How it adapts to unfamiliar files (`app/ingest.py`):
- **Column roles** are detected from header names (exact, word-subset and fuzzy matches against alias lists, e.g. `Item`, `City`,
  `Total Amount`, `Qty`), assigned greedily so `Sales Channel` isn't mistaken for `Sales`. A date column is also found by its values.
- Your own header names become the labels, so "which **city**…" works. Missing columns are refused politely
  (`no such column`, `no quantity`) instead of guessed.
- **Dates**: format detected per file (ISO, DD/MM, MM/DD, `15 Mar 2026`, with times...). If it could be day-first or month-first, the
  default is day-first, a warning is shown and the user can switch.
- **Numbers**: currency symbols, thousands separators and `(500)` negatives are understood. Negative or non-numeric amounts are rejected and listed.
- **Status**: values like Delivered/Paid/Completed count as sales; if none are recognised the UI asks which statuses to count.
- **Repeated order IDs** (several lines per order) are not treated as duplicates.
- If it can't decide, the API returns `needs_mapping` with the columns and a suggestion, and the UI shows a mapping form.

Safety and limits: max 5 MB, 100,000 rows, 60 columns; `.csv/.tsv/.txt` only; binary files rejected. Each upload is its own SQLite file
named by a random 128-bit id (ids are validated, so no path traversal), opened read-only, auto-deleted after 24 h
(`BI_UPLOAD_TTL_HOURS`). Column headers are never put into SQL: data is mapped into fixed internal columns. With an API key set, the
question text and up to 40 sample values per product/region/category column are sent to the LLM provider; the rest of your data is not.
There is no login, so treat it as a demo and don't host confidential uploads on a public instance without adding authentication.

## Tests
```bash
python -m pytest -q        # 65 tests, no network or API key needed
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
- Uploads: three breakdown columns only (product, category, region, shown with your own header names); other text columns are ignored.
  Mixed date formats in one column, European decimals (`1.234,56`) and exact-duplicate rows without an ID are not handled.
- Uploads have no authentication or per-user quota.
- Rule-based interpreter is keyword-driven: unusual phrasing may be refused (the LLM path handles more).
- Only 2026 data exists; a month without a year is assumed to be the latest year in the data.
- No quarters/weeks/date ranges, no multi-product or multi-region filters, no "top N" or averages.
- Discount is assumed to be a per-line amount (500 on 6 monitors reads as a line total); confirm with the data owner.
- Currency is not in the data, so no symbol is shown.
- The word "may" is read as the month May.
- `sales.db` is rebuilt from CSV, not incrementally updated.
