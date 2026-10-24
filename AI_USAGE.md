# AI usage (DRAFT - edit so it reflects what YOU actually did and can explain)

## Tools used
- Claude (chat): planning, scaffolding code, writing tests, drafting docs.
- Claude API (runtime, optional): classifies a question into a structured intent via forced tool-use.

## How AI was used during development
1. Asked for a plan first (hybrid design: LLM routes -> validated intent -> deterministic SQL), then built layer by layer:
   data loader -> queries -> interpreter -> service -> API -> UI -> tests.
2. Had AI generate the first version of each module; I reviewed the code, ran it, and used the tests to find gaps.
3. Used an independent calculation (plain csv parsing in the tests) to check the SQL results instead of trusting the app's own output.

## Problems encountered and how they were debugged
| Problem | How found | Fix |
|---|---|---|
| Dirty rows in the CSV (qty 0, blank price, Returned) | Read the file before coding | `sales_raw` / `sales_clean` split with `reject_reason`; revenue counts only valid Completed rows |
| Ambiguous `DD-MM-YYYY` dates | Reviewing sample rows | Explicit `strptime` formats, no auto-detection |
| Canonical spelling depended on the first row seen (`north ` became the label) | Loader test with mixed case | Choose spellings across all rows (`build_canon`) |
| "revenue for Smartphone" silently returned total revenue (wrong answer) | Edge-case test | Rule interpreter reports unrecognised words in `warnings`; LLM path rejects unknown entities (422) |
| "how did revenue change in March" returned a plain total | Edge-case test | Change/compare wording with fewer than two months asks for two months |
| Writes through the DB connection | Thought through safety | `mode=ro` plus a SQLite authorizer; tested with DROP/DELETE/UPDATE/PRAGMA/stacked statements |

## What I'd improve with another day
- Add a validated text-to-SQL fallback (sqlglot AST checks) for questions outside the fixed intents.
- Quarters, date ranges, top-N, averages, multiple filters, follow-up questions with conversation memory.
- Evaluate the LLM interpreter on a labelled question set; add caching and request logging.
- Currency/discount semantics confirmed with the data owner; incremental data loads; Docker packaging.
