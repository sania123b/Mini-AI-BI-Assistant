"""Deterministic, parameterised SQL for each supported intent.
Only whitelisted identifiers are ever interpolated; all values are bound parameters."""
from .db import run_select

BASE = "is_valid = 1 AND status = 'Completed'"
DIMS = {"product", "region", "category"}
METRIC_COL = {"revenue": "revenue", "units": "units"}   # output aliases to ORDER BY


def _where(filters, skip=()):
    clauses, params = [BASE], []
    for key in ("product", "region", "category", "month"):
        if filters.get(key) and key not in skip:
            clauses.append(f"{key} = ?")
            params.append(filters[key])
    if filters.get("months"):
        clauses.append(f"month IN ({','.join('?' * len(filters['months']))})")
        params.extend(filters["months"])
    return " AND ".join(clauses), params


def total(conn, filters):
    where, params = _where(filters)
    sql = ("SELECT ROUND(COALESCE(SUM(revenue),0),2) AS revenue, "
           "COALESCE(SUM(quantity),0) AS units, COUNT(*) AS orders "
           f"FROM sales_clean WHERE {where}")
    return sql, params, run_select(conn, sql, params)


def top(conn, dim, metric, order, filters):
    assert dim in DIMS and metric in METRIC_COL and order in ("asc", "desc")
    where, params = _where(filters, skip=(dim,))
    sql = (f"SELECT {dim} AS name, ROUND(SUM(revenue),2) AS revenue, SUM(quantity) AS units, "
           f"COUNT(*) AS orders FROM sales_clean WHERE {where} "
           f"GROUP BY {dim} ORDER BY {METRIC_COL[metric]} {order.upper()}, name")
    return sql, params, run_select(conn, sql, params)


def by_month(conn, filters):
    where, params = _where(filters)
    sql = ("SELECT month, ROUND(SUM(revenue),2) AS revenue, SUM(quantity) AS units, "
           f"COUNT(*) AS orders FROM sales_clean WHERE {where} GROUP BY month ORDER BY month")
    return sql, params, run_select(conn, sql, params)


def distinct_entities(conn):
    out = {}
    for dim in sorted(DIMS):
        rows = run_select(conn, f"SELECT DISTINCT {dim} AS v FROM sales_clean "
                                f"WHERE is_valid = 1 ORDER BY v")
        out[dim] = [r["v"] for r in rows]
    out["months"] = [r["month"] for r in run_select(
        conn, "SELECT DISTINCT month FROM sales_clean WHERE is_valid = 1 ORDER BY month")]
    return out


def excluded_rows(conn):
    invalid = run_select(conn, "SELECT transaction_id, reject_reason AS reason FROM sales_clean "
                               "WHERE is_valid = 0 ORDER BY row_num")
    other = run_select(conn, "SELECT transaction_id, status AS reason FROM sales_clean "
                             "WHERE is_valid = 1 AND status != 'Completed' ORDER BY row_num")
    return {"invalid_rows": invalid, "non_completed_rows": other}
