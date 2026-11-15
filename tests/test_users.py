"""v2.2: users and permissions, deleting customers with history, editing card items, suggestions, cash drawer,
reset app data."""
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth  # noqa: E402
from db import BookingError, Database, OutOfStock, PermissionDenied  # noqa: E402


def admin_row(db):
    return db.conn.execute("SELECT * FROM users WHERE is_admin = 1").fetchone()


class Users(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.db.sign_in(admin_row(self.db))
        self.room = self.db.add_room("A", 60)
        self.cash = [(self.db.cash_account_id(), None)]

    def test_admin_seeded_with_hashed_password(self):
        a = admin_row(self.db)
        self.assertEqual(a["email"], "admin@gmail.com")
        self.assertNotIn("admin", a["password_hash"].split("$")[-1])
        self.assertTrue(a["password_hash"].startswith("pbkdf2_sha256$"))
        self.assertEqual(self.db.authenticate("ADMIN@gmail.com ", "admin")["id"], a["id"])
        with self.assertRaises(PermissionDenied):
            self.db.authenticate("admin@gmail.com", "wrong")
        self.assertTrue(self.db.can("money.report"))

    def test_new_user_has_no_permissions(self):
        uid = self.db.add_user("Sara", "sara@gmail.com", "1234")
        self.db.sign_in(self.db.authenticate("sara@gmail.com", "1234"))
        self.assertFalse(any(self.db.can(p) for p in auth.ALL_PERMISSIONS))
        with self.assertRaises(PermissionDenied):
            self.db.open_room_card("Ali", "01001234567", self.room)
        with self.assertRaises(PermissionDenied):
            self.db.add_user("X", "x@gmail.com", "1234")
        with self.assertRaises(PermissionDenied):
            self.db.reset_app_data("1234", "1234")
        self.assertEqual(self.db.user_perms(uid), [])

    def test_normal_user_template(self):
        t = next(t for t in self.db.list_templates() if t["name"] == "Normal user")
        perms = self.db.template_perms(t)
        self.db.add_user("Omar", "omar@gmail.com", "1234", perms)
        self.db.sign_in(self.db.authenticate("omar@gmail.com", "1234"))
        for p in ("rooms.open", "rooms.checkout", "hall.open", "hall.items", "customers.view", "money.view_in",
                  "money.view_expenses", "money.add_expense", "money.cash"):
            self.assertTrue(self.db.can(p), p)
        for p in ("money.report", "money.past_dates", "money.edit_expense", "customers.delete", "customers.edit",
                  "menu.view", "settings.view", "inventory.view"):
            self.assertFalse(self.db.can(p), p)
        cid = self.db.open_room_card("Ali", "01001234567", self.room)
        self.db.close_card(cid, payments=self.cash)
        self.assertEqual(self.db.get_card(cid)["closed_by"], self.db.user["id"])
        eid = self.db.add_expense("Milk", 40, payments=self.cash)
        with self.assertRaises(PermissionDenied):
            self.db.delete_expense(eid)
        self.db.set_opening_cash(date.today(), 300)
        with self.assertRaises(PermissionDenied):
            self.db.set_opening_cash(date.today() - timedelta(days=1), 100)

    def test_update_disable_delete_user(self):
        uid = self.db.add_user("Sara", "sara@gmail.com", "1234", ["menu.view"])
        with self.assertRaises(BookingError):
            self.db.add_user("Sara 2", "SARA@gmail.com", "1234")          # email taken
        self.db.update_user(uid, "Sara A", "sara@gmail.com", ["menu.view", "menu.edit", "nonsense"], active=False)
        self.assertEqual(sorted(self.db.user_perms(uid)), ["menu.edit", "menu.view"])
        with self.assertRaises(PermissionDenied):
            self.db.authenticate("sara@gmail.com", "1234")                 # disabled
        self.db.update_user(uid, "Sara A", "sara@gmail.com", active=True, password="abcd")
        self.assertEqual(self.db.authenticate("sara@gmail.com", "abcd")["name"], "Sara A")
        self.db.delete_user(uid)
        with self.assertRaises(PermissionDenied):
            self.db.authenticate("sara@gmail.com", "abcd")
        self.assertNotIn(uid, [u["id"] for u in self.db.list_users()])
        self.assertEqual(self.db.user_names()[uid], "Sara A")              # old records still show the name
        with self.assertRaises(BookingError):
            self.db.delete_user(admin_row(self.db)["id"])
        self.db.add_user("Sara", "sara@gmail.com", "1234")                  # the email is free again

    def test_change_password_and_templates(self):
        with self.assertRaises(BookingError):
            self.db.change_password("wrong", "abcd", "abcd")
        with self.assertRaises(BookingError):
            self.db.change_password("admin", "abcd", "abce")
        self.db.change_password("admin", "abcd", "abcd")
        self.assertTrue(self.db.authenticate("admin@gmail.com", "abcd"))
        tid = self.db.save_template("Barista", ["menu.view", "rooms.items"])
        self.db.save_template("barista", ["menu.view"])                      # same name: replaced
        t = next(t for t in self.db.list_templates() if t["id"] == tid)
        self.assertEqual(self.db.template_perms(t), ["menu.view"])
        self.db.delete_template(tid)
        self.assertNotIn(tid, [t["id"] for t in self.db.list_templates()])


class DeleteCustomer(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.room = self.db.add_room("A", 60)
        self.cash = [(self.db.cash_account_id(), None)]
        self.t = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)

    def test_delete_with_history_keeps_numbers(self):
        c1 = self.db.open_room_card("Ali", "01001234567", self.room, when=self.t)
        self.db.close_card(c1, self.t + timedelta(minutes=60), self.cash)
        c2 = self.db.open_room_card("Mona", "01111111111", self.room, when=self.t + timedelta(hours=1))
        self.db.close_card(c2, self.t + timedelta(minutes=120), self.cash)
        today = self.t.date()
        before = self.db.summary(today, today)
        all_before = self.db.customer_stats(None)
        ali = self.db.find_customer("01001234567")["id"]

        self.db.remove_customer(ali)

        self.assertIsNone(self.db.get_customer(ali))
        after = self.db.summary(today, today)
        self.assertEqual(after["income_total"], before["income_total"])
        self.assertEqual(self.db.account_totals(today, today), before["accounts"])
        all_after = self.db.customer_stats(None)
        self.assertEqual(all_after["total"], all_before["total"])
        self.assertEqual(all_after["visits"], 2)
        self.assertEqual(self.db.get_card(c1)["customer_name"], "Ali")      # the bill keeps its copy
        gone = self.db.removed_customers_total()
        self.assertEqual((gone["visits"], gone["total"]), (1, 60))
        self.assertEqual([r["name"] for r in self.db.top_customers()], ["Mona"])
        self.assertEqual(self.db.removed_customer_stats(), {"visits": 1, "spent": 60})

        # restarting the app must not bring the customer back from the old bills
        self.db._import_customers()
        self.assertIsNone(self.db.find_customer("01001234567"))
        # the same phone later starts as a new customer
        c3 = self.db.open_room_card("Ali", "01001234567", self.room)
        self.assertNotEqual(self.db.get_card(c3)["customer_id"], ali)
        with self.assertRaises(BookingError):                                # open card blocks the delete
            self.db.remove_customer(self.db.get_card(c3)["customer_id"])


class EditItems(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.water = self.db.add_stock_item("Water", "bottle", 5)
        self.item = self.db.add_menu_item("Water", "Drinks", 10)
        self.db.set_menu_stock(self.item, self.water)
        self.card = self.db.open_hall_card("G", "01000000009", 1)
        self.db.add_items(self.card, [(self.item, None, 3)])
        self.line = self.db.card_items(self.card)[0]

    def stock(self):
        return self.db.get_stock_item(self.water)["quantity"]

    def test_lower_raise_and_remove(self):
        self.assertEqual(self.stock(), 2)
        self.db.set_item_quantities(self.card, {self.line["id"]: 1})
        it = self.db.card_items(self.card)[0]
        self.assertEqual((it["qty"], it["price"]), (1, 10))
        self.assertEqual(self.stock(), 4)
        with self.assertRaises(OutOfStock):
            self.db.set_item_quantities(self.card, {it["id"]: 6})
        self.assertEqual(self.stock(), 4)
        self.db.set_item_quantities(self.card, {it["id"]: 5})
        self.assertEqual(self.stock(), 0)
        self.db.set_item_quantities(self.card, {it["id"]: 0})
        self.assertEqual(self.db.card_items(self.card), [])
        self.assertEqual(self.stock(), 5)

    def test_closed_card_is_final(self):
        self.db.close_card(self.card, payments=[(self.db.cash_account_id(), None)])
        with self.assertRaises(BookingError):
            self.db.set_item_quantities(self.card, {self.line["id"]: 1})
        with self.assertRaises(BookingError):
            self.db.delete_item(self.line["id"])


class Suggestions(unittest.TestCase):
    def test_search_by_name_or_phone(self):
        db = Database(":memory:")
        db.add_customer("Ahmed Ali", "01012345678")
        db.add_customer("Mona Ali", "01198765432")
        db.add_customer("Sara", "01234500000")
        self.assertEqual(db.search_customers("a"), [])                        # needs 2 characters
        self.assertEqual({r["name"] for r in db.search_customers("ali")}, {"Ahmed Ali", "Mona Ali"})
        self.assertEqual([r["name"] for r in db.search_customers("45678")], ["Ahmed Ali"])
        self.assertEqual([r["name"] for r in db.search_customers("0123 450")], ["Sara"])


class CashDrawer(unittest.TestCase):
    def test_drawer_math_and_not_income(self):
        db = Database(":memory:")
        room = db.add_room("A", 60)
        cash = db.cash_account_id()
        vf = db.add_account("vodafone", "0100")
        today = date.today()
        self.assertEqual(db.cash_drawer(today)["opening"], 0)
        db.set_opening_cash(today, 500)
        db.set_opening_cash(today, 400)                                        # replaces, not adds
        entry = db.add_cash(today, 100, "change from bank")
        start = datetime.now() - timedelta(minutes=60)
        c1 = db.open_room_card("Ali", "01001234567", room, when=start)
        db.close_card(c1, start + timedelta(minutes=60), [(cash, None)])       # 60 cash
        c2 = db.open_room_card("Mona", "01111111111", room, when=start)
        db.close_card(c2, start + timedelta(minutes=60), [(vf, None)])         # 60 Vodafone, not in the drawer
        db.add_expense("Milk", 30, payments=[(cash, None)])
        d = db.cash_drawer(today)
        self.assertEqual((d["opening"], d["added_total"], d["cash_in"], d["cash_out"]), (400, 100, 60, 30))
        self.assertEqual(d["drawer"], 530)
        self.assertEqual(db.summary(today, today)["income_total"], 120)       # opening/added cash not income
        db.update_cash(entry, 150, "x")
        self.assertEqual(db.cash_drawer(today)["drawer"], 580)
        db.delete_cash(entry)
        self.assertEqual(db.cash_drawer(today)["drawer"], 430)
        with self.assertRaises(BookingError):
            db.add_cash(today, 0)
        with self.assertRaises(BookingError):
            db.set_opening_cash(today, -5)
        self.assertEqual(db.cash_drawer(today - timedelta(days=1))["drawer"], 0)   # each day starts at 0

    def test_report_has_cash_lines(self):
        import report
        from openpyxl import load_workbook
        db = Database(":memory:")
        room = db.add_room("A", 60)
        db.set_opening_cash(date.today(), 250)
        c = db.open_room_card("Ali", "01001234567", room)
        db.close_card(c, payments=[(db.cash_account_id(), None)])
        path = os.path.join(tempfile.mkdtemp(), "r.xlsx")
        report.build_report(db, date.today(), date.today(), path)
        ws = load_workbook(path)[date.today().isoformat()]
        labels = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=4).value for r in range(1, 40)}
        self.assertEqual(labels["Opening cash (not income)"], 250)
        self.assertIn("Cash in drawer (end of day)", labels)


class Reset(unittest.TestCase):
    def test_reset_keeps_setup_and_makes_backup(self):
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "upspace.db")
        db = Database(path)
        db.sign_in(admin_row(db))
        room = db.add_room("A", 60)
        water = db.add_stock_item("Water", "bottle", 5)
        c = db.open_room_card("Ali", "01001234567", room)
        db.close_card(c, payments=[(db.cash_account_id(), None)])
        db.add_expense("Milk", 30, payments=[(db.cash_account_id(), None)])
        db.set_opening_cash(date.today(), 100)
        db.add_user("Sara", "sara@gmail.com", "1234")
        with self.assertRaises(BookingError):
            db.reset_app_data("admin", "admim")
        with self.assertRaises(BookingError):
            db.reset_app_data("nope", "nope")
        menu_before = len(db.list_menu())
        backup = db.reset_app_data("admin", "admin")
        self.assertTrue(os.path.exists(backup))
        self.assertEqual(os.path.dirname(backup), os.path.join(folder, "backups"))
        for t in ("cards", "items", "payments", "expenses", "customers", "stock_moves", "cash_drawer"):
            self.assertEqual(db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], 0, t)
        self.assertEqual(len(db.list_menu()), menu_before)
        self.assertEqual(len(db.list_rooms()), 1)
        self.assertEqual(len(db.list_users()), 2)
        self.assertEqual(db.get_stock_item(water)["quantity"], 0)
        old = Database(backup)                                                 # the backup has everything
        self.assertEqual(old.conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0], 1)
        old.conn.close()
        db.conn.close()


if __name__ == "__main__":
    unittest.main()
