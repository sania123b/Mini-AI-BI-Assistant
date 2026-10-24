"""Simple UI.  Run:  streamlit run ui/streamlit_app.py   (API must be running)"""
import os

import pandas as pd
import requests
import streamlit as st

API = os.getenv("BI_API_URL", "http://127.0.0.1:8000")
EXAMPLES = ["What is the total revenue?", "Which product generated the highest revenue?",
            "What is the monthly revenue trend?", "Which region generated the highest sales?",
            "How did revenue change between February and March?"]

st.set_page_config(page_title="Mini AI BI Assistant", page_icon="📊")
st.title("📊 Mini AI BI Assistant")
st.caption("Ask a question about the sales data. Answers come from database queries, not guesses.")

if "q" not in st.session_state:
    st.session_state.q = ""
cols = st.columns(len(EXAMPLES))
for col, ex in zip(cols, EXAMPLES):
    if col.button(ex, use_container_width=True):
        st.session_state.q = ex
question = st.text_input("Your question", key="q", max_chars=500)

if st.button("Ask", type="primary") and question is not None:
    try:
        resp = requests.post(f"{API}/ask", json={"question": question}, timeout=40)
        body = resp.json()
    except requests.RequestException:
        st.error("Can't reach the API. Start it with: uvicorn app.main:app")
        st.stop()
    if body.get("status") != "ok":
        st.warning(body.get("message", "Something went wrong."))
        for ex in body.get("examples", []):
            st.markdown(f"- {ex}")
        st.stop()
    st.success(body["answer"])
    for w in body["warnings"]:
        st.warning(w)
    df = pd.DataFrame(body["data"])
    st.subheader("Supporting data")
    st.dataframe(df, use_container_width=True, hide_index=True)
    if body["intent"] == "monthly_trend" and len(df) > 1:
        st.line_chart(df.set_index("month")[body["params"]["metric"]])
    elif body["intent"].startswith("top_") and len(df) > 1:
        st.bar_chart(df.set_index("name")[body["params"]["metric"]])
    with st.expander("How this was calculated"):
        st.code(body["sql"], language="sql")
        st.write("Parameters:", body["sql_params"])
        st.write(f"Interpreted by: **{body['interpreted_by']}**  |  intent: `{body['intent']}`")
        for a in body["assumptions"]:
            st.markdown(f"- {a}")
    ex = body["excluded"]
    with st.expander(f"Excluded rows ({len(ex['invalid_rows'])} invalid, {len(ex['non_completed_rows'])} not completed)"):
        st.dataframe(pd.DataFrame(ex["invalid_rows"] + ex["non_completed_rows"]), hide_index=True)
