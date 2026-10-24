"""FastAPI entry point.  Run:  uvicorn app.main:app --reload"""
import json
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import datasets, ingest, service
from .db import DataError, connect, load_csv, run_select
from .interpreter import interpret

ROOT = Path(__file__).resolve().parent.parent


class AskRequest(BaseModel):
    question: str


def create_app(db_path=None, csv_path=None, interpreter=interpret, upload_dir=None):
    db_path = str(db_path or os.getenv("BI_DB_PATH", ROOT / "data" / "sales.db"))
    csv_path = str(csv_path or os.getenv("BI_CSV_PATH", ROOT / "data" / "sales.csv"))
    upload_dir = str(upload_dir or os.getenv("BI_UPLOAD_DIR", ROOT / "data" / "uploads"))
    app = FastAPI(title="Mini AI BI Assistant")
    startup = {"error": None}
    try:
        if not os.path.exists(db_path):
            load_csv(csv_path, db_path)
    except DataError as e:
        startup["error"] = str(e)

    @app.exception_handler(RequestValidationError)
    async def bad_request(req: Request, exc):
        msg = ("Upload a CSV file in the form field 'file'." if req.url.path == "/datasets"
               else 'Send JSON like {"question": "..."} where question is text.')
        return JSONResponse({"status": "error", "code": "invalid_request", "message": msg}, 400)

    def answer(question, path):
        try:
            return service.ask(question, path, interpreter)
        except service.AppError as e:
            return JSONResponse({"status": "error", "code": e.code, "message": e.message, **e.extra}, e.status)
        except Exception:  # last-resort guard: never leak a stack trace
            return JSONResponse({"status": "error", "code": "internal_error",
                                 "message": "Something went wrong answering that."}, 500)

    def dataset_path(dataset_id):
        try:
            return datasets.db_path(upload_dir, dataset_id), None
        except DataError as e:
            return None, JSONResponse({"status": "error", "code": "dataset_not_found", "message": str(e)}, 404)

    @app.post("/datasets")
    def upload(file: UploadFile = File(...), mapping: str = Form(None), day_first: bool = Form(True),
               completed_values: str = Form(None)):
        try:
            data = file.file.read(ingest.MAX_BYTES + 1)
            try:
                mp = json.loads(mapping) if mapping else None
                cv = json.loads(completed_values) if completed_values else None
                if (mp is not None and not isinstance(mp, dict)) or (cv is not None and not isinstance(cv, list)):
                    raise ValueError
            except ValueError:
                raise ingest.IngestError(400, "invalid_mapping", "mapping must be a JSON object and "
                                         "completed_values a JSON list.")
            return datasets.create(upload_dir, file.filename, data, mp,
                                   {"day_first": day_first, "completed_values": cv})
        except ingest.IngestError as e:
            return JSONResponse({"status": "error", "code": e.code, "message": e.message, **e.extra}, e.status)
        except Exception:
            return JSONResponse({"status": "error", "code": "internal_error",
                                 "message": "Couldn't process that file."}, 500)

    @app.get("/datasets/{dataset_id}")
    def dataset_info(dataset_id: str):
        path, bad = dataset_path(dataset_id)
        return bad or datasets.profile(path)

    @app.post("/datasets/{dataset_id}/ask")
    def dataset_ask(dataset_id: str, req: AskRequest):
        path, bad = dataset_path(dataset_id)
        return bad or answer(req.question, path)

    @app.get("/datasets/{dataset_id}/data-quality")
    def dataset_quality(dataset_id: str):
        path, bad = dataset_path(dataset_id)
        if bad:
            return bad
        conn = connect(path)
        try:
            from . import queries
            return {**json.loads(run_select(conn, "SELECT value FROM meta WHERE key='load_report'")[0]["value"]),
                    "excluded": queries.excluded_rows(conn)}
        finally:
            conn.close()

    @app.delete("/datasets/{dataset_id}")
    def dataset_delete(dataset_id: str):
        path, bad = dataset_path(dataset_id)
        if bad:
            return bad
        datasets.delete(upload_dir, dataset_id)
        return {"status": "ok", "deleted": dataset_id}

    @app.get("/health")
    def health():
        return {"status": "ok" if not startup["error"] else "degraded", "error": startup["error"]}

    @app.get("/data-quality")
    def data_quality():
        try:
            conn = connect(db_path)
            rep = run_select(conn, "SELECT value FROM meta WHERE key='load_report'")
            conn.close()
            return json.loads(rep[0]["value"])
        except (DataError, IndexError) as e:
            return JSONResponse({"status": "error", "code": "data_error", "message": str(e)}, 500)

    @app.post("/ask")
    def ask(req: AskRequest):
        return answer(req.question, db_path)

    return app


app = create_app()
