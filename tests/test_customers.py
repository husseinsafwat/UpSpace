import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import BookingError, Database, NameMismatch, normalize_phone  # noqa: E402


class Customers(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.cash = [(self.db.cash_account_id(), None)]
        self.room = self.db.add_room("A", 60)
        self.t = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)

    def test_phone_required_and_normalized(self):
        with self.assertRaises(BookingError):
            self.db.open_room_card("Ali", "", self.room)
        with self.assertRaises(BookingError):
            self.db.open_room_card("Ali", "123", self.room)
        self.assertEqual(normalize_phone("0100 123-4567"), "01001234567")

    def test_new_phone_registers_customer(self):
        cid = self.db.open_room_card("Ali Hassan", "0100 123 4567", self.room)
        cust = self.db.find_customer("01001234567")
        self.assertEqual(cust["name"], "Ali Hassan")
        self.assertEqual(self.db.get_card(cid)["customer_id"], cust["id"])

    def test_same_phone_other_name_is_refused(self):
        self.db.open_hall_card("Ali Hassan", "01001234567", 1)
        with self.assertRaises(NameMismatch) as e:
            self.db.open_hall_card("Omar", "0100-123-4567", 1)
        self.assertEqual(e.exception.customer["name"], "Ali Hassan")
        # same name, any case -> fine, one customer only
        self.db.open_hall_card("ali hassan", "01001234567", 1)
        self.assertEqual(len(self.db.list_customers()), 1)

    def test_customers_tab_rules(self):
        a = self.db.add_customer("Sara", "01111111111")
        with self.assertRaises(BookingError):
            self.db.add_customer("Mona", "011 1111 1111")
        b = self.db.add_customer("Mona", "01222222222")
        with self.assertRaises(BookingError):
            self.db.update_customer(b, "Mona", "01111111111")
        self.db.update_customer(a, "Sara Adel", "01111111111", "VIP")
        self.assertEqual(self.db.get_customer(a)["name"], "Sara Adel")
        self.db.remove_customer(b)
        self.db.open_room_card("Sara Adel", "01111111111", self.room)
        with self.assertRaises(BookingError):
            self.db.remove_customer(a)

    def test_stats(self):
        c1 = self.db.open_room_card("Ali", "01001234567", self.room, when=self.t)
        menu = self.db.list_menu()
        tea = next(m for m in menu if m["name"] == "Tea")
        self.db.add_items(c1, [(tea["id"], None, 2)])
        self.db.close_card(c1, self.t + timedelta(minutes=90), self.cash)        # 90 + 30 = 120
        h = self.db.open_hall_card("Ali", "01001234567", 2, when=self.t + timedelta(hours=2))
        self.db.close_card(h, self.t + timedelta(hours=3), self.cash)            # 2 chairs x 15 = 30
        self.db.open_hall_card("Sara", "01111111111", 1, when=self.t)              # open: not counted
        ali = self.db.find_customer("01001234567")["id"]
        s = self.db.customer_stats(ali)
        self.assertEqual((s["visits"], s["room_visits"], s["hall_visits"]), (2, 1, 1))
        self.assertEqual(s["total"], 150)
        self.assertEqual(s["items_count"], 2)
        self.assertEqual(s["minutes"], 150)
        self.assertEqual(s["average"], 75)
        self.assertEqual(s["items"][0]["name"], "Tea")
        top = self.db.top_customers()
        self.assertEqual([r["name"] for r in top], ["Ali"])
        self.assertEqual(top[0]["items_count"], 2)
        self.assertEqual(self.db.customer_stats(ali, date.today() + timedelta(days=1),
                                                date.today() + timedelta(days=2))["visits"], 0)
        self.assertEqual(self.db.list_customers("0100")[0]["visits"], 2)

    def test_old_cards_are_imported(self):
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        db = Database(path)
        room = db.add_room("A", 60)
        cash = [(db.cash_account_id(), None)]
        for name, when in (("Ali", self.t - timedelta(days=2)), ("Ali H.", self.t - timedelta(days=1))):
            cid = db.open_room_card(name if name == "Ali" else "Ali", "01001234567", room, when=when)
            db.close_card(cid, when + timedelta(hours=1), cash)
        db.conn.execute("UPDATE cards SET customer_id = NULL")
        db.conn.execute("UPDATE cards SET customer_name = 'Ali H.' WHERE id = 2")
        db.conn.execute("UPDATE cards SET phone = NULL WHERE id = 1")
        db.conn.execute("INSERT INTO cards (kind, customer_name, phone, chairs, start_time, status) "
                        "VALUES ('hall', 'Old', '0100-123-4567', 1, '2026-01-01 10:00:00', 'cancelled')")
        db.conn.execute("DELETE FROM customers")
        db.conn.commit()
        db.conn.close()
        db = Database(path)
        rows = db.list_customers()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Ali H.")      # newest name wins
        self.assertEqual(rows[0]["visits"], 1)
        self.assertIsNone(db.get_card(1)["customer_id"])
        db.conn.close()


if __name__ == "__main__":
    unittest.main()
