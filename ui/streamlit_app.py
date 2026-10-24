"""Web UI.  Run:  streamlit run ui/streamlit_app.py   (the API must be running)"""
import json
import os

import pandas as pd
import requests
import streamlit as st

API = os.getenv("BI_API_URL", "http://127.0.0.1:8000")
SAMPLE_QUESTIONS = ["What is the total revenue?", "Which product generated the highest revenue?",
                    "What is the monthly revenue trend?", "Which region generated the highest sales?",
                    "How did revenue change between February and March?"]

st.set_page_config(page_title="Mini AI BI Assistant", page_icon="📊", layout="wide")
st.title("📊 Mini AI BI Assistant")
st.caption("Ask questions about sales data in plain English. Every answer is calculated by a database query "
           "and shown with the figures behind it.")
ss = st.session_state
ss.setdefault("dataset", None)        # {"id", "profile"} for an uploaded file
ss.setdefault("pending", None)        # file waiting for column mapping
ss.setdefault("q", "")


def api(method, path, **kw):
    try:
        return requests.request(method, f"{API}{path}", timeout=60, **kw)
    except requests.RequestException:
        st.error(f"Can't reach the API at {API}. Start it with: uvicorn app.main:app")
        st.stop()


def send_upload(name, data, mapping=None, completed=None, day_first=True):
    form = {"day_first": str(day_first).lower()}
    if mapping is not None:
        form["mapping"] = json.dumps(mapping)
    if completed:
        form["completed_values"] = json.dumps(completed)
    return api("POST", "/datasets", files={"file": (name, data, "text/csv")}, data=form)


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Data")
    source = st.radio("Use", ["Sample sales data", "Upload my own CSV"],
                      index=1 if ss.dataset or ss.pending else 0)
    day_first = True
    if source == "Upload my own CSV":
        up = st.file_uploader("CSV file (max 5 MB)", type=["csv", "tsv", "txt"])
        day_first = st.radio("If dates look like 01/02/2026", ["Day first (DD/MM)", "Month first (MM/DD)"]) \
            .startswith("Day")
        if up is not None and st.button("Load file", type="primary"):
            data = up.getvalue()
            r = send_upload(up.name, data, day_first=day_first)
            body = r.json()
            if r.status_code == 200:
                ss.dataset, ss.pending = {"id": body["dataset_id"], "profile": body}, None
            elif body.get("code") == "needs_mapping":
                ss.pending, ss.dataset = {"name": up.name, "data": data, "body": body}, None
            else:
                ss.pending = None
                st.error(body.get("message", "Upload failed."))
        if ss.dataset and st.button("Remove my data from the server"):
            api("DELETE", f"/datasets/{ss.dataset['id']}")
            ss.dataset = None
            st.rerun()
        st.caption("Your file is stored privately under a random ID and deleted automatically after 24 hours. "
                   "Don't upload confidential data to a shared deployment.")
    else:
        ss.dataset = None

# ------------------------------------------------- column mapping screen
if source == "Upload my own CSV" and ss.pending:
    body = ss.pending["body"]
    st.warning(body["message"])
    cols = ["(none)"] + body["columns"]
    st.write("Tell me which column is which (only the date and a revenue/amount column are required):")
    st.dataframe(pd.DataFrame(body["sample_rows"]), hide_index=True)
    chosen = {}
    grid = st.columns(3)
    for i, (role, label) in enumerate(body["roles"].items()):
        default = body["suggested"].get(role)
        chosen[role] = grid[i % 3].selectbox(label, cols, index=cols.index(default) if default in cols else 0,
                                             key=f"map_{role}")
    completed = None
    if body.get("status_values"):
        completed = st.multiselect("Which statuses count as real, completed sales?", body["status_values"])
    if st.button("Load with this mapping", type="primary"):
        mp = {k: v for k, v in chosen.items() if v != "(none)"}
        r = send_upload(ss.pending["name"], ss.pending["data"], mp, completed, day_first)
        b = r.json()
        if r.status_code == 200:
            ss.dataset, ss.pending = {"id": b["dataset_id"], "profile": b}, None
            st.rerun()
        else:
            ss.pending["body"] = b if b.get("code") == "needs_mapping" else body
            st.error(b.get("message", "Couldn't load that file."))
    st.stop()

# ---------------------------------------------------------------- profile
examples = SAMPLE_QUESTIONS
ask_path = "/ask"
if source == "Upload my own CSV":
    if not ss.dataset:
        st.info("Upload a CSV in the sidebar. It needs at least a date column and a revenue/amount column "
                "(or quantity and unit price). I'll work out the rest.")
        st.stop()
    p = ss.dataset["profile"]
    examples, ask_path = p["suggested_questions"], f"/datasets/{ss.dataset['id']}/ask"
    st.success(f"Loaded **{p['source']}**: {p['valid_rows']} usable rows of {p['rows_read']}"
               + (f", {p['date_range']['from']} to {p['date_range']['to']}" if p["date_range"] else ""))
    for w in p["warnings"]:
        st.warning(w)
    with st.expander("How I understood your columns"):
        st.json(p["mapping"])
        for a in p["assumptions"]:
            st.markdown(f"- {a}")
        if p["rejected_rows"]:
            st.write("Rows I couldn't use:", p["rejection_reasons"])

# --------------------------------------------------------------- question
cols = st.columns(min(len(examples), 3))
for i, ex in enumerate(examples):
    if cols[i % len(cols)].button(ex, width="stretch", key=f"ex{i}"):
        ss.q = ex
question = st.text_input("Your question", key="q", max_chars=500)

if st.button("Ask", type="primary"):
    r = api("POST", ask_path, json={"question": question})
    body = r.json()
    if body.get("status") != "ok":
        st.warning(body.get("message", "Something went wrong."))
        for ex in body.get("examples", []):
            st.markdown(f"- {ex}")
        st.stop()
    st.success(body["answer"])
    for w in body.get("warnings", []):
        st.warning(w)
    df = pd.DataFrame(body["data"])
    st.subheader("Supporting data")
    st.dataframe(df, width="stretch", hide_index=True)
    metric = body["params"]["metric"]
    if body["intent"] == "monthly_trend" and len(df) > 1:
        st.line_chart(df.set_index("month")[metric])
    elif body["intent"].startswith("top_") and len(df) > 1:
        st.bar_chart(df.set_index("name")[metric])
    with st.expander("How this was calculated"):
        st.code(body["sql"], language="sql")
        st.write("Parameters:", body["sql_params"])
        st.write(f"Interpreted by: **{body['interpreted_by']}**  |  intent: `{body['intent']}`")
        for a in body["assumptions"]:
            st.markdown(f"- {a}")
    ex = body["excluded"]
    with st.expander(f"Excluded rows ({ex['invalid_total']} invalid, {ex['non_completed_total']} not counted)"):
        st.dataframe(pd.DataFrame(ex["invalid_rows"] + ex["non_completed_rows"]), hide_index=True)
