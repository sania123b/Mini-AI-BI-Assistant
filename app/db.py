"""Load, validate and store sales rows in SQLite; read-only query access."""
import csv
import json
import math
import os
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

REQUIRED_COLUMNS = ["Transaction_Id", "Order_date", "Product", "Category", "Region",
                    "Sales_channel", "Quantity", "Unit_price", "Discount_amount", "Status"]
VALID_STATUSES = {"Completed", "Returned", "Cancelled"}
DEFAULT_CONFIG = {
    "labels": {"product": "Product", "category": "Category", "region": "Region"},
    "has_units": True,
    "revenue_definition": "Revenue = quantity x unit price - discount (discount treated as a per-line amount).",
    "status_note": "Only valid orders with status 'Completed' are counted; returned, cancelled and "
                   "invalid rows are excluded (listed under 'excluded').",
    "warnings": [], "mapping": {}, "source": "sales.csv"}


class DataError(Exception):
    """Raised when the CSV or database cannot be used at all."""


SCHEMA = """
DROP TABLE IF EXISTS sales_raw;
DROP TABLE IF EXISTS sales_clean;
DROP TABLE IF EXISTS meta;
CREATE TABLE sales_raw (            -- rows as read (uploads: after column mapping), nothing dropped
  row_num INTEGER PRIMARY KEY, transaction_id TEXT, order_date TEXT, product TEXT,
  category TEXT, region TEXT, sales_channel TEXT, quantity TEXT, unit_price TEXT,
  discount_amount TEXT, status TEXT);
CREATE TABLE sales_clean (          -- typed + validated; analytics read only this
  row_num INTEGER PRIMARY KEY, transaction_id TEXT, order_date TEXT, month TEXT,
  product TEXT, category TEXT, region TEXT, sales_channel TEXT,
  quantity INTEGER, unit_price REAL, discount_amount REAL, status TEXT,
  is_valid INTEGER NOT NULL, reject_reason TEXT, revenue REAL);
CREATE INDEX idx_clean_month ON sales_clean(month);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _num(text):
    """'' -> None; '1,200' -> 1200.0; garbage/NaN/inf -> ValueError."""
    s = (text or "").strip().replace(",", "")
    if s == "":
        return None
    v = float(s)
    if not math.isfinite(v):
        raise ValueError(s)
    return v


def build_canon(rows):
    """One canonical spelling per case-insensitive value, chosen across ALL rows:
    prefer a mixed-case spelling ('USB-C Hub') over 'usb-c hub'/'USB-C HUB', then the most common."""
    votes = {}
    for col, kind in (("Product", "product"), ("Category", "category"),
                      ("Region", "region"), ("Sales_channel", "channel")):
        for r in rows:
            v = " ".join((r.get(col) or "").split())
            if v:
                votes.setdefault(kind, {}).setdefault(v.lower(), Counter())[v] += 1
    canon = {}
    for kind, groups in votes.items():
        for low, spellings in groups.items():
            mixed = [(n, sp) for sp, n in spellings.items() if sp != sp.lower() and sp != sp.upper()]
            pool = mixed or [(n, sp) for sp, n in spellings.items()]
            best = max(pool)[1]
            canon.setdefault(kind, {})[low] = best if mixed or kind == "product" else best.title()
    return canon


def _canon(ctx, kind, value):
    value = " ".join(value.split())
    return ctx.get("canon", {}).get(kind, {}).get(value.lower(), value)


def clean_row(raw, ctx, opts=None):
    """Return (clean_dict, reject_reason_or_None). Collects ALL problems for a row.
    opts: strict (sample data rules) | mapped_dims | price_label | allow_zero_price."""
    opts = opts or {}
    strict = opts.get("strict", True)
    mapped = opts.get("mapped_dims", {"product", "category", "region"})
    plabel = opts.get("price_label", "unit price")
    r = {k: (raw.get(k) or "").strip() for k in REQUIRED_COLUMNS}
    errors = []
    out = {"transaction_id": r["Transaction_Id"], "order_date": None, "month": None,
           "product": _canon(ctx, "product", r["Product"]),
           "category": _canon(ctx, "category", r["Category"]),
           "region": _canon(ctx, "region", r["Region"]),
           "sales_channel": _canon(ctx, "channel", r["Sales_channel"]),
           "quantity": None, "unit_price": None, "discount_amount": None,
           "status": r["Status"].title()}

    ids = ctx.setdefault("ids", set())
    if not r["Transaction_Id"]:
        errors.append("missing transaction id")
    elif r["Transaction_Id"] in ids:
        errors.append("duplicate transaction id")
    else:
        ids.add(r["Transaction_Id"])

    d = None
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):   # explicit formats: never guess
        try:
            d = datetime.strptime(r["Order_date"], fmt).date()
            break
        except ValueError:
            pass
    if d is None:
        errors.append("invalid or missing date")
    elif not 1970 <= d.year <= 2100:
        errors.append("date out of range")
    else:
        out["order_date"], out["month"] = d.isoformat(), d.strftime("%Y-%m")

    for field in ("product", "category", "region"):
        if not out[field]:
            if strict:
                errors.append(f"missing {field}")
            elif field in mapped:
                out[field] = "Unknown"
    if strict:
        if out["status"] not in VALID_STATUSES:
            errors.append("unknown status")
    elif not out["status"]:
        out["status"] = "Unknown"

    qty = price = None
    try:
        q = _num(r["Quantity"])
        if q is None:
            errors.append("missing quantity")
        elif q != int(q):
            errors.append("non-integer quantity")
        elif q <= 0:
            errors.append("zero or negative quantity")
        else:
            qty = out["quantity"] = int(q)
    except ValueError:
        errors.append("invalid quantity")
    try:
        p = _num(r["Unit_price"])
        if p is None:
            errors.append(f"missing {plabel}")
        elif p < 0 or (p == 0 and not opts.get("allow_zero_price")):
            errors.append(f"non-positive {plabel}")
        else:
            price = out["unit_price"] = p
    except ValueError:
        errors.append(f"invalid {plabel}")
    disc = 0.0
    try:
        disc = _num(r["Discount_amount"])
        disc = 0.0 if disc is None else disc          # blank discount = no discount
        if disc < 0:
            errors.append("negative discount")
        else:
            out["discount_amount"] = disc
            if qty and price is not None and disc > qty * price:
                errors.append("discount exceeds line total")
    except ValueError:
        errors.append("invalid discount")

    out["is_valid"] = 0 if errors else 1
    out["reject_reason"] = "; ".join(errors) or None
    out["revenue"] = round(qty * price - disc, 2) if not errors else None
    return out, out["reject_reason"]


def load_rows(rows, db_path, config=None, opts=None):
    """Rebuild the database from canonical rows and return a load report."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    tmp = str(db_path) + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(SCHEMA)
        ctx, reasons, rejected, non_completed = {"canon": build_canon(rows)}, Counter(), [], []
        for i, raw in enumerate(rows, start=1):
            conn.execute("INSERT INTO sales_raw VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (i, *[(raw.get(c) or "") for c in REQUIRED_COLUMNS]))
            c, reason = clean_row(raw, ctx, opts)
            c["row_num"] = i
            cols = list(c)
            conn.execute(f"INSERT INTO sales_clean ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                         [c[k] for k in cols])
            if reason:
                rejected.append({"transaction_id": c["transaction_id"] or f"row {i}", "reason": reason})
                for part in reason.split("; "):
                    reasons[part] += 1
            elif c["status"] != "Completed":
                non_completed.append({"transaction_id": c["transaction_id"], "status": c["status"]})
        report = {"rows_read": len(rows), "valid_rows": len(rows) - len(rejected),
                  "rejected_rows": len(rejected), "rejection_reasons": dict(reasons),
                  "rejected": rejected[:200], "non_completed_valid_rows": non_completed[:200]}
        conn.execute("INSERT INTO meta VALUES ('load_report', ?)", (json.dumps(report),))
        conn.execute("INSERT INTO meta VALUES ('config', ?)", (json.dumps(cfg),))
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp, db_path)
    return report


def load_csv(csv_path, db_path):
    """Load the bundled sample file (already in the canonical column layout)."""
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise DataError(f"CSV file not found: {csv_path}")
    try:
        text = csv_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = csv_path.read_text(encoding="latin-1")
    lines = text.splitlines()
    if not lines or not lines[0].strip():
        raise DataError("CSV file is empty")
    reader = csv.DictReader(lines, delimiter=max(",;\t", key=lines[0].count))
    reader.fieldnames = [h.strip() for h in reader.fieldnames or []]
    missing = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
    if missing:
        raise DataError(f"CSV is missing required columns: {', '.join(missing)}")
    rows = [r for r in reader if any((v or "").strip() for k, v in r.items() if k)]
    return load_rows(rows, db_path, {"source": csv_path.name})


def _authorizer(action, *_):
    # Allow only reading. Any write/DDL/ATTACH/PRAGMA is denied by SQLite itself.
    ok = (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION)
    return sqlite3.SQLITE_OK if action in ok else sqlite3.SQLITE_DENY


def connect(db_path):
    """Read-only connection with a deny-everything-but-SELECT authorizer."""
    if not os.path.exists(db_path):
        raise DataError(f"Database not found: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.set_authorizer(_authorizer)
    return conn


def run_select(conn, sql, params=()):
    """Execute ONE parameterised statement (sqlite3 rejects stacked statements)."""
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.Error as e:
        raise DataError(f"Query failed: {e}") from e
