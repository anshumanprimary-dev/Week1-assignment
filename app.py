"""Run with: uv run streamlit run app.py"""
from dataclasses import asdict
from datetime import date
from pathlib import Path
import hashlib
import os

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from week1_assignment.ledger import parse_csv, analyze
from week1_assignment.market import fetch_history, value_holdings, historical_values
from week1_assignment.assistant import answer, error_message, IRS_URL

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
st.set_page_config(page_title="Portfolio Lens", page_icon="📈", layout="wide")
st.title("Portfolio Lens")
st.caption("Your stocks, transactions and the story behind the numbers · USD · FIFO · Taxable account")
tabs = st.tabs(["1 · Import portfolio", "2 · Portfolio overview", "3 · Historical performance", "4 · Ask your portfolio"])


@st.cache_data(ttl=900, show_spinner=False)
def cached_prices(tickers, start, end):
    return fetch_history(tickers, start, end)


def money(value):
    return f"${value:,.2f}" if value is not None else "Unavailable"


with tabs[0]:
    st.subheader("Start with your complete transaction history")
    st.write("Upload a CSV with ticker, date, transaction_type, quantity and price. Dates use YYYY-MM-DD; transactions are BUY or SELL.")
    uploaded = st.file_uploader("Transaction CSV", type="csv")
    sample = st.button("Load sample portfolio")
    content = uploaded.getvalue() if uploaded else None
    if sample:
        content = (ROOT / "Week1 Sample Data - Sheet1.csv").read_bytes()
    if content:
        digest = hashlib.sha256(content).hexdigest()
        if digest != st.session_state.get("digest"):
            try:
                trades, warnings = parse_csv(content)
                st.session_state.update(trades=trades, warnings=warnings, digest=digest,
                                        histories={}, price_errors={}, messages=[])
                for key in ("coverage_confirmed", "coverage_date", "pending_question"):
                    st.session_state.pop(key, None)
            except (ValueError, UnicodeDecodeError) as exc:
                st.error(f"Upload rejected: {exc}. Previous valid dataset remains active.")
    if "trades" not in st.session_state:
        st.info("Upload your CSV or load the supplied sample to begin.")
        st.stop()
    trades = st.session_state.trades
    for warning in st.session_state.warnings:
        st.warning(warning)
    st.success(f"{len(trades)} transactions loaded. Each valid import replaces the active dataset.")
    st.dataframe(pd.DataFrame([asdict(t) for t in trades]), hide_index=True)
    last = max(t.date for t in trades)
    confirmed = st.checkbox("I confirm this file contains my complete account transaction history through the date below", key="coverage_confirmed")
    through = st.date_input("History complete through", value=last, min_value=last, max_value=date.today(), key="coverage_date")
    st.caption("Completeness covers this uploaded account only. Dates without trades still count toward the wash-sale window.")

result = analyze(trades, through if confirmed else None)
tickers = tuple(sorted({t.ticker for t in trades}))
with st.sidebar:
    st.header("Market data")
    st.caption("Yahoo Finance daily closes; prices may be delayed. Nothing is fetched until you request it.")
    if st.button("Fetch / refresh prices", type="primary"):
        cached_prices.clear()
        with st.spinner("Fetching price histories…"):
            histories, errors = cached_prices(tickers, min(t.date for t in trades), date.today())
        st.session_state.histories = histories
        st.session_state.price_errors = errors
    for ticker, error in st.session_state.price_errors.items():
        st.warning(f"{ticker}: {error}")
    st.caption("Holdings exclude cash, fees and dividends. Transactions are assumed to be split-consistent; detected splits block valuation.")
histories = st.session_state.histories
valued, total = value_holdings(result["holdings"], histories)
economic_realized = sum(s["economic_gain"] for s in result["sales"])
with tabs[1]:
    st.subheader("Your current stock holdings")
    a, b, c = st.columns(3)
    a.metric("Holdings value", money(total if valued else 0))
    b.metric("Unrealized gain / loss", money(sum(h["unrealized_gain"] for h in valued) if total is not None else (0 if not valued else None)))
    c.metric("Realized economic gain / loss", money(economic_realized))
    st.metric("Combined economic gain / loss", money(economic_realized + sum(h["unrealized_gain"] for h in valued) if total is not None else (economic_realized if not valued else None)))
    if valued:
        frame = pd.DataFrame(valued)
        st.dataframe(frame, hide_index=True)
        if total is not None:
            st.bar_chart(frame.set_index("ticker")[["allocation_pct"]], y_label="Allocation (%)")
        else:
            st.info("Fetch prices to calculate complete valuation and allocations. Missing quotes are not treated as zero.")
    else:
        st.info("There are no remaining stock holdings.")
    st.caption("Price dates are shown per ticker. Returns exclude dividends and fees; no account-level percentage return is inferred.")

with tabs[2]:
    st.subheader("Review purchases, sales and performance")
    dates = st.date_input("Period", value=(min(t.date for t in trades), last), min_value=min(t.date for t in trades), max_value=date.today())
    if len(dates) == 2:
        start, end = dates
        period = [t for t in trades if start <= t.date <= end]
        sales = [s for s in result["sales"] if str(start) <= s["date"] <= str(end)]
        a, b, c = st.columns(3)
        a.metric("Purchase spending", money(sum(float(t.quantity*t.price) for t in period if t.side == "BUY")))
        b.metric("Sale proceeds", money(sum(s["proceeds"] for s in sales)))
        c.metric("Realized economic gain / loss", money(sum(s["economic_gain"] for s in sales)))
        st.metric("Estimated tax-adjusted realized gain / loss", money(sum(s["tax_adjusted_gain"] for s in sales)))
        if period:
            monthly = pd.DataFrame([dict(month=t.date.strftime("%Y-%m"), purchases=float(t.quantity*t.price) if t.side == "BUY" else 0,
                                         sale_proceeds=float(t.quantity*t.price) if t.side == "SELL" else 0) for t in period]).groupby("month").sum()
            st.bar_chart(monthly)
        if histories:
            history = historical_values(trades, histories, date.today()).loc[str(start):str(end)]
            st.write("Holdings value over time")
            st.line_chart(history)
            st.caption("End-of-day shares × daily close; closed-market dates carry the last available close. This is holdings value, not an investment-return chart.")
            if history.isna().any():
                st.warning("Some dates cannot be valued because price history is missing.")
        st.write("Sale results")
        st.dataframe(pd.DataFrame(sales), hide_index=True)
        with st.expander("FIFO disposal details"):
            st.dataframe(pd.DataFrame([r for r in result["lot_disposals"] if str(start) <= r["sale_date"] <= str(end)]), hide_index=True)
        st.subheader("Wash-sale review")
        washes = [w for w in result["wash_sales"] if str(start) <= w["sale_date"] <= str(end)]
        st.dataframe(pd.DataFrame(washes), hide_index=True)
        st.caption(f"Same-ticker, single-account screening only. Other accounts, spouse trades, IRAs and different substantially identical securities are outside this file. Reference: {IRS_URL}")
        st.info("Tax figures are screening estimates, not filing records. No long/short-term classification is produced. Unconfirmed coverage can change later tax results.")
        if washes and st.button("Explain these wash-sale findings"):
            st.session_state.pending_question = "Explain the wash-sale findings for " + str(start) + " through " + str(end)
            st.success("Open the Ask your portfolio tab to read the explanation.")

evidence = {**result, "valued_holdings": valued, "total_market_value": total,
            "transactions": [{**asdict(t), "date": str(t.date), "quantity": float(t.quantity), "price": float(t.price)} for t in trades],
            "history_complete_through": str(through) if confirmed else None,
            "assumptions": "USD; FIFO; one US taxable account; stocks only; fees and dividends absent",
            "price_errors": st.session_state.price_errors}
with tabs[3]:
    st.subheader("Ask questions grounded in your portfolio")
    st.caption("Try: Which stock has the largest unrealized gain? · Explain my AAPL wash sales · How much have I realized from MSFT?")
    model = st.text_input("OpenAI model", value=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
    enabled = st.checkbox("Send this portfolio’s transactions and calculated results to OpenAI when I ask a question")
    if not os.getenv("OPENAI_API_KEY"):
        st.info("Set OPENAI_API_KEY in the local .env file to enable answers. The other tabs work without it.")
    if st.button("Clear conversation"):
        st.session_state.messages = []
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    question = st.chat_input("Ask about your holdings, sales or wash-sale findings")
    pending = st.session_state.get("pending_question")
    if pending:
        st.write("Pending question:", pending)
        if st.button("Answer pending question"):
            question = pending
    if question:
        if not enabled or not os.getenv("OPENAI_API_KEY") or not model.strip():
            st.warning("Enable portfolio sharing, configure the API key and enter a model to request an answer.")
        else:
            try:
                with st.spinner("Explaining the calculated evidence…"):
                    reply = answer(question, evidence, model.strip(), st.session_state.messages)
                st.session_state.messages.extend([{"role": "user", "content": question}, {"role": "assistant", "content": reply}])
                st.session_state.pop("pending_question", None)
                st.rerun()
            except Exception as exc:
                st.error(error_message(exc))
    with st.expander("Inspect the evidence supplied to AI"):
        st.json(evidence)
