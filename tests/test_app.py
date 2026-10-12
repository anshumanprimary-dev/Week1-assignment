import unittest
from pathlib import Path
from streamlit.testing.v1 import AppTest


class AppTests(unittest.TestCase):
    def test_sample_renders_four_tabs_without_external_calls(self):
        app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
        self.assertFalse(app.exception)
        next(b for b in app.button if b.label == "Load sample portfolio").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.tabs), 4)
        self.assertTrue(any(m.label == "Realized economic gain / loss" for m in app.metric))
        app.checkbox(key="coverage_confirmed").check().run()
        self.assertFalse(app.exception)
