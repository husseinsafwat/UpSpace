import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import BookingError, Database, item_label  # noqa: E402


class Menu(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")

    def find(self, name):
        return next(r for r in self.db.list_menu() if r["name"] == name)

    def test_seeded_once(self):
        latte = self.find("Latte")
        self.assertEqual(Database.item_prices(latte), {"S": 55, "M": 65})
        self.assertEqual(Database.item_prices(self.find("Tea")), {None: 15})
        self.assertIn("Add-ons", self.db.menu_categories())
        # removing everything doesn't bring the starting menu back
        for r in self.db.list_menu():
            self.db.remove_menu_item(r["id"])
        self.db._seed_menu()
        self.assertEqual(self.db.list_menu(), [])

    def test_add_and_validate(self):
        iid = self.db.add_menu_item("Croissant", "Food", 40)
        self.assertEqual(self.db.get_menu_item(iid)["price"], 40)
        sid = self.db.add_menu_item("Big Tea", "Hot Drinks", sizes={"S": 15, "M": 20, "L": 25})
        self.assertEqual(Database.item_prices(self.db.get_menu_item(sid)), {"S": 15, "M": 20, "L": 25})
        bad = [("", "Food", 10, None), ("X", "", 10, None), ("X", "Food", None, None), ("X", "Food", 0, None),
               ("X", "Food", None, {"S": 10}), ("X", "Food", None, {"S": 10, "M": -1}),
               ("latte", "Hot Drinks", 30, None)]
        for name, cat, price, sizes in bad:
            with self.assertRaises(BookingError, msg=(name, cat, price, sizes)):
                self.db.add_menu_item(name, cat, price, sizes)

    def test_edit_keeps_old_bills(self):
        room = self.db.add_room("A", 100)
        cid = self.db.open_room_card("Ali", "01000000001", room)
        latte = self.find("Latte")
        self.db.add_items(cid, [(latte["id"], "M", 2)])
        self.db.update_menu_item(latte["id"], "Latte", "Hot Drinks", sizes={"S": 60, "M": 70})
        self.db.remove_menu_item(latte["id"])
        [line] = self.db.card_items(cid)
        self.assertEqual((line["qty"], line["size"], line["unit_price"], line["price"]), (2, "M", 65, 130))
        self.assertEqual(item_label(line), "2 × Latte (M)")
        with self.assertRaises(BookingError):
            self.db.add_items(cid, [(latte["id"], "M", 1)])


class CartToCard(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.ids = {r["name"]: r["id"] for r in self.db.list_menu()}

    def test_hall_card_items_and_bill(self):
        start = datetime.now() - timedelta(minutes=160)
        cid = self.db.open_hall_card("G", "01000000006", 3, when=start)
        self.db.add_items(cid, [(self.ids["Latte"], "S", 2), (self.ids["Tea"], None, 1),
                                (self.ids["Flavor"], None, 1), (self.ids["Tea"], None, 0)])
        self.assertEqual(len(self.db.card_items(cid)), 3)            # qty 0 skipped
        bill = self.db.close_card(cid, end=start + timedelta(minutes=160), payments=[(self.db.cash_account_id(), None)])
        self.assertEqual(bill.time_cost, 120)                         # 3 chairs x 40
        self.assertEqual(bill.items_total, 110 + 15 + 15)
        self.assertEqual(bill.total, 260)

    def test_bad_size_or_empty_cart(self):
        cid = self.db.open_hall_card("G", "01000000006", 1)
        with self.assertRaises(BookingError):
            self.db.add_items(cid, [(self.ids["Latte"], None, 1)])   # needs a size
        with self.assertRaises(BookingError):
            self.db.add_items(cid, [(self.ids["Tea"], "M", 1)])      # has no sizes
        with self.assertRaises(BookingError):
            self.db.add_items(cid, [])
        self.assertEqual(self.db.card_items(cid), [])


class HallSettings(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")

    def test_defaults(self):
        self.assertEqual(self.db.hall_chairs_total(), 16)
        self.assertEqual(self.db.hall_tiers(), [15, 15, 10, 10])

    def test_change_chairs_and_prices(self):
        self.db.set_hall_settings(20, [20, 15, 10, 5])
        self.assertEqual(self.db.free_chairs(), 20)
        start = datetime.now() - timedelta(hours=10)
        cid = self.db.open_hall_card("G", "01000000006", 2, when=start)
        self.assertEqual(self.db.close_card(cid, end=start + timedelta(hours=10), payments=[(self.db.cash_account_id(), None)]).time_cost, 100)  # cap 50 x 2

    def test_open_card_keeps_its_prices(self):
        start = datetime.now() - timedelta(minutes=160)
        cid = self.db.open_hall_card("G", "01000000006", 3, when=start)
        self.db.set_hall_settings(16, [30, 30, 30, 30])
        self.assertEqual(self.db.close_card(cid, end=start + timedelta(minutes=160), payments=[(self.db.cash_account_id(), None)]).time_cost, 120)

    def test_validation(self):
        self.db.open_hall_card("G", "01000000006", 10)
        for chairs, tiers in [(9, [15, 15, 10, 10]), (0, [15, 15, 10, 10]), ("x", [15, 15, 10, 10]),
                              (16, [15, -1, 10, 10]), (16, [0, 0, 0, 0]), (16, ["a", 1, 1, 1])]:
            with self.assertRaises(BookingError, msg=(chairs, tiers)):
                self.db.set_hall_settings(chairs, tiers)
        self.db.set_hall_settings(10, [15, 15, 10, 10])
        self.assertEqual(self.db.free_chairs(), 0)


class OldDatabase(unittest.TestCase):
    def test_upgrade_v11_file(self):
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        c = sqlite3.connect(path)
        c.executescript("""
            CREATE TABLE cards (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, customer_name TEXT NOT NULL,
              phone TEXT, room_id INTEGER, room_name TEXT, rate REAL, chairs INTEGER,
              discount_type TEXT NOT NULL DEFAULT 'none', discount_value REAL NOT NULL DEFAULT 0,
              start_time TEXT NOT NULL, end_time TEXT, status TEXT NOT NULL DEFAULT 'open', minutes INTEGER,
              billed_hours REAL, time_cost REAL, items_total REAL, total REAL);
            CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, card_id INTEGER NOT NULL, name TEXT NOT NULL,
              price REAL NOT NULL, created_at TEXT NOT NULL);
            INSERT INTO cards (kind, customer_name, chairs, start_time) VALUES ('hall', 'Old', 2, '2026-11-11 10:00:00');
            INSERT INTO items (card_id, name, price, created_at) VALUES (1, 'Water', 10, '2026-11-11 10:05:00');
        """)
        c.commit()
        c.close()
        db = Database(path)
        [it] = db.card_items(1)
        self.assertEqual(item_label(it), "Water")
        bill = db.compute_bill(db.get_card(1), datetime(2026, 11, 11, 12, 40))   # 2h40 -> 3h -> 40 x 2
        self.assertEqual(bill.total, 90)
        self.assertTrue(db.list_menu())


if __name__ == "__main__":
    unittest.main()
