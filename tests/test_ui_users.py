"""v2.2 screens: login, what a limited user sees, users popup, edit items, suggestions, cash drawer, reset."""
import os
import sys
import unittest
from datetime import date
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
    from ui.window import LoginView, MainView  # noqa: E402


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class UserScreens(unittest.TestCase):
    def setUp(self):
        self.app, self.win = main.create_app(":memory:")
        self.db = self.win.db
        self.room = self.db.add_room("Room A", 600)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.db.conn.close()

    def login(self, email, password):
        view = self.win.centralWidget()
        self.assertIsInstance(view, LoginView)
        view.email.setText(email)
        view.password.setText(password)
        view.submit()
        return view

    def normal_user(self):
        t = next(t for t in self.db.list_templates() if t["name"] == "Normal user")
        self.db.add_user("Omar", "omar@gmail.com", "1234", self.db.template_perms(t))

    def test_login_admin_and_wrong_password(self):
        view = self.login("admin@gmail.com", "nope")
        self.assertIn("Wrong", view.error.text())
        self.login("admin@gmail.com", "admin")
        main_view = self.win.centralWidget()
        self.assertIsInstance(main_view, MainView)
        self.assertEqual(list(main_view.pages),
                         ["Rooms", "Hall", "Customers", "Menu", "Inventory", "Money", "Settings"])
        self.assertTrue(hasattr(main_view.pages["Settings"], "users"))

    def test_normal_user_sees_only_their_pages(self):
        self.db.sign_in(None)
        self.normal_user()
        self.login("omar@gmail.com", "1234")
        view: MainView = self.win.centralWidget()
        self.assertEqual(list(view.pages), ["Rooms", "Hall", "Customers", "Money"])
        money = view.pages["Money"]
        texts = [view.pages["Money"].toolbar.itemAt(i).widget().text() for i in range(money.toolbar.count())]
        self.assertEqual(texts, ["+ Add expense"])                  # no report download
        self.assertFalse(money.past)
        money.shift(-1)
        self.assertEqual(money.day, date.today())                   # can't go to previous dates
        cust = view.pages["Customers"]
        self.assertEqual(cust.stack.currentIndex(), 0)
        self.assertFalse(cust.manage.can_delete)

    def test_card_buttons_follow_permissions(self):
        from PySide6.QtWidgets import QPushButton
        self.db.open_room_card("Ali", "01001234567", self.room)
        self.db.add_user("Viewer", "v@gmail.com", "1234", ["rooms.view", "rooms.checkout"])
        self.win.show_main()
        card = self.win.centralWidget().pages["Rooms"].cards.cards[0]
        self.assertEqual([b.text() for b in card.findChildren(QPushButton)],
                         ["+ Items", "Discount", "Cancel", "Checkout"])
        self.win.show_login()
        self.login("v@gmail.com", "1234")
        page = self.win.centralWidget().pages["Rooms"]
        self.assertEqual([b.text() for b in page.cards.cards[0].findChildren(QPushButton)], ["Checkout"])
        self.assertTrue(page.start_btn.parentWidget().isHidden())          # can't open new cards

    def test_no_permissions_shows_message(self):
        self.db.add_user("Nobody", "no@gmail.com", "1234")
        self.login("no@gmail.com", "1234")
        view: MainView = self.win.centralWidget()
        self.assertEqual(view.pages, {})
        view.tick()
        view.data_changed()

    def test_user_dialog_creates_user_from_template(self):
        self.win.show_main()
        d = dialogs.UserDialog(self.win, self.db)
        self.assertFalse(any(cb.isChecked() for cb in d.checks.values()))       # everything off by default
        d.name.setText("Sara")
        d.email.setText("sara@gmail.com")
        d.password.setText("1234")
        d.template.setCurrentIndex(d.template.findText("Normal user"))
        d.apply_template()
        d.checks["menu.view"].setChecked(True)
        d.save()
        self.assertEqual(d.result(), QDialog.Accepted)
        u = self.db.authenticate("sara@gmail.com", "1234")
        self.assertIn("menu.view", self.db.user_perms(u["id"]))
        self.assertIn("rooms.open", self.db.user_perms(u["id"]))
        settings = self.win.centralWidget().pages["Settings"]
        settings.refresh()
        self.assertEqual(settings.users.rowCount(), 2)
        admin = self.db.list_users()[0]
        dialogs.UserDialog(self.win, self.db, admin).show()                       # admin: no permission grid

    def test_edit_items_and_suggestions(self):
        self.win.show_main()
        view = self.win.centralWidget()
        self.db.add_customer("Ahmed Ali", "01012345678")
        hall = view.pages["Hall"]
        hall.name.setText("ahm")
        hall._suggest("ahm", hall.name._completer, hall.name._completer.model())
        self.assertEqual(hall.name._completer.model().stringList(), ["Ahmed Ali  ·  01012345678"])
        hall.fill_customer(self.db.search_customers("ahm")[0])
        self.assertEqual((hall.phone.text(), hall.name.text()), ("01012345678", "Ahmed Ali"))
        hall.start()
        card = self.db.open_cards("hall")[0]
        tea = next(m for m in self.db.list_menu() if m["name"] == "Tea")
        self.db.add_items(card["id"], [(tea["id"], None, 3)])
        d = dialogs.EditItemsDialog(self.win, self.db, card)
        line = next(iter(d.qty))
        d.change(line, -1)
        d.save()
        self.assertEqual(self.db.card_items(card["id"])[0]["qty"], 2)
        d = dialogs.EditItemsDialog(self.win, self.db, card)
        d.change(next(iter(d.qty)), -10_000)
        d.save()
        self.assertEqual(self.db.card_items(card["id"]), [])

    def test_cash_drawer_and_reset(self):
        self.win.show_main()
        view = self.win.centralWidget()
        view.show_page("Money")
        money = view.pages["Money"]
        d = dialogs.CashDialog(self.win, self.db, date.today(), "opening")
        d.amount.setText("500")
        d.save()
        d = dialogs.CashDialog(self.win, self.db, date.today(), "add")
        d.amount.setText("50")
        d.save()
        view.data_changed()
        last = money.cash_table.rowCount() - 1
        self.assertEqual(money.cash_table.item(last, 0).text(), "Cash in drawer now")
        self.assertEqual(money.cash_table.item(last, 1).text(), "550 EGP")
        self.assertEqual(money.kpi["income"].text(), "0 EGP")
        r = dialogs.ResetDialog(self.win, self.db)
        r.p1.setText("admin")
        r.p2.setText("admin")
        r.save()
        self.assertNotEqual(r.result(), QDialog.Accepted)          # RESET not typed
        r.word.setText("RESET")
        r.save()
        self.assertEqual(r.result(), QDialog.Accepted)
        self.assertEqual(self.db.cash_drawer(date.today())["drawer"], 0)
        with mock.patch.object(dialogs.ChangePasswordDialog, "exec", return_value=QDialog.Rejected) as ex:
            view.change_password()
            ex.assert_called_once()


if __name__ == "__main__":
    unittest.main()
