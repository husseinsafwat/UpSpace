import os
import sys
import unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import BookingError, Database, PaymentError  # noqa: E402
from pricing import round_pound  # noqa: E402


class Rounding(unittest.TestCase):
    def test_round_pound(self):
        for exact, rounded in [(158.33, 158), (158.5, 159), (158.49, 158), (0.5, 1), (100, 100), (99.99, 100)]:
            self.assertEqual(round_pound(exact), rounded, exact)


class Payments(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.cash = self.db.cash_account_id()
        self.vf = self.db.add_account("vodafone", "0100 123 4567")
        self.vf2 = self.db.add_account("vodafone", "01111111111")
        self.ip = self.db.add_account("instapay", "upspace@instapay")
        self.room = self.db.add_room("A", 600)          # 600/h -> 100 min = 1000 EGP
        self.start = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)

    def card(self):
        return self.db.open_room_card("Ali", "01000000001", self.room, when=self.start)

    def close(self, cid, payments):
        return self.db.close_card(cid, end=self.start + timedelta(minutes=100), payments=payments)

    def test_method_is_required(self):
        cid = self.card()
        for bad in (None, [], [(None, 500)]):
            with self.assertRaises(PaymentError):
                self.close(cid, bad)
        self.assertEqual(self.db.get_card(cid)["status"], "open")

    def test_single_account_takes_everything(self):
        cid = self.card()
        self.close(cid, [(self.vf, None)])
        [p] = self.db.payments_for("card", cid)
        self.assertEqual((p["account_id"], p["amount"], p["label"]), (self.vf, 1000, "Vodafone Cash · 0100 123 4567"))

    def test_split_must_match_exactly(self):
        cid = self.card()
        with self.assertRaises(PaymentError) as e:                      # owner's example: 400 + 800 = 1200
            self.close(cid, [(self.vf, 400), (self.ip, 800)])
        self.assertIn("1,200", str(e.exception))
        self.assertIn("200 more than", str(e.exception))
        with self.assertRaises(PaymentError) as e:
            self.close(cid, [(self.vf, 400), (self.ip, 500)])
        self.assertIn("100 less than", str(e.exception))
        for bad in ([(self.vf, 400), (self.ip, None)], [(self.vf, 1000), (self.ip, 0)],
                    [(self.vf, 500), (self.vf, 500)], [(self.vf, "x"), (self.ip, 1000)]):
            with self.assertRaises(PaymentError):
                self.close(cid, bad)
        self.assertEqual(self.db.payments_for("card", cid), [])
        self.close(cid, [(self.cash, 300), (self.vf, 400), (self.ip, "300")])
        self.assertEqual(sum(p["amount"] for p in self.db.payments_for("card", cid)), 1000)

    def test_expenses_follow_same_rules(self):
        with self.assertRaises(PaymentError):
            self.db.add_expense("Milk", 120)
        with self.assertRaises(PaymentError):
            self.db.add_expense("Milk", 120, payments=[(self.cash, 100), (self.vf, 30)])
        eid = self.db.add_expense("Milk", 120, payments=[(self.cash, 100), (self.vf, 20)])
        self.db.update_expense(eid, "Milk", 150, payments=[(self.vf2, None)])
        [p] = self.db.payments_for("expense", eid)
        self.assertEqual((p["account_id"], p["amount"]), (self.vf2, 150))
        self.db.delete_expense(eid)
        self.assertEqual(self.db.payments_for("expense", eid), [])

    def test_balances_and_summary(self):
        self.close(self.card(), [(self.cash, 300), (self.vf, 700)])
        self.close(self.card(), [(self.ip, None)])
        self.db.add_expense("Milk", 120, when=self.start, payments=[(self.cash, 100), (self.vf, 20)])
        s = self.db.summary(self.start.date(), self.start.date())
        acc = {a["id"]: a for a in s["accounts"]}
        self.assertEqual((acc[self.cash]["in"], acc[self.cash]["out"], acc[self.cash]["balance"]), (300, 100, 200))
        self.assertEqual(acc[self.vf]["balance"], 680)
        self.assertEqual(acc[self.ip]["in"], 1000)
        self.assertEqual(acc[self.vf2]["in"], 0)
        self.assertEqual(s["income_total"], 2000)
        self.assertEqual(sum(a["balance"] for a in s["accounts"]), s["net"])
        self.assertEqual(self.db.summary(date(2000, 1, 1), date(2000, 1, 1))["accounts"][0]["in"], 0)

    def test_rounding_in_summary(self):
        room = self.db.add_room("B", 100)
        cid = self.db.open_room_card("S", "01000000007", room, when=self.start)
        bill = self.db.close_card(cid, end=self.start + timedelta(minutes=95), payments=[(self.cash, None)])
        self.assertEqual((bill.exact_total, bill.total, bill.rounding), (158.33, 158, -0.33))
        s = self.db.summary(self.start.date(), self.start.date())
        self.assertEqual(s["income_total"], 158)
        self.assertEqual(s["rounding"], -0.33)
        self.assertEqual(round(s["room_time_income"] + s["hall_time_income"] + s["items_income"] + s["rounding"], 2),
                         s["income_total"])

    def test_accounts_admin(self):
        with self.assertRaises(BookingError):
            self.db.add_account("vodafone", "0100 123 4567")            # duplicate
        with self.assertRaises(BookingError):
            self.db.add_account("vodafone", "  ")
        with self.assertRaises(BookingError):
            self.db.remove_account(self.cash)
        self.close(self.card(), [(self.vf2, None)])
        self.db.remove_account(self.vf2)
        labels = [a["label"] for a in self.db.summary(self.start.date(), self.start.date())["accounts"]]
        self.assertIn("Vodafone Cash · 01111111111", labels)                # removed, but has money that day
        with self.assertRaises(PaymentError):
            self.close(self.card(), [(self.vf2, None)])


if __name__ == "__main__":
    unittest.main()
