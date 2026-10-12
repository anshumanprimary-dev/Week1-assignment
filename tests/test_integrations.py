import unittest
from types import SimpleNamespace
from datetime import date
from unittest.mock import Mock
import pandas as pd
from week1_assignment.assistant import answer, error_message
from openai import RateLimitError, AuthenticationError
import httpx
from week1_assignment.ledger import parse_csv
from week1_assignment.market import historical_values, value_holdings


class IntegrationTests(unittest.TestCase):
    def test_credit_exhaustion_is_explained_without_echoing_error(self):
        response = httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
        error = RateLimitError("secret error content", response=response,
                               body={"code": "credit_balance_exhausted"})
        message = error_message(error)
        self.assertIn("credits or quota are exhausted", message)
        self.assertNotIn("secret", message)

    def test_invalid_key_is_distinct(self):
        response = httpx.Response(401, request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
        error = AuthenticationError("secret key", response=response, body={})
        self.assertIn("rejected the API key", error_message(error))
        self.assertNotIn("secret", error_message(error))

    def test_missing_quote_does_not_create_total(self):
        rows, total = value_holdings([dict(ticker="ABC", quantity=2, economic_basis=100, tax_basis=100)], {})
        self.assertIsNone(total)
        self.assertIsNone(rows[0]["market_value"])

    def test_weekend_carry_and_no_backfill(self):
        trades, _ = parse_csv(b"ticker,date,transaction_type,quantity,price\nABC,2026-01-02,BUY,2,100")
        prices = {"ABC": pd.Series([110.0], index=pd.to_datetime(["2026-01-03"]))}
        values = historical_values(trades, prices, date(2026, 1, 4))
        self.assertTrue(pd.isna(values.iloc[0]))
        self.assertEqual(values.iloc[-1], 220)

    def test_ai_receives_evidence_and_disables_storage(self):
        client = Mock()
        client.responses.create.return_value = SimpleNamespace(output_text="Evidence-based explanation")
        self.assertEqual(answer("Explain", {"holdings": []}, "test-model", client=client), "Evidence-based explanation")
        args = client.responses.create.call_args.kwargs
        self.assertFalse(args["store"])
        self.assertIn("Current authoritative evidence", args["input"][0]["content"])
