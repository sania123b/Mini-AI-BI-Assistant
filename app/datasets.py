"""Per-upload datasets: each upload gets its own private SQLite file named by a random id."""
import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import ingest, queries
from .db import DataError, connect, load_rows, run_select

ID_RE = re.compile(r"^[0-9a-f]{32}$")
TTL_SECONDS = int(float(os.getenv("BI_UPLOAD_TTL_HOURS", "24")) * 3600)


def db_path(upload_dir, dataset_id):
    if not ID_RE.match(dataset_id or ""):          # blocks path traversal like ../../x
        raise DataError("Unknown dataset.")
    p = Path(upload_dir) / f"{dataset_id}.db"
    if not p.exists():
        raise DataError("Unknown or expired dataset. Please upload the file again.")
    return str(p)


def cleanup(upload_dir, ttl=TTL_SECONDS):
    for p in Path(upload_dir).glob("*.db*"):
        try:
            if time.time() - p.stat().st_mtime > ttl:
                p.unlink()
        except OSError:
            pass


def delete(upload_dir, dataset_id):
    Path(db_path(upload_dir, dataset_id)).unlink()


def profile(path):
    conn = connect(path)
    try:
        cfg = queries.get_config(conn)
        ents = queries.distinct_entities(conn)
        rep = json.loads(run_select(conn, "SELECT value FROM meta WHERE key='load_report'")[0]["value"])
    finally:
        conn.close()
    months = ents["months"]
    labels = cfg["labels"]
    ex = []
    ex.append("What is the total revenue?")
    for slot in ("product", "region", "category"):
        if slot in labels:
            ex.append(f"Which {labels[slot].lower()} generated the highest revenue?")
    ex.append("What is the monthly revenue trend?")
    if len(months) >= 2:
        a, b = months[0], months[1]
        nm = lambda m: datetime.strptime(m + "-01", "%Y-%m-%d").strftime("%B %Y")
        ex.append(f"How did revenue change between {nm(a)} and {nm(b)}?")
    return {"source": cfg.get("source"), "rows_read": rep["rows_read"], "valid_rows": rep["valid_rows"],
            "rejected_rows": rep["rejected_rows"], "rejection_reasons": rep["rejection_reasons"],
            "date_range": {"from": months[0], "to": months[-1]} if months else None,
            "labels": labels, "has_units": cfg["has_units"], "mapping": cfg["mapping"],
            "assumptions": [cfg["revenue_definition"], cfg["status_note"]],
            "warnings": cfg["warnings"], "suggested_questions": ex}


def create(upload_dir, filename, data, mapping=None, options=None):
    """Parse + map + load an uploaded CSV. Returns the profile plus the new dataset id."""
    name = os.path.basename(filename or "")
    if not name.lower().endswith((".csv", ".tsv", ".txt")):
        raise ingest.IngestError(400, "invalid_file", "Please upload a .csv file.")
    Path(upload_dir).mkdir(parents=True, exist_ok=True)
    cleanup(upload_dir)
    headers, rows = ingest.parse_csv_bytes(data)
    canon, config, opts, warnings = ingest.prepare(headers, rows, mapping, options)
    config["source"] = name[:100]
    dataset_id = uuid.uuid4().hex
    path = Path(upload_dir) / f"{dataset_id}.db"
    try:
        report = load_rows(canon, path, config, opts)
    except Exception as e:
        path.unlink(missing_ok=True)
        raise ingest.IngestError(500, "load_failed", f"Couldn't load that file: {e}") from e
    if report["valid_rows"] == 0:
        path.unlink(missing_ok=True)
        reasons = ", ".join(f"{k} ({v})" for k, v in report["rejection_reasons"].items())
        raise ingest.IngestError(422, "no_valid_rows",
                                 f"None of the {report['rows_read']} rows were usable. Reasons: {reasons}.",
                                 rejection_reasons=report["rejection_reasons"])
    return {"status": "ok", "dataset_id": dataset_id, **profile(str(path))}
