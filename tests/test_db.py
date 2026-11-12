import os
import sys
import unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import BookingError, Database  # noqa: E402
from pricing import DISCOUNT_EGP, DISCOUNT_PCT  # noqa: E402


class DatabaseFlow(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.room = self.db.add_room("Meeting Room A", 120)

    def test_room_blocked_while_busy(self):
        self.db.open_room_card("Ali", "", self.room)
        with self.assertRaises(BookingError):
            self.db.open_room_card("Sara", "", self.room)
        self.assertEqual(self.db.free_rooms(), [])

    def test_room_checkout_with_discount_and_items(self):
        start = datetime.now() - timedelta(minutes=95)
        cid = self.db.open_room_card("Ali", "010", self.room, when=start)
        menu = {r["name"]: r["id"] for r in self.db.list_menu()}
        self.db.add_items(cid, [(menu["Tea"], None, 1), (menu["Herbs"], None, 1), (menu["Flavor"], None, 1)])
        self.db.set_discount(cid, DISCOUNT_EGP, 20)          # 100/hr
        bill = self.db.close_card(cid, end=start + timedelta(minutes=95))
        self.assertEqual(bill.time_cost, round(95 / 60 * 100, 2))
        self.assertEqual(bill.items_total, 50)                # 15 + 20 + 15
        self.assertEqual(bill.total, round(95 / 60 * 100 + 50, 2))
        self.assertEqual(len(self.db.free_rooms()), 1)       # room freed

    def test_pct_discount(self):
        start = datetime.now() - timedelta(minutes=60)
        cid = self.db.open_room_card("Ali", "", self.room, when=start)
        self.db.set_discount(cid, DISCOUNT_PCT, 50)
        self.assertEqual(self.db.close_card(cid, end=start + timedelta(minutes=60)).time_cost, 60)

    def test_hall_capacity(self):
        self.db.open_hall_card("Group", "", 10)
        with self.assertRaises(BookingError):
            self.db.open_hall_card("Big group", "", 7)
        self.db.open_hall_card("Six", "", 6)
        self.assertEqual(self.db.free_chairs(), 0)

    def test_hall_no_discount(self):
        cid = self.db.open_hall_card("G", "", 2)
        with self.assertRaises(BookingError):
            self.db.set_discount(cid, DISCOUNT_EGP, 5)

    def test_summary(self):
        start = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
        r = self.db.open_room_card("Ali", "", self.room, when=start)
        coffee = next(m["id"] for m in self.db.list_menu() if m["name"] == "French Coffee")
        self.db.add_items(r, [(coffee, None, 1)])                      # 40
        self.db.close_card(r, end=start + timedelta(minutes=90))       # 180 + 40
        h = self.db.open_hall_card("G", "", 3, when=start)
        self.db.close_card(h, end=start + timedelta(minutes=160))      # 3 x 40 = 120
        self.db.add_expense("Milk", 70, when=start + timedelta(hours=1))
        s = self.db.summary(start.date(), start.date())
        self.assertEqual(s["room_time_income"], 180)
        self.assertEqual(s["hall_time_income"], 120)
        self.assertEqual(s["items_income"], 40)
        self.assertEqual(s["income_total"], 340)
        self.assertEqual(s["expenses_total"], 70)
        self.assertEqual(s["net"], 270)
        self.assertEqual(s["hall_chair_hours"], 9)
        self.assertEqual(self.db.summary(date(2000, 1, 1), date(2000, 1, 1))["income_total"], 0)

    def test_room_validation(self):
        with self.assertRaises(BookingError):
            self.db.add_room("", 100)
        with self.assertRaises(BookingError):
            self.db.add_room("X", 0)
        with self.assertRaises(BookingError):
            self.db.add_room("meeting room a", 50)


if __name__ == "__main__":
    unittest.main()
