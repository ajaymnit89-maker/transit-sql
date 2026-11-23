"""TransitSQL: ask questions about NYC paratransit data in plain English.

Run:  uv run streamlit run app.py
"""

import os

import anthropic
import streamlit as st

from transit_sql.agent import DEFAULT_MODEL, ask, default_backend
from transit_sql.context import MODES
from transit_sql.data import connect

st.set_page_config(page_title="TransitSQL", page_icon="🚐", layout="wide")


@st.cache_resource
def get_con():
    return connect()


st.title("TransitSQL")
st.caption(
    "Ask about NYC Access-A-Ride (paratransit) trips, on-time performance, complaints, "
    "ride time and no-shows. Data: MTA open data on data.ny.gov, Oct 2016 to Sep 2026."
)

with st.sidebar:
    mode = st.selectbox("Context given to the model", MODES, index=MODES.index("semantic"),
                        help="How much the model knows about the data. 'semantic' adds metric definitions and business rules.")
    # A fixed list, not free text: the public demo spends the owner's API key.
    model = st.selectbox("Model", [DEFAULT_MODEL, "claude-sonnet-5-5"])
    st.markdown("**Try:**\n- What was pickup on-time performance in 2025?\n"
                "- Complaint rate per 1,000 trips by year\n"
                "- Which borough had the worst on-time performance in 2024?")

if default_backend() == "api" and not os.getenv("ANTHROPIC_API_KEY"):
    st.warning("Add your Anthropic API key to a `.env` file (see `.env.example`) or to the app's "
               "Streamlit secrets as ANTHROPIC_API_KEY, then restart.")
    st.stop()

# shortcut: per browser session only, a refresh resets it; the hard cap is the API key's spend limit.
MAX_QUESTIONS = 20
asked = st.session_state.setdefault("asked", 0)
question = st.chat_input("Ask a question about paratransit service", max_chars=300,
                         disabled=asked >= MAX_QUESTIONS)
if asked >= MAX_QUESTIONS:
    st.info(f"This demo allows {MAX_QUESTIONS} questions per session.")
if question:
    st.session_state.asked += 1
    st.chat_message("user").write(question)
    with st.chat_message("assistant"):
        with st.spinner("Writing SQL..."):
            try:
                ans = ask(question, get_con(), mode=mode, model=model)
            except (anthropic.APIError, RuntimeError) as e:
                st.error(f"Claude call failed: {e}")
                st.stop()

        if ans.error:
            st.error(ans.error)
        else:
            df = ans.df
            if df.shape == (1, 1):
                v = df.iloc[0, 0]
                if isinstance(v, float):  # DuckDB SUMs come back as floats: show 9,687,112 but keep 92.0 (a %)
                    v = int(v) if v.is_integer() and abs(v) >= 1000 else round(v, 2)
                st.metric(df.columns[0], f"{v:,}" if isinstance(v, (int, float)) else v)
            else:
                st.dataframe(df, hide_index=True)
                num = df.select_dtypes("number").columns
                if len(df) > 1 and len(num) and len(df.columns) > len(num):
                    st.bar_chart(df, x=df.columns[0], y=list(num))
        if ans.notes:
            st.caption(ans.notes)
        with st.expander("SQL"):
            st.code(ans.sql or "(none)", language="sql", wrap_lines=True)
            st.caption(f"{ans.attempts} attempt(s), {ans.input_tokens:,} input / {ans.output_tokens:,} output tokens")
