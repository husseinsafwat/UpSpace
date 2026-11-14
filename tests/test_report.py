import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openpyxl import load_workbook  # noqa: E402

from db import Database  # noqa: E402
from report import build_report, default_filename  # noqa: E402


class Report(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.menu = {r["name"]: r["id"] for r in self.db.list_menu()}
        self.room = self.db.add_room("A", 120)
        self.d1 = date.today() - timedelta(days=2)
        self.d3 = date.today()
        t1 = datetime.combine(self.d1, datetime.min.time()).replace(hour=10)
        c = self.db.open_room_card("Ali", "01000000001", self.room, when=t1)
        self.db.add_items(c, [(self.menu["Latte"], "M", 2), (self.menu["Flavor"], None, 1)])
        self.db.close_card(c, end=t1 + timedelta(minutes=90), payments=[(self.db.cash_account_id(), None)])                 # 180 + 145
        h = self.db.open_hall_card("G", "01000000006", 3, when=t1)
        self.db.add_items(h, [(self.menu["Latte"], "M", 1)])
        self.db.close_card(h, end=t1 + timedelta(minutes=160), payments=[(self.db.cash_account_id(), None)])                # 120 + 65
        self.db.add_expense("Milk", 70, when=t1 + timedelta(hours=1), payments=[(self.db.cash_account_id(), None)])
        t3 = datetime.combine(self.d3, datetime.min.time()).replace(hour=0, minute=5)
        self.db.add_expense("Sugar", 30, when=t3, payments=[(self.db.cash_account_id(), None)])

    def test_period_checks(self):
        today = date.today()
        self.assertIsNone(self.db.check_period(self.d1, today))
        self.assertIsNotNone(self.db.check_period(self.d1 - timedelta(days=1), today))   # before data
        self.assertIsNotNone(self.db.check_period(self.d1, today + timedelta(days=1)))   # future
        self.assertIsNotNone(self.db.check_period(today, self.d1))                       # reversed
        self.assertIsNotNone(Database(":memory:").check_period(today, today))           # no data

    def test_items_sold(self):
        rows = {(r["name"], r["size"]): (r["qty"], r["total"]) for r in self.db.items_sold_between(self.d1, self.d1)}
        self.assertEqual(rows, {("Latte", "M"): (3, 195), ("Flavor", None): (1, 15)})

    def test_workbook(self):
        path = os.path.join(tempfile.mkdtemp(), default_filename(self.d1, self.d3))
        totals = build_report(self.db, self.d1, self.d3, path)
        self.assertEqual(totals["income_total"], 180 + 120 + 210)
        self.assertEqual(totals["expenses_total"], 100)
        self.assertEqual(totals["net"], 410)
        wb = load_workbook(path)
        # day 2 has nothing -> no sheet, but it is a row on Total
        self.assertEqual(wb.sheetnames, [f"{self.d1:%Y-%m-%d}", f"{self.d3:%Y-%m-%d}", "Total"])
        total = wb["Total"]
        self.assertEqual(total.max_row >= 8, True)
        self.assertEqual(total.cell(row=8, column=1).value, "TOTAL")
        self.assertEqual(total.cell(row=8, column=14).value, 410)
        self.assertEqual(total.cell(row=8, column=10).value, 510)                  # all paid in cash
        self.assertEqual(total.cell(row=6, column=9).value, 0)                   # quiet day = 0
        values = [c.value for row in wb[f"{self.d1:%Y-%m-%d}"].iter_rows() for c in row]
        self.assertIn("Latte", values)
        self.assertNotIn("Ali", values)                                          # no individual bills

    def test_bad_period_raises(self):
        with self.assertRaises(ValueError):
            build_report(self.db, self.d3, self.d1, os.path.join(tempfile.mkdtemp(), "x.xlsx"))


if __name__ == "__main__":
    unittest.main()
