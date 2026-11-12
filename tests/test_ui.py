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
        for name in ("Rooms", "Hall", "Menu", "Money", "Settings"):
            self.view.show_page(name)
            self.assertIs(self.view.stack.currentWidget(), self.view.pages[name])
        self.view.tick()

    def test_room_flow(self):
        rooms = self.view.pages["Rooms"]
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

    def test_popups_build(self):
        card_id = self.db.open_room_card("Ali", "", self.room)
        card = self.db.get_card(card_id)
        for d in (dialogs.DiscountDialog(self.win, self.db, card), dialogs.RoomDialog(self.win, self.db),
                  dialogs.AccountDialog(self.win, self.db), dialogs.MenuItemDialog(self.win, self.db),
                  dialogs.ReportDialog(self.win, self.db), dialogs.CheckoutDialog(self.win, self.db, card)):
            d.show()
            d.close()


if __name__ == "__main__":
    unittest.main()
