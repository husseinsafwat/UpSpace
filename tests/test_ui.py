"""Smoke tests for the Qt screens: every page and popup builds, and the main flows work end to end."""
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PySide6.QtWidgets import QApplication, QDialog
except ImportError:  # pragma: no cover
    QApplication = None

if QApplication is not None:
    import main  # noqa: E402
    from ui import dialogs  # noqa: E402
    from ui.window import MainView  # noqa: E402


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Screens(unittest.TestCase):
    def setUp(self):
        self.app, self.win = main.create_app(":memory:")
        self.db = self.win.db
        self.room = self.db.add_room("Room A", 600)
        self.win.show_main()
        self.view: MainView = self.win.centralWidget()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.db.conn.close()

    def test_every_page_opens(self):
        for name in ("Rooms", "Hall", "Customers", "Menu", "Inventory", "Money", "Settings"):
            self.view.show_page(name)
            self.assertIs(self.view.stack.currentWidget(), self.view.pages[name])
        self.view.tick()

    def test_room_flow(self):
        rooms = self.view.pages["Rooms"]
        rooms.phone.setText("01001234567")
        rooms.name.setText("Ali")
        rooms.start()
        self.assertEqual(len(rooms.cards.cards), 1)
        card = self.db.open_cards("room")[0]
        self.assertEqual(rooms.room.currentText(), "No free rooms")

        # add 2 of the first menu item through the picker
        picker = dialogs.MenuPickerDialog(self.win, self.db, card)
        item = self.db.list_menu()[0]
        size = next(iter(self.db.item_prices(item)))
        picker.change(item["id"], size, 1)
        picker.change(item["id"], size, 1)
        self.assertTrue(picker.review_btn.isEnabled())
        self.db.add_items(card["id"], [(i, s, q) for (i, s), q in picker.cart.items()])
        self.assertEqual(self.db.card_items(card["id"])[0]["qty"], 2)

        # checkout with a split that doesn't match -> popup, card stays open
        self.db.conn.execute("UPDATE cards SET start_time=? WHERE id=?",
                             ((datetime.now() - timedelta(minutes=100)).strftime("%Y-%m-%d %H:%M:%S"), card["id"]))
        card = self.db.get_card(card["id"])
        vf = self.db.add_account("vodafone", "0100")
        co = dialogs.CheckoutDialog(self.win, self.db, card)
        cash_box, cash_amt = co.picker.rows[self.db.cash_account_id()]
        vf_box, vf_amt = co.picker.rows[vf]
        cash_box.setChecked(True)
        vf_box.setChecked(True)
        cash_amt.setText("1")
        vf_amt.setText("1")
        with mock.patch.object(dialogs, "alert") as alert:
            co.save()
            alert.assert_called_once()
        self.assertEqual(len(self.db.open_cards("room")), 1)

        # one account -> whole bill
        vf_box.setChecked(False)
        co.save()
        self.assertEqual(co.result(), QDialog.Accepted)
        self.assertEqual(self.db.open_cards("room"), [])
        self.view.data_changed()
        self.assertEqual(rooms.cards.cards, [])

    def test_hall_and_money(self):
        hall = self.view.pages["Hall"]
        self.view.show_page("Hall")
        hall.phone.setText("01001234568")
        hall.name.setText("Group")
        hall.step(2)
        hall.start()
        self.assertEqual(self.db.open_cards("hall")[0]["chairs"], 3)
        ex = dialogs.ExpenseDialog(self.win, self.db)
        self.assertIsInstance(ex, QDialog)
        self.db.add_expense("Milk", 50, payments=[(self.db.cash_account_id(), None)])
        self.view.data_changed()
        self.view.show_page("Money")
        money = self.view.pages["Money"]
        self.assertEqual(money.kpi["expenses"].text(), "50 EGP")
        self.assertEqual(money.expenses.rowCount(), 1)

    def test_known_phone_and_name_popup(self):
        from ui import pages
        self.db.add_customer("Sara Adel", "01111111111")
        hall = self.view.pages["Hall"]
        hall.phone.setText("0111 111 1111")
        self.assertEqual(hall.name.text(), "Sara Adel")          # filled in from the customer list
        hall.name.setText("Someone else")
        with mock.patch.object(pages, "name_mismatch_popup", return_value=True) as popup:
            hall.start()
            popup.assert_called_once()
        card = self.db.open_cards("hall")[0]
        self.assertEqual(card["customer_name"], "Sara Adel")

    def test_out_of_stock_opens_inventory(self):
        water = self.db.add_stock_item("Water bottle", "bottle", 1)
        item = self.db.add_menu_item("Water", "Drinks", 10)
        self.db.set_menu_stock(item, water)
        card = self.db.get_card(self.db.open_hall_card("G", "01000000009", 1))
        picker = dialogs.MenuPickerDialog(self.win, self.db, card)
        picker.change(item, None, 1)
        with mock.patch.object(dialogs, "out_of_stock_popup", return_value=True):
            picker.change(item, None, 1)                          # only 1 in stock
        self.assertEqual(picker.cart[(item, None)], 1)
        self.assertEqual(picker.goto_stock, water)
        with mock.patch.object(dialogs.RestockDialog, "exec", return_value=QDialog.Rejected) as ex:
            self.view.open_restock(water)
            ex.assert_called_once()
        self.assertEqual(self.view.current, "Inventory")

    def test_customers_and_inventory_pages(self):
        import tempfile
        cash = [(self.db.cash_account_id(), None)]
        cid = self.db.open_room_card("Ali", "01001234567", self.room)
        self.db.close_card(cid, payments=cash)
        self.db.add_stock_item("Cola can", "can", 2, 5)
        self.view.data_changed()
        self.view.show_page("Inventory")
        inv = self.view.pages["Inventory"]
        self.assertEqual(inv.kpi["low"].text(), "1")
        self.view.show_page("Customers")
        page = self.view.pages["Customers"]
        self.assertEqual(page.manage.table.rowCount(), 1)
        ali = self.db.find_customer("01001234567")["id"]
        page.show_stats(ali)
        self.assertEqual(page.stats.customer_id(), ali)
        self.assertEqual(page.stats.k["visits"][0].text(), "1")
        import customer_report
        for who in (None, ali):
            path = os.path.join(tempfile.mkdtemp(), "c.xlsx")
            customer_report.build_customer_report(self.db, who, None, None, path)
            self.assertTrue(os.path.getsize(path) > 0)

    def test_stock_dialog_puts_item_on_menu(self):
        d = dialogs.StockItemDialog(self.win, self.db)
        d.name.setText("Juice box")
        d.qty.setText("12")
        d.price.setText("25")
        d.cat.setCurrentText("Add-ons")
        d.save()
        self.assertEqual(d.result(), QDialog.Accepted)
        m = next(x for x in self.db.list_menu() if x["name"] == "Juice box")
        self.assertEqual(self.db.stock_left_for(m), 12)
        d2 = dialogs.StockItemDialog(self.win, self.db)
        d2.name.setText("Ice")
        d2.save()                                          # price missing -> nothing saved
        self.assertNotEqual(d2.result(), QDialog.Accepted)
        self.assertNotIn("Ice", [s["name"] for s in self.db.list_stock()])

    def test_popups_build(self):
        card_id = self.db.open_room_card("Ali", "01000000001", self.room)
        card = self.db.get_card(card_id)
        for d in (dialogs.DiscountDialog(self.win, self.db, card), dialogs.RoomDialog(self.win, self.db),
                  dialogs.AccountDialog(self.win, self.db), dialogs.MenuItemDialog(self.win, self.db),
                  dialogs.ReportDialog(self.win, self.db), dialogs.CheckoutDialog(self.win, self.db, card),
                  dialogs.CustomerDialog(self.win, self.db), dialogs.StockItemDialog(self.win, self.db)):
            d.show()
            d.close()


if __name__ == "__main__":
    unittest.main()
