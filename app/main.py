"""FastAPI entry point.  Run:  uvicorn app.main:app --reload"""
import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import service
from .db import DataError, connect, load_csv, run_select
from .interpreter import interpret

ROOT = Path(__file__).resolve().parent.parent


class AskRequest(BaseModel):
    question: str


def create_app(db_path=None, csv_path=None, interpreter=interpret):
    db_path = str(db_path or os.getenv("BI_DB_PATH", ROOT / "data" / "sales.db"))
    csv_path = str(csv_path or os.getenv("BI_CSV_PATH", ROOT / "data" / "sales.csv"))
    app = FastAPI(title="Mini AI BI Assistant")
    startup = {"error": None}
    try:
        if not os.path.exists(db_path):
            load_csv(csv_path, db_path)
    except DataError as e:
        startup["error"] = str(e)

    @app.exception_handler(RequestValidationError)
    async def bad_request(_: Request, exc):
        return JSONResponse({"status": "error", "code": "invalid_request",
                             "message": 'Send JSON like {"question": "..."} where question is text.'}, 400)

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
        try:
            return service.ask(req.question, db_path, interpreter)
        except service.AppError as e:
            return JSONResponse({"status": "error", "code": e.code, "message": e.message, **e.extra}, e.status)
        except Exception:  # last-resort guard: never leak a stack trace
            return JSONResponse({"status": "error", "code": "internal_error",
                                 "message": "Something went wrong answering that."}, 500)

    return app


app = create_app()
