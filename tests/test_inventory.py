import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import BookingError, Database, OutOfStock, PaymentError  # noqa: E402


class Inventory(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.cash = self.db.cash_account_id()
        self.water = self.db.add_stock_item("Water bottle", "bottle", 3, 2)
        self.item = self.db.add_menu_item("Water", "Drinks", 10)
        self.db.set_menu_stock(self.item, self.water, 1)
        self.sized = self.db.add_menu_item("Cola", "Drinks", sizes={"S": 15, "M": 20})
        self.cola = self.db.add_stock_item("Cola can", "can", 10)
        self.db.set_menu_stock(self.sized, self.cola)
        self.card = self.db.open_hall_card("G", "01000000001", 1)

    def qty(self, sid):
        return self.db.get_stock_item(sid)["quantity"]

    def test_sale_subtracts_and_remove_returns(self):
        self.db.add_items(self.card, [(self.item, None, 2), (self.sized, "S", 1), (self.sized, "M", 2)])
        self.assertEqual(self.qty(self.water), 1)
        self.assertEqual(self.qty(self.cola), 7)                  # 1 unit per sale for every size
        line = self.db.card_items(self.card)[0]
        self.db.delete_item(line["id"])
        self.assertEqual(self.qty(self.water), 3)
        reasons = [m["reason"] for m in self.db.stock_moves(self.water)]
        self.assertEqual(reasons, ["return", "sale", "start"])

    def test_out_of_stock_blocks_everything(self):
        with self.assertRaises(OutOfStock) as e:
            self.db.add_items(self.card, [(self.sized, "S", 1), (self.item, None, 4)])
        self.assertEqual(e.exception.stock_item["id"], self.water)
        self.assertEqual(self.qty(self.cola), 10)                 # nothing taken
        self.assertEqual(self.db.card_items(self.card), [])

    def test_untracked_items_are_free(self):
        tea = next(m for m in self.db.list_menu() if m["name"] == "Tea")
        self.db.add_items(self.card, [(tea["id"], None, 50)])
        self.assertIsNone(self.db.stock_left_for(tea))
        self.assertEqual(self.db.stock_left_for(self.db.get_menu_item(self.item)), 3)

    def test_cancel_returns_stock(self):
        self.db.add_items(self.card, [(self.item, None, 3)])
        self.db.cancel_card(self.card)
        self.assertEqual(self.qty(self.water), 3)

    def test_restock_take_out_count(self):
        self.db.restock(self.water, 12, "Supplier")
        self.assertEqual(self.qty(self.water), 15)
        with self.assertRaises(BookingError):
            self.db.take_out(self.water, 1, "")
        with self.assertRaises(BookingError):
            self.db.take_out(self.water, 100, "broken")
        self.db.take_out(self.water, 2, "broken")
        self.db.set_count(self.water, 10, "Monthly count")
        self.assertEqual(self.qty(self.water), 10)
        self.assertEqual(self.db.stock_moves(self.water)[0]["change"], -3)

    def test_restock_as_expense(self):
        with self.assertRaises(PaymentError):
            self.db.restock(self.water, 24, cost=120)
        self.assertEqual(self.qty(self.water), 3)
        self.db.restock(self.water, 24, cost=120, payments=[(self.cash, None)])
        exp = self.db.conn.execute("SELECT * FROM expenses").fetchall()
        self.assertEqual(len(exp), 1)
        self.assertEqual(exp[0]["amount"], 120)
        self.assertIn("Water bottle", exp[0]["description"])
        self.assertEqual(self.qty(self.water), 27)

    def test_new_stock_item_can_go_on_the_menu(self):
        chips = self.db.add_stock_item("Chips", "bag", 10)
        mid = self.db.sell_stock_item(chips, 20, "Add-ons")
        m = self.db.get_menu_item(mid)
        self.assertEqual((m["name"], m["category"], m["price"], m["stock_item_id"]), ("Chips", "Add-ons", 20, chips))
        self.assertIn("Chips", [x["name"] for x in self.db.list_menu()])      # shows in + Items
        # same name already on the menu -> linked, not duplicated
        tea_stock = self.db.add_stock_item("Tea bags", "bag", 100)
        tea = next(x for x in self.db.list_menu() if x["name"] == "Tea")
        self.assertEqual(self.db.sell_stock_item(tea_stock, 99, "X", name="Tea"), tea["id"])
        self.assertEqual(sum(1 for x in self.db.list_menu() if x["name"] == "Tea"), 1)

    def test_rules(self):
        with self.assertRaises(BookingError):
            self.db.add_stock_item("water BOTTLE")
        with self.assertRaises(BookingError):
            self.db.add_stock_item("Ice", "kg", -1)
        self.db.remove_stock_item(self.water)
        self.assertIsNone(self.db.get_menu_item(self.item)["stock_item_id"])
        self.db.add_items(self.card, [(self.item, None, 9)])     # no longer tracked


if __name__ == "__main__":
    unittest.main()
