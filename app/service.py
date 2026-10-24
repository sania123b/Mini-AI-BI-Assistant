"""Orchestration: guardrails -> interpret -> validate -> deterministic query -> narrate.
The answer text is built from the returned rows only, so numbers can't be hallucinated."""
import re
from datetime import datetime

from . import queries
from .db import DataError, connect
from .interpreter import INTENTS, interpret, parse_month
from difflib import get_close_matches

MAX_LEN = 500
EXAMPLES = ["What is the total revenue?", "Which product generated the highest revenue?",
            "What is the monthly revenue trend?", "Which region generated the highest sales?",
            "How did revenue change between February and March?"]

_DESTRUCTIVE = re.compile(
    r"\b(drop|delete|truncate|alter|attach|pragma|grant|exec)\b|"
    r"\b(insert|update|create|modify|remove|erase|wipe)\b.{0,25}\b(table|record|row|data|database|sales)\b", re.I)
_SQLISH = re.compile(r"\bselect\b.+\bfrom\b|\bunion\b\s+select|;\s*\w|--", re.I | re.S)
_INJECTION = re.compile(r"ignore (all |the |your )?(previous|prior|above)|system prompt|"
                        r"reveal.{0,20}(instructions|prompt|key)|api[_ ]key", re.I)


class AppError(Exception):
    def __init__(self, status, code, message, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def fmt(x):
    x = float(x)
    return f"{x:,.0f}" if x.is_integer() else f"{x:,.2f}"


def label(month):
    return datetime.strptime(month + "-01", "%Y-%m-%d").strftime("%B %Y")


def check_question(q):
    if not isinstance(q, str) or not q.strip():
        raise AppError(400, "empty_question", "Please type a question.", examples=EXAMPLES)
    q = re.sub(r"[\x00-\x1f\x7f]", " ", q).strip()
    if len(q) > MAX_LEN:
        raise AppError(400, "question_too_long", f"Questions are limited to {MAX_LEN} characters.")
    if _DESTRUCTIVE.search(q):
        raise AppError(422, "destructive_request",
                       "This assistant is read-only; it can't change or delete data.", examples=EXAMPLES)
    if _SQLISH.search(q):
        raise AppError(422, "sql_not_accepted",
                       "Please ask in plain English; raw SQL is not accepted.", examples=EXAMPLES)
    if _INJECTION.search(q):
        raise AppError(422, "unsafe_request", "I can only answer questions about the sales data.",
                       examples=EXAMPLES)
    return q


def _resolve(kind, value, entities):
    """Map a requested value onto a real DB value (case-insensitive, typo tolerant)."""
    values = entities[kind]
    lower = {v.lower(): v for v in values}
    v = str(value).strip().lower()
    if v in lower:
        return lower[v], None
    close = get_close_matches(v, list(lower), n=1, cutoff=0.8)
    if close:
        return lower[close[0]], f"Interpreted {kind} '{value}' as '{lower[close[0]]}'."
    raise AppError(422, "unknown_entity", f"No {kind} called '{value}' exists in the data.",
                   available=values)


def validate_intent(spec, entities, config):
    """Never trust the interpreter (LLM or rules): re-validate everything."""
    if not isinstance(spec, dict) or spec.get("intent") not in INTENTS:
        raise AppError(422, "unsupported_question", "I couldn't map that to a supported analysis.",
                       examples=EXAMPLES)
    if spec["intent"] == "unsupported":
        raise AppError(422, "unsupported_question",
                       spec.get("reason") or "I can't answer that from the sales data.", examples=EXAMPLES)
    metric = spec.get("metric") if spec.get("metric") in ("revenue", "units") else "revenue"
    if metric == "units" and not config.get("has_units", True):
        raise AppError(422, "no_quantity", "This dataset has no quantity column, so I can't calculate "
                       "units. Ask about revenue instead.", examples=EXAMPLES)
    if spec["intent"].startswith("top_") and spec["intent"][4:] not in config["labels"]:
        dim = spec["intent"][4:]
        raise AppError(422, "no_such_column", f"This dataset has no {dim} column, so I can't break "
                       "results down that way.", examples=EXAMPLES)
    order = spec.get("order") if spec.get("order") in ("asc", "desc") else "desc"
    years = [int(m[:4]) for m in entities["months"]] or [2026]
    default_year, notes, assumed_year = max(years), [], False
    filters = {}
    for kind in ("product", "region", "category"):
        if spec.get("filters", {}).get(kind):
            filters[kind], note = _resolve(kind, spec["filters"][kind], entities)
            notes += [note] if note else []

    def month(token):
        nonlocal assumed_year
        try:
            m, assumed = parse_month(token, default_year)
        except ValueError as e:
            raise AppError(422, "invalid_month", str(e)) from e
        assumed_year |= assumed
        return m

    months = []
    if spec["intent"] == "month_comparison":
        months = [month(m) for m in spec.get("months") or []]
        if len(months) != 2:
            raise AppError(422, "need_two_months",
                           "Please name two months to compare, e.g. 'February to March'.")
        if months[0] == months[1]:
            raise AppError(422, "same_month", "Those are the same month; pick two different months.")
    elif spec.get("filters", {}).get("month"):
        filters["month"] = month(spec["filters"]["month"])
    if assumed_year:
        notes.append(f"No year given; assumed {default_year} (the latest year in the data).")
    return spec["intent"], metric, order, filters, months, notes


def _scope(filters, labels):
    parts = []
    if filters.get("month"):
        parts.append("in " + label(filters["month"]))
    dims = [f"{labels.get(k, k)} '{filters[k]}'" for k in ("product", "category", "region") if filters.get(k)]
    if dims:
        parts.append("for " + ", ".join(dims))
    return (" " + " ".join(parts)) if parts else ""


def run(intent, metric, order, filters, months, conn, labels, has_units):
    mname = "units sold" if metric == "units" else "revenue"
    if intent == "total_revenue":
        sql, params, rows = queries.total(conn, filters)
        r = rows[0]
        if r["orders"] == 0:
            return sql, params, rows, f"No completed sales found{_scope(filters, labels)}."
        extra = (f"revenue {fmt(r['revenue'])}" if metric == "units"
                 else f"{fmt(r['units'])} units" if has_units else "")
        extra = f" ({extra})" if extra else ""
        val = fmt(r["units"] if metric == "units" else r["revenue"])
        return sql, params, rows, (f"Total {mname}{_scope(filters, labels)} is {val} from {r['orders']} "
                                   f"completed orders{extra}.")
    if intent.startswith("top_"):
        dim = intent[4:]
        sql, params, rows = queries.top(conn, dim, metric, order, filters)
        if not rows:
            return sql, params, rows, f"No completed sales found{_scope(filters, labels)}."
        best = rows[0][metric]
        winners = [r["name"] for r in rows if r[metric] == best]
        word = "highest" if order == "desc" else "lowest"
        if len(winners) == 1:
            txt = f"{winners[0]} has the {word} {mname}{_scope(filters, labels)}: {fmt(best)}."
        else:
            txt = f"{' and '.join(winners)} are tied for the {word} {mname}{_scope(filters, labels)}: {fmt(best)} each."
        return sql, params, rows, txt
    if intent == "monthly_trend":
        sql, params, rows = queries.by_month(conn, filters)
        if not rows:
            return sql, params, rows, f"No completed sales found{_scope(filters, labels)}."
        prev = None
        for r in rows:
            cur = r[metric]
            r["change_vs_prev_month"] = None if prev is None else round(cur - prev, 2)
            r["change_pct"] = (None if prev in (None, 0) else round((cur - prev) / prev * 100, 1))
            prev = cur
        hi = max(rows, key=lambda r: r[metric])
        lo = min(rows, key=lambda r: r[metric])
        if hi[metric] == lo[metric] and len(rows) > 1:
            return sql, params, rows, (f"Monthly {mname}{_scope(filters, labels)} is the same every month "
                                       f"({fmt(hi[metric])}) from {label(rows[0]['month'])} to {label(rows[-1]['month'])}.")
        if len(rows) == 1:
            return sql, params, rows, (f"Only one month has data ({label(rows[0]['month'])}: "
                                       f"{fmt(rows[0][metric])}), so no trend can be computed.")
        return sql, params, rows, (
            f"Monthly {mname}{_scope(filters, labels)} from {label(rows[0]['month'])} to {label(rows[-1]['month'])}: "
            f"highest in {label(hi['month'])} ({fmt(hi[metric])}), lowest in {label(lo['month'])} "
            f"({fmt(lo[metric])}).")
    # month_comparison
    sql, params, rows = queries.by_month(conn, {**filters, "months": months})
    have = {r["month"]: r for r in rows}
    a, b = (have.get(m, {}).get(metric, 0) for m in months)
    table = [{"month": m, "revenue": have.get(m, {}).get("revenue", 0),
              "units": have.get(m, {}).get("units", 0), "orders": have.get(m, {}).get("orders", 0)}
             for m in months]
    if a == 0 and b == 0:
        return sql, params, table, f"No completed sales in either {label(months[0])} or {label(months[1])}."
    diff = round(b - a, 2)
    verb = "rose" if diff > 0 else "fell" if diff < 0 else "was unchanged"
    pct = "" if a == 0 else f" ({diff / a * 100:+.1f}%)"
    base = (f"{mname.capitalize()}{_scope(filters, labels)} {verb} from {fmt(a)} in {label(months[0])} to "
            f"{fmt(b)} in {label(months[1])}: {'+' if diff > 0 else '-' if diff < 0 else ''}{fmt(abs(diff))}{pct}.")
    if a == 0:
        base += f" There were no completed sales in {label(months[0])}, so a percentage change isn't defined."
    if b == 0:
        base += f" There were no completed sales in {label(months[1])}."
    table.append({"month": "change", "revenue": round(table[1]["revenue"] - table[0]["revenue"], 2),
                  "units": table[1]["units"] - table[0]["units"],
                  "orders": table[1]["orders"] - table[0]["orders"]})
    return sql, params, table, base


def ask(question, db_path, interpreter=interpret):
    question = check_question(question)
    try:
        conn = connect(db_path)
        try:
            config = queries.get_config(conn)
            entities = queries.distinct_entities(conn)
            entities["labels"] = config["labels"]
            spec, source, warnings = interpreter(question, entities)
            intent, metric, order, filters, months, notes = validate_intent(spec, entities, config)
            sql, params, rows, answer = run(intent, metric, order, filters, months, conn,
                                            config["labels"], config["has_units"])
            excluded = queries.excluded_rows(conn)
        finally:
            conn.close()
    except DataError as e:
        raise AppError(500, "data_error", str(e)) from e
    if spec.get("ignored"):
        warnings = warnings + ["Ignored words I did not recognise: " + ", ".join(spec["ignored"]) +
                               ". Check that the answer matches what you meant."]
    assumptions = [config["revenue_definition"], config["status_note"]] + notes
    warnings = warnings + list(config.get("warnings", []))
    if re.search(r"\bsales\b", question, re.I) and metric == "revenue":
        assumptions.append("'Sales' interpreted as revenue (ask for 'units' to get quantities).")
    return {"status": "ok", "answer": answer, "intent": intent, "interpreted_by": source,
            "params": {"metric": metric, "order": order, "filters": filters, "months": months},
            "data": rows, "sql": sql, "sql_params": params, "assumptions": assumptions,
            "warnings": warnings, "excluded": excluded}
