"""helpers.py uchun testlar."""

import datetime as dt
import unittest

from helpers import fmt_money, list_time, now_time_str, parse_summa, today_str


class HelpersTest(unittest.TestCase):
    def test_today_and_time_format(self):
        moment = dt.datetime(2026, 10, 22, 9, 5, 3)
        self.assertEqual(today_str(moment), "22.10.2026")
        self.assertEqual(now_time_str(moment), "09:05:03")

    def test_parse_summa_accepts_common_cashier_input(self):
        self.assertEqual(parse_summa("15000"), 15000.0)
        self.assertEqual(parse_summa("1 250 000"), 1250000.0)
        self.assertEqual(parse_summa("1\u00a0250\u00a0000"), 1250000.0)
        self.assertEqual(parse_summa("15,5"), 15.5)
        self.assertEqual(parse_summa("15.5"), 15.5)
        self.assertEqual(parse_summa("12000 so'm"), 12000.0)
        self.assertEqual(parse_summa("  "), None)
        self.assertEqual(parse_summa("abc"), None)
        self.assertEqual(parse_summa("."), None)

    def test_fmt_money(self):
        self.assertEqual(fmt_money(1250000), "1 250 000")
        self.assertEqual(fmt_money(1500.5), "1 500.50")

    def test_list_time(self):
        self.assertEqual(list_time("14:05:09"), "14:05")
        self.assertEqual(list_time("14:05"), "14:05")


if __name__ == "__main__":
    unittest.main()
