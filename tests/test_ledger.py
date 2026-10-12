import unittest
from datetime import date
from pathlib import Path
from week1_assignment.ledger import parse_csv, analyze


def run(rows, complete=date(2026, 10, 11)):
    trades, _ = parse_csv(("ticker,date,transaction_type,quantity,price\n" + rows).encode(), today=complete)
    return analyze(trades, complete)


class LedgerTests(unittest.TestCase):
    def test_sample(self):
        trades, _ = parse_csv((Path(__file__).parents[1] / "Week1 Sample Data - Sheet1.csv").read_bytes())
        result = analyze(trades, date(2026, 10, 11))
        self.assertEqual({h["ticker"]: h["quantity"] for h in result["holdings"]}, {"AAPL": 15, "MSFT": 9, "TSLA": 4})
        self.assertAlmostEqual(result["purchases"], 18630.79)
        self.assertAlmostEqual(result["sale_proceeds"], 11455.29)
        june = next(s for s in result["sales"] if s["date"] == "2026-06-04")
        self.assertAlmostEqual(june["economic_gain"], -75.20)
        # All deferred basis remains in open lots, preserving economic identity.
        self.assertAlmostEqual(sum(s["tax_adjusted_gain"] for s in result["sales"]) - sum(s["economic_gain"] for s in result["sales"]),
                               sum(h["tax_basis"] - h["economic_basis"] for h in result["holdings"]))

    def test_fifo_partial_sale(self):
        result = run("ABC,2026-01-01,BUY,5,100\nABC,2026-02-01,BUY,5,200\nABC,2026-03-15,SELL,7,150")
        self.assertEqual(result["sales"][0]["economic_gain"], 150)
        self.assertEqual(result["holdings"][0]["economic_basis"], 600)

    def test_partial_future_replacement(self):
        result = run("ABC,2026-01-01,BUY,10,100\nABC,2026-03-01,SELL,10,80\nABC,2026-03-31,BUY,4,90")
        self.assertEqual(result["sales"][0]["disallowed_loss"], 80)
        self.assertEqual(result["sales"][0]["tax_adjusted_gain"], -120)
        self.assertEqual(result["holdings"][0]["tax_basis"], 440)

    def test_outside_window(self):
        result = run("ABC,2026-01-01,BUY,10,100\nABC,2026-03-01,SELL,10,80\nABC,2026-04-01,BUY,4,90")
        self.assertFalse(result["wash_sales"])

    def test_prior_replacement_and_split(self):
        result = run("ABC,2026-01-01,BUY,5,100\nABC,2026-02-20,BUY,10,90\nABC,2026-03-01,SELL,5,80\nABC,2026-04-15,SELL,7,110")
        self.assertEqual(result["wash_sales"][0]["quantity"], 5)
        self.assertEqual(result["sales"][1]["tax_adjusted_gain"], 40)
        self.assertEqual(result["holdings"][0]["tax_basis"], 270)

    def test_chained_loss(self):
        result = run("ABC,2026-01-01,BUY,5,100\nABC,2026-03-01,SELL,5,80\nABC,2026-03-02,BUY,5,90\nABC,2026-03-03,SELL,5,85\nABC,2026-03-04,BUY,5,95")
        self.assertEqual(result["sales"][1]["disallowed_loss"], 125)
        self.assertEqual(result["holdings"][0]["tax_basis"], 600)

    def test_incomplete_coverage(self):
        result = run("ABC,2026-01-01,BUY,5,100\nABC,2026-03-01,SELL,5,80\nABC,2026-03-02,BUY,5,90", date(2026, 3, 2))
        self.assertIn("Provisional", result["wash_sales"][0]["status"])

    def test_validation(self):
        for rows in ("ABC,2026-01-01,SELL,5,100", "ABC,2026-01-01,BUY,NaN,100", "ABC,2026-01-01,BUY,-5,100"):
            with self.assertRaises(ValueError):
                run(rows)


if __name__ == "__main__":
    unittest.main()
