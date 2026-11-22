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
    model = st.text_input("Model", DEFAULT_MODEL)
    st.markdown("**Try:**\n- What was pickup on-time performance in 2025?\n"
                "- Complaint rate per 1,000 trips by year\n"
                "- Which borough had the worst on-time performance in 2024?")

if default_backend() == "api" and not os.getenv("ANTHROPIC_API_KEY"):
    st.warning("Add your Anthropic API key to a `.env` file (see `.env.example`), then restart.")
    st.stop()

question = st.chat_input("Ask a question about paratransit service")
if question:
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
                st.metric(df.columns[0], f"{df.iloc[0, 0]:,}" if isinstance(df.iloc[0, 0], (int, float)) else df.iloc[0, 0])
            else:
                st.dataframe(df, hide_index=True)
                num = df.select_dtypes("number").columns
                if len(df) > 1 and len(num) and len(df.columns) > len(num):
                    st.bar_chart(df, x=df.columns[0], y=list(num))
        if ans.notes:
            st.caption(ans.notes)
        with st.expander("SQL"):
            st.code(ans.sql or "(none)", language="sql")
            st.caption(f"{ans.attempts} attempt(s), {ans.input_tokens:,} input / {ans.output_tokens:,} output tokens")
