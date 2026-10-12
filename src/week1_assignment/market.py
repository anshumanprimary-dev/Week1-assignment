"""Yahoo daily closes; never substitute fabricated or transaction prices."""
from datetime import timedelta
import pandas as pd
import yfinance as yf


def fetch_history(tickers, start, end):
    histories, errors = {}, {}
    for ticker in tickers:
        try:
            security = yf.Ticker(ticker)
            frame = security.history(start=str(start), end=str(end + timedelta(days=1)),
                                     auto_adjust=False, actions=True, timeout=15)
            if frame.empty:
                raise ValueError("No prices returned")
            currency = security.history_metadata.get("currency")
            if currency != "USD":
                raise ValueError(f"Expected USD prices; provider currency is {currency or 'unknown'}")
            if "Stock Splits" in frame and frame["Stock Splits"].ne(0).any():
                raise ValueError("Stock split detected: corporate-action reconciliation is required")
            series = frame["Close"].dropna()
            series.index = pd.to_datetime(series.index.date)
            if series.empty or not series.gt(0).all():
                raise ValueError("Invalid price history")
            histories[ticker] = series
        except Exception as exc:
            errors[ticker] = str(exc)
    return histories, errors


def value_holdings(holdings, histories):
    rows = []
    for h in holdings:
        series = histories.get(h["ticker"])
        price = float(series.iloc[-1]) if series is not None and not series.empty else None
        value = price * h["quantity"] if price is not None else None
        rows.append({**h, "price": price, "price_date": str(series.index[-1].date()) if price is not None else None,
                     "market_value": value,
                     "unrealized_gain": value - h["economic_basis"] if value is not None else None})
    complete = bool(rows) and all(r["market_value"] is not None for r in rows)
    total = sum(r["market_value"] for r in rows) if complete else None
    for row in rows:
        row["allocation_pct"] = row["market_value"] / total * 100 if total else None
        row["unrealized_pct"] = row["unrealized_gain"] / row["economic_basis"] * 100 if row["unrealized_gain"] is not None else None
    return rows, total


def historical_values(trades, histories, end):
    days = pd.date_range(min(t.date for t in trades), end)
    total = pd.Series(0.0, index=days)
    valid = pd.Series(True, index=days)
    for ticker in {t.ticker for t in trades}:
        changes = pd.Series(0.0, index=days)
        for trade in trades:
            if trade.ticker == ticker:
                changes.loc[pd.Timestamp(trade.date)] += float(trade.quantity) * (1 if trade.side == "BUY" else -1)
        quantities = changes.cumsum()
        prices = histories.get(ticker, pd.Series(dtype=float)).reindex(days).ffill()
        # Missing prices only invalidate dates on which the stock is held.
        valid &= quantities.eq(0) | prices.notna()
        total += (quantities * prices).where(quantities.ne(0), 0).fillna(0)
    return total.where(valid).rename("Holdings value (USD)")
