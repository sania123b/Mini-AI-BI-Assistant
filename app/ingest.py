"""Turn ANY reasonably tabular CSV into the canonical layout the analytics engine expects.
Steps: parse safely -> auto-detect which column plays which role -> detect date format ->
clean numbers/statuses -> canonical rows. If anything is ambiguous we ask (needs_mapping)."""
import csv
import difflib
import io
import os
import re
from datetime import datetime

from .db import REQUIRED_COLUMNS

MAX_BYTES = int(os.getenv("BI_MAX_UPLOAD_MB", "5")) * 1024 * 1024
MAX_ROWS, MAX_COLS = 100_000, 60
ROLES = ["date", "amount", "quantity", "unit_price", "discount", "status", "id",
         "product", "category", "region", "channel"]
ALIASES = {
    "date": ["order date", "date", "invoice date", "transaction date", "sale date", "sales date",
             "purchase date", "created at", "orderdate", "timestamp", "order dt"],
    "amount": ["revenue", "sales", "amount", "total", "total amount", "sales amount", "net sales",
               "order value", "order total", "line total", "gross sales", "turnover", "net amount",
               "total price", "sale amount", "value"],
    "quantity": ["quantity", "qty", "units", "units sold", "unit sold", "pieces", "order quantity", "count"],
    "unit_price": ["unit price", "price", "selling price", "price per unit", "rate", "mrp"],
    "discount": ["discount", "discount amount", "disc", "rebate", "discount value"],
    "status": ["status", "order status", "payment status", "fulfilment status", "fulfillment status"],
    "id": ["transaction id", "order id", "txn id", "invoice no", "invoice number", "order no",
           "order number", "transaction no", "id"],
    "product": ["product", "product name", "item", "item name", "sku", "product title", "article", "service"],
    "category": ["category", "product category", "department", "segment", "product type", "type", "group"],
    "region": ["region", "area", "zone", "territory", "state", "city", "country", "location",
               "market", "branch", "store"],
    "channel": ["sales channel", "channel", "platform", "source"]}
ROLE_LABEL = {"date": "Order date", "amount": "Revenue / amount", "quantity": "Quantity",
              "unit_price": "Unit price", "discount": "Discount", "status": "Status",
              "id": "Transaction / order ID", "product": "Product (or item)",
              "category": "Category", "region": "Region (or city/state)", "channel": "Sales channel"}
POSITIVE = {"completed", "complete", "delivered", "paid", "shipped", "success", "successful", "fulfilled",
            "closed won", "won", "done", "settled", "captured", "confirmed", "finished", "approved",
            "sold", "invoiced"}
DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%m-%d-%Y", "%m/%d/%Y", "%d.%m.%Y",
                "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M", "%d-%b-%y", "%d-%b-%Y", "%Y%m%d"]


class IngestError(Exception):
    def __init__(self, status, code, message, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


# ------------------------------------------------------------------ parsing
def parse_csv_bytes(data):
    if not data:
        raise IngestError(422, "invalid_csv", "The file is empty.")
    if len(data) > MAX_BYTES:
        raise IngestError(413, "file_too_large", f"File is larger than {MAX_BYTES // 1024 // 1024} MB.")
    if b"\x00" in data[:8192]:
        raise IngestError(422, "invalid_csv", "This doesn't look like a text CSV file.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    delim = max(",;\t|", key=first.count)
    if first.count(delim) == 0:
        raise IngestError(422, "invalid_csv", "I couldn't find columns - is this a comma-separated file?")
    try:
        grid = [r for r in csv.reader(io.StringIO(text), delimiter=delim)]
    except csv.Error as e:
        raise IngestError(422, "invalid_csv", f"Couldn't read the CSV: {e}") from e
    grid = [r for r in grid if any(c.strip() for c in r)]
    if len(grid) < 2:
        raise IngestError(422, "invalid_csv", "The file has a header but no data rows.")
    if len(grid) - 1 > MAX_ROWS:
        raise IngestError(413, "too_many_rows", f"Too many rows (limit {MAX_ROWS:,}).")
    headers, seen = [], {}
    for i, h in enumerate(grid[0]):
        h = h.strip() or f"column_{i + 1}"
        seen[h] = seen.get(h, 0) + 1
        headers.append(h if seen[h] == 1 else f"{h}_{seen[h]}")
    if len(headers) > MAX_COLS:
        raise IngestError(413, "too_many_columns", f"Too many columns (limit {MAX_COLS}).")
    rows = [dict(zip(headers, [c.strip() for c in r] + [""] * (len(headers) - len(r)))) for r in grid[1:]]
    return headers, rows


# ------------------------------------------------------------ column roles
def _norm(h):
    return re.sub(r"[^a-z0-9]+", " ", h.lower()).strip()


def _score(header, alias):
    h = _norm(header)
    if h == alias:
        return 3
    if set(alias.split()) <= set(h.split()):
        return 2
    if len(h) >= 5 and difflib.SequenceMatcher(None, h, alias).ratio() >= 0.85:
        return 1
    return 0


def auto_map(headers, rows):
    cands = []
    for ri, role in enumerate(ROLES):
        for h in headers:
            best = max(((_score(h, a) - k * 0.001) for k, a in enumerate(ALIASES[role])), default=0)
            if best > 0.5:
                cands.append((-best, ri, role, h))
    mapping, used = {}, set()
    for _, _, role, h in sorted(cands):
        if role not in mapping and h not in used:
            mapping[role] = h
            used.add(h)
    if "date" not in mapping:                       # fall back to looking at the values
        for h in headers:
            if h not in used and detect_date_format([r[h] for r in rows])[0]:
                mapping["date"] = h
                break
    return mapping


# -------------------------------------------------------------- dates/numbers
def _parses(v, fmt):
    try:
        datetime.strptime(v, fmt)
        return True
    except ValueError:
        return False


def detect_date_format(values, day_first=True, min_frac=0.9):
    """-> (format or None, ambiguous_day_month)."""
    vals = [v.strip() for v in values if v and v.strip()]
    if not vals:
        return None, False
    sample = vals[::max(1, len(vals) // 500)][:500]
    scores = [(sum(_parses(v, f) for v in sample) / len(sample), f) for f in DATE_FORMATS]
    best = max(s for s, _ in scores)
    if best < min_frac:
        return None, False
    top = [f for s, f in scores if s == best]
    dm = [f for f in top if f[:2] in ("%d", "%m")]
    if len(dm) > 1:
        want = "%d" if day_first else "%m"
        return next((f for f in dm if f.startswith(want)), dm[0]), True
    return top[0], False


_CURRENCY = re.compile(r"[₹$€£¥\s]|\b(rs|inr|usd|eur|gbp)\b\.?", re.I)


def clean_number(s):
    t = _CURRENCY.sub("", (s or "").strip())
    if t.startswith("(") and t.endswith(")"):      # accounting negative
        t = "-" + t[1:-1]
    return t


def _to_float(s):
    try:
        return float(clean_number(s).replace(",", ""))
    except ValueError:
        return None


def _numeric_fraction(values):
    vals = [v for v in values if v and v.strip()]
    return 1.0 if not vals else sum(_to_float(v) is not None for v in vals) / len(vals)


# ---------------------------------------------------------------- main entry
def prepare(headers, rows, mapping=None, options=None):
    """-> (canonical_rows, config, loader_opts, warnings). Raises IngestError."""
    options = options or {}
    suggested = auto_map(headers, rows)
    m = {k: v for k, v in (mapping if mapping is not None else suggested).items() if v}
    for role, col in m.items():
        if role not in ROLES:
            raise IngestError(400, "invalid_mapping", f"Unknown field '{role}'.")
        if col not in headers:
            raise IngestError(400, "invalid_mapping", f"Column '{col}' was not found in the file.")
    if len(set(m.values())) != len(m):
        raise IngestError(400, "invalid_mapping", "Each column can be used for only one field.")

    def need(reason, message, **extra):
        raise IngestError(422, "needs_mapping", message, reason=reason, columns=headers,
                          suggested=m, sample_rows=rows[:5], roles=ROLE_LABEL, **extra)

    warnings = []
    if "date" not in m:
        need("date", "I couldn't tell which column holds the order date. Please pick it.")
    fmt, ambiguous = detect_date_format([r[m["date"]] for r in rows], options.get("day_first", True), 0.5)
    if fmt is None:
        need("date", f"Column '{m['date']}' doesn't look like dates (try a format like 2026-03-15 or 15/03/2026).")
    if ambiguous:
        warnings.append(f"Dates could be day-first or month-first; I assumed "
                        f"{'DD/MM' if options.get('day_first', True) else 'MM/DD'}. "
                        "If that's wrong, reload with the other option.")
    has_amount = "amount" in m
    if not has_amount and not ("quantity" in m and "unit_price" in m):
        need("amount", "Which column holds revenue? Pick a revenue/amount column "
                       "(or both quantity and unit price).")
    for role in ("amount", "quantity", "unit_price", "discount"):
        if role in m and _numeric_fraction([r[m[role]] for r in rows]) < 0.5:
            need(role, f"Column '{m[role]}' is mostly not numeric, so it can't be used as {ROLE_LABEL[role]}.")

    # ---- status
    completed = {v.lower() for v in options.get("completed_values") or []}
    status_note = DEFAULT_STATUS_NOTE = ("Only valid orders with status 'Completed' are counted; returned, "
                                         "cancelled and invalid rows are excluded (listed under 'excluded').")
    if "status" in m:
        distinct = sorted({r[m["status"]].strip() for r in rows if r[m["status"]].strip()})
        if not completed:
            completed = {v.lower() for v in distinct if v.lower() in POSITIVE}
        if not completed:
            need("status", f"I couldn't tell which '{m['status']}' values count as real sales. "
                           "Choose the completed ones.", status_values=distinct[:50])
        excluded_vals = [v for v in distinct if v.lower() not in completed]
        status_note = (f"Counted statuses: {', '.join(sorted(completed))}. "
                       + (f"Not counted: {', '.join(excluded_vals[:10])}. " if excluded_vals else "")
                       + "Invalid rows are excluded (listed under 'excluded').")
    else:
        status_note = "No status column, so every valid row is counted as a sale."

    # ---- ids: repeated order ids are normal (one order, many lines), so don't dedupe on them
    use_id = False
    if "id" in m:
        ids = [r[m["id"]] for r in rows]
        if len(set(ids)) == len(ids) and all(ids):
            use_id = True
        else:
            warnings.append(f"'{m['id']}' repeats or has blanks (e.g. several lines per order), so every row "
                            "is treated as its own transaction.")

    canon = []
    for i, r in enumerate(rows, start=1):
        out = {c: "" for c in REQUIRED_COLUMNS}
        out["Transaction_Id"] = r[m["id"]] if use_id else f"ROW{i:06d}"
        raw_date = r[m["date"]].strip()
        try:
            out["Order_date"] = datetime.strptime(raw_date, fmt).date().isoformat()
        except ValueError:
            out["Order_date"] = raw_date            # loader will reject it visibly
        for role, col in (("product", "Product"), ("category", "Category"), ("region", "Region"),
                          ("channel", "Sales_channel")):
            out[col] = r[m[role]] if role in m else ""
        qty = r[m["quantity"]] if "quantity" in m else ""
        if has_amount:
            amt, q = _to_float(r[m["amount"]]), _to_float(qty)
            out["Quantity"] = clean_number(qty) if "quantity" in m else "1"
            out["Unit_price"] = (repr(amt / q) if amt is not None and q and q > 0 and "quantity" in m
                                 else clean_number(r[m["amount"]]))
            out["Discount_amount"] = "0"
        else:
            out["Quantity"] = clean_number(qty)
            out["Unit_price"] = clean_number(r[m["unit_price"]])
            out["Discount_amount"] = clean_number(r[m["discount"]]) if "discount" in m else "0"
        if "status" in m:
            s = r[m["status"]].strip()
            out["Status"] = "Completed" if s.lower() in completed else s
        else:
            out["Status"] = "Completed"
        canon.append(out)

    if has_amount:
        revdef = f"Revenue = the '{m['amount']}' column, taken as the net value of each row."
    else:
        revdef = (f"Revenue = '{m['quantity']}' x '{m['unit_price']}'"
                  + (f" - '{m['discount']}'" if "discount" in m else "")
                  + " (discount treated as a per-line amount).")
    config = {"labels": {s: m[s] for s in ("product", "category", "region") if s in m},
              "has_units": "quantity" in m, "revenue_definition": revdef, "status_note": status_note,
              "mapping": m, "warnings": warnings, "date_format": fmt}
    opts = {"strict": False, "mapped_dims": {s for s in ("product", "category", "region") if s in m},
            "price_label": "amount" if has_amount else "unit price", "allow_zero_price": has_amount}
    return canon, config, opts, warnings
