"""Decimal FIFO ledger and single-account, same-ticker wash-sale screening."""
import csv
import io
import re
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class Trade:
    id: int
    ticker: str
    date: date
    side: str
    quantity: Decimal
    price: Decimal


def parse_csv(content: bytes, today: date | None = None):
    today = today or date.today()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    required = {"ticker", "date", "transaction_type", "quantity", "price"}
    if not reader.fieldnames or set(reader.fieldnames) != required:
        raise ValueError("CSV must contain exactly: ticker,date,transaction_type,quantity,price")
    trades, warnings, seen = [], [], set()
    for row_number, row in enumerate(reader, 2):
        try:
            ticker = row["ticker"].strip().upper()
            day = date.fromisoformat(row["date"].strip())
            side = row["transaction_type"].strip().upper()
            quantity, price = Decimal(row["quantity"]), Decimal(row["price"])
            if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-^=]{0,19}", ticker):
                raise ValueError("Invalid ticker")
            if side not in {"BUY", "SELL"}:
                raise ValueError("Transaction must be BUY or SELL")
            if not quantity.is_finite() or not price.is_finite() or quantity <= 0 or price <= 0:
                raise ValueError("Quantity and price must be positive finite numbers")
            if day > today:
                raise ValueError("Future transactions are not supported")
            key = (ticker, day, side, quantity, price)
            if key in seen:
                warnings.append(f"Row {row_number}: identical transaction exists; retained, please review.")
            seen.add(key)
            trades.append(Trade(row_number, ticker, day, side, quantity, price))
        except (ValueError, InvalidOperation, AttributeError, TypeError) as exc:
            raise ValueError(f"Row {row_number}: {exc}") from exc
    if not trades:
        raise ValueError("CSV has no transactions")
    trades.sort(key=lambda t: (t.date, t.id))
    balances = {}
    for t in trades:
        balances[t.ticker] = balances.get(t.ticker, Decimal(0)) + (t.quantity if t.side == "BUY" else -t.quantity)
        if balances[t.ticker] < 0:
            raise ValueError(f"Row {t.id}: sale exceeds available {t.ticker} shares")
    if len({t.date for t in trades}) < len(trades):
        warnings.append("Same-day transactions use CSV row order; ensure it matches execution order.")
    return trades, warnings


@dataclass
class Lot:
    trade: Trade
    quantity: Decimal
    tax_price: Decimal
    matched: bool = False


def analyze(trades: list[Trade], complete_through: date | None = None):
    """Split FIFO lots on disposal/matching, preserving per-share adjusted basis.

    Future replacement lots are reserved before activation. Prior replacements
    must remain held after the loss sale. Each replacement share is matched once.
    Results are screening estimates, not tax filing records.
    """
    lots = [Lot(t, t.quantity, t.price) for t in trades if t.side == "BUY"]
    sales, washes, details = [], [], []
    zero = Decimal(0)
    for t in trades:
        if t.side == "BUY":
            continue
        remaining, disposed = t.quantity, []
        for lot in list(lots):
            if remaining <= 0:
                break
            if lot.trade.ticker != t.ticker or (lot.trade.date, lot.trade.id) >= (t.date, t.id):
                continue
            amount = min(remaining, lot.quantity)
            if amount <= 0:
                continue
            disposed.append((lot.trade, amount, lot.tax_price))
            lot.quantity -= amount
            remaining -= amount
        if remaining:
            raise ValueError(f"Sale {t.id} exceeds holdings")
        economic = sum((q * (t.price - buy.price) for buy, q, tax in disposed), zero)
        tax_gain = sum((q * (t.price - tax) for buy, q, tax in disposed), zero)
        disallowed = zero
        for buy, quantity, tax_price in disposed:
            details.append(dict(sale_id=t.id, ticker=t.ticker, sale_date=str(t.date), purchase_id=buy.id,
                                purchase_date=str(buy.date), quantity=float(quantity),
                                original_cost=float(quantity * buy.price), adjusted_cost=float(quantity * tax_price),
                                proceeds=float(quantity * t.price)))
            loss_per_share = tax_price - t.price
            if loss_per_share <= 0:
                continue
            unmatched = quantity
            for candidate in list(lots):
                if unmatched <= 0:
                    break
                if (candidate.trade.ticker != t.ticker or candidate.matched or candidate.quantity <= 0
                    or abs((candidate.trade.date - t.date).days) > 30):
                    continue
                matched = min(unmatched, candidate.quantity)
                # Preserve FIFO order when only part of a replacement lot matches.
                if matched < candidate.quantity:
                    tail = replace(candidate, quantity=candidate.quantity - matched)
                    lots.insert(lots.index(candidate) + 1, tail)
                    candidate.quantity = matched
                candidate.tax_price += loss_per_share
                candidate.matched = True
                deferred = matched * loss_per_share
                disallowed += deferred
                unmatched -= matched
                washes.append(dict(sale_id=t.id, ticker=t.ticker, sale_date=str(t.date),
                                   sold_purchase_id=buy.id, replacement_id=candidate.trade.id,
                                   replacement_date=str(candidate.trade.date), quantity=float(matched),
                                   disallowed_loss=float(deferred),
                                   replacement_adjusted_price=float(candidate.tax_price),
                                   window_end=str(t.date + timedelta(days=30)),
                                   status="Covered within uploaded account" if complete_through and complete_through >= t.date + timedelta(days=30)
                                   else "Provisional: history coverage unconfirmed"))
        sales.append(dict(sale_id=t.id, ticker=t.ticker, date=str(t.date), quantity=float(t.quantity),
                          proceeds=float(t.quantity * t.price), economic_gain=float(economic),
                          disallowed_loss=float(disallowed), tax_adjusted_gain=float(tax_gain + disallowed),
                          window_end=str(t.date + timedelta(days=30)),
                          coverage_confirmed=bool(complete_through and complete_through >= t.date + timedelta(days=30))))
    holdings = []
    for ticker in sorted({t.ticker for t in trades}):
        active = [lot for lot in lots if lot.trade.ticker == ticker and lot.quantity > 0]
        quantity = sum((lot.quantity for lot in active), zero)
        if quantity:
            holdings.append(dict(ticker=ticker, quantity=float(quantity),
                                 economic_basis=float(sum((l.quantity * l.trade.price for l in active), zero)),
                                 tax_basis=float(sum((l.quantity * l.tax_price for l in active), zero))))
    return dict(holdings=holdings, sales=sales, wash_sales=washes, lot_disposals=details,
                purchases=float(sum((t.quantity*t.price for t in trades if t.side == "BUY"), zero)),
                sale_proceeds=float(sum((t.quantity*t.price for t in trades if t.side == "SELL"), zero)))
