"""All popups: confirm, discount, room, menu item, payment account, checkout, expense, menu cart, report."""
from __future__ import annotations

import calendar
import os
from datetime import date, datetime

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDateEdit, QDialog, QFileDialog, QFrame,
                               QGridLayout, QScrollArea, QStackedWidget, QWidget)

import pricing
from config import CURRENCY
from db import SIZES, BookingError, Database, PaymentError, account_label, item_label
from pricing import fmt_duration, fmt_money

from .widgets import (Table, button, caps, clear_layout, divider, field, hbox, label, line_edit, money, panel,
                      parse_number, vbox)


class Dialog(QDialog):
    """Modal popup with a title, a body and Cancel / OK buttons at the bottom."""

    def __init__(self, parent, title: str, width: int = 440, subtitle: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        self.outer = vbox(self, margins=(28, 24, 28, 22), spacing=14)
        head = vbox(spacing=2)
        head.addWidget(label(title, "H2"))
        if subtitle:
            head.addWidget(label(subtitle, "Sub", wrap=True))
        self.outer.addLayout(head)
        self.body = vbox(spacing=12)
        self.outer.addLayout(self.body, 1)
        self.error = label("", "Error", wrap=True)
        self.error.hide()
        self.outer.addWidget(self.error)
        self.button_row = hbox(spacing=10)
        self.button_row.addStretch(1)
        self.outer.addLayout(self.button_row)

    def buttons(self, ok_text: str, on_ok, cancel_text: str | None = "Cancel", danger: bool = False):
        if cancel_text:
            self.button_row.addWidget(button(cancel_text, self.reject, width=110))
        ok = button(ok_text, on_ok, "Danger" if danger else "Primary")
        ok.setMinimumWidth(140)
        ok.setDefault(True)
        self.button_row.addWidget(ok)
        return ok

    def show_error(self, msg: str):
        self.error.setText(msg)
        self.error.show()


def confirm(parent, title: str, message: str, ok_text: str, danger: bool = True) -> bool:
    d = Dialog(parent, title, 420)
    d.body.addWidget(label(message, wrap=True))
    d.buttons(ok_text, d.accept, danger=danger)
    return d.exec() == QDialog.Accepted


def alert(parent, title: str, message: str) -> None:
    d = Dialog(parent, title, 420)
    d.body.addWidget(label(message, wrap=True))
    d.buttons("OK", d.accept, cancel_text=None)
    d.exec()


# ------------------------------------------------------------------------- simple forms
class RoomDialog(Dialog):
    def __init__(self, parent, db: Database, room=None):
        super().__init__(parent, "Edit room" if room else "New room", 420,
                         "A new price applies to new bookings only.")
        self.db, self.room = db, room
        self.name = line_edit("e.g. Meeting Room A")
        self.price = line_edit("e.g. 120")
        self.body.addWidget(field("Room name", self.name))
        self.body.addWidget(field(f"Price per hour ({CURRENCY})", self.price))
        if room:
            self.name.setText(room["name"])
            self.price.setText(fmt_money(room["price_per_hour"]).replace(",", ""))
        self.buttons("Save", self.save)

    def save(self):
        try:
            price = parse_number(self.price.text())
            if self.room:
                self.db.update_room(self.room["id"], self.name.text(), price)
            else:
                self.db.add_room(self.name.text(), price)
        except ValueError:
            return self.show_error("Price must be a number.")
        except BookingError as e:
            return self.show_error(str(e))
        self.accept()


class AccountDialog(Dialog):
    def __init__(self, parent, db: Database, account=None):
        super().__init__(parent, "Edit payment account" if account else "New payment account", 420,
                         "Checkout and expenses can use this account.")
        self.db, self.account = db, account
        row = hbox(spacing=8)
        self.group = QButtonGroup(self)
        for i, (code, name) in enumerate((("vodafone", "Vodafone Cash"), ("instapay", "InstaPay"))):
            b = button(name, kind="Chip")
            b.setCheckable(True)
            b.setProperty("code", code)
            self.group.addButton(b)
            row.addWidget(b)
            if (account and account["method"] == code) or (not account and i == 0):
                b.setChecked(True)
        row.addStretch(1)
        w = QWidget()
        w.setLayout(row)
        self.body.addWidget(field("Type", w))
        self.number = line_edit("e.g. 0100 123 4567  or  name@instapay")
        if account:
            self.number.setText(account["number"] or "")
        self.body.addWidget(field("Number / address", self.number))
        self.buttons("Save", self.save)

    def save(self):
        method = self.group.checkedButton().property("code")
        try:
            if self.account:
                self.db.update_account(self.account["id"], method, self.number.text())
            else:
                self.db.add_account(method, self.number.text())
        except BookingError as e:
            return self.show_error(str(e))
        self.accept()


class MenuItemDialog(Dialog):
    def __init__(self, parent, db: Database, item=None, category: str | None = None):
        super().__init__(parent, "Edit menu item" if item else "New menu item", 460,
                         "One price, or a price for each size it's sold in.")
        self.db, self.item = db, item
        self.name = line_edit("e.g. Latte")
        self.body.addWidget(field("Name", self.name))
        self.cat = QComboBox()
        self.cat.setEditable(True)
        self.cat.addItems(db.menu_categories() or ["Hot Drinks"])
        self.cat.setCurrentText(item["category"] if item else (category or self.cat.itemText(0)))
        self.body.addWidget(field("Category (pick or type a new one)", self.cat))
        self.sized = QCheckBox("Sold in sizes (Small / Medium / Large)")
        self.body.addWidget(self.sized)
        self.stack = QStackedWidget()
        single = QWidget()
        sl = vbox(single)
        self.price = line_edit("e.g. 40")
        sl.addWidget(field(f"Price ({CURRENCY})", self.price))
        sl.addStretch(1)
        sizes = QWidget()
        g = QGridLayout(sizes)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(10)
        self.size_edits = {}
        for i, (code, name, _) in enumerate(SIZES):
            e = line_edit("—")
            self.size_edits[code] = e
            g.addWidget(field(f"{name} ({CURRENCY})", e), 0, i)
        g.addWidget(label("Fill the sizes it comes in (at least two). Leave the others empty.", "Sub", wrap=True),
                    1, 0, 1, 3)
        self.stack.addWidget(single)
        self.stack.addWidget(sizes)
        self.body.addWidget(self.stack)
        self.sized.toggled.connect(lambda on: self.stack.setCurrentIndex(1 if on else 0))
        if item:
            self.name.setText(item["name"])
            prices = Database.item_prices(item)
            if None in prices:
                self.price.setText(fmt_money(prices[None]).replace(",", ""))
            else:
                self.sized.setChecked(True)
                for code, p in prices.items():
                    self.size_edits[code].setText(fmt_money(p).replace(",", ""))
        self.buttons("Save", self.save)

    def save(self):
        if self.sized.isChecked():
            price, sizes = None, {c: e.text() for c, e in self.size_edits.items()}
        else:
            price, sizes = self.price.text(), None
        try:
            if self.item:
                self.db.update_menu_item(self.item["id"], self.name.text(), self.cat.currentText(), price, sizes)
            else:
                self.db.add_menu_item(self.name.text(), self.cat.currentText(), price, sizes)
        except BookingError as e:
            return self.show_error(str(e))
        self.accept()


class DiscountDialog(Dialog):
    LABELS = {pricing.DISCOUNT_NONE: "No discount", pricing.DISCOUNT_EGP: f"{CURRENCY} off / hour",
              pricing.DISCOUNT_PCT: "% off"}

    def __init__(self, parent, db: Database, card):
        super().__init__(parent, "Room discount", 440,
                         f"Room price: {money(card['rate'])} per hour. Drinks are never discounted.")
        self.db, self.card = db, card
        row = hbox(spacing=8)
        self.group = QButtonGroup(self)
        for code, text in self.LABELS.items():
            b = button(text, kind="Chip")
            b.setCheckable(True)
            b.setProperty("code", code)
            b.setChecked(code == card["discount_type"])
            b.toggled.connect(self.preview)
            self.group.addButton(b)
            row.addWidget(b)
        row.addStretch(1)
        w = QWidget()
        w.setLayout(row)
        self.body.addWidget(w)
        self.value = line_edit("e.g. 20")
        if card["discount_type"] != pricing.DISCOUNT_NONE:
            self.value.setText(fmt_money(card["discount_value"]))
        self.value.textChanged.connect(self.preview)
        self.body.addWidget(field("Value", self.value))
        self.result = label("", "H3")
        self.body.addWidget(self.result)
        self.buttons("Save", self.save)
        self.preview()

    def kind(self) -> str:
        return self.group.checkedButton().property("code")

    def preview(self):
        kind = self.kind()
        self.value.setEnabled(kind != pricing.DISCOUNT_NONE)
        try:
            v = parse_number(self.value.text()) or 0
        except ValueError:
            return self.result.setText("")
        self.result.setText(f"New price: {money(pricing.discounted_rate(self.card['rate'], kind, v))} per hour")

    def save(self):
        kind = self.kind()
        try:
            value = 0 if kind == pricing.DISCOUNT_NONE else parse_number(self.value.text())
            if value is None:
                raise BookingError("Enter the discount value.")
            self.db.set_discount(self.card["id"], kind, value)
        except ValueError:
            return self.show_error("Discount must be a number.")
        except BookingError as e:
            return self.show_error(str(e))
        self.accept()


# ------------------------------------------------------------------------- payments
class PaymentPicker(QFrame):
    """Tick the account(s) used. One → it takes the whole amount. Two or more → type each amount;
    they must add up exactly to the total (checked again in db._check_payments)."""

    def __init__(self, db: Database, get_total, existing=None):
        super().__init__()
        self.setObjectName("Panel")
        self.get_total = get_total
        self.rows: dict[int, tuple[QCheckBox, object]] = {}
        lay = vbox(self, margins=(14, 10, 14, 12), spacing=2)
        existing = {p["account_id"]: p["amount"] for p in (existing or [])}
        for acc in db.list_accounts():
            row = hbox(spacing=10)
            box = QCheckBox(account_label(acc))
            box.setChecked(acc["id"] in existing)
            amount = line_edit("", width=110, align_right=True)
            if acc["id"] in existing and len(existing) > 1:
                amount.setText(fmt_money(existing[acc["id"]]).replace(",", ""))
            box.toggled.connect(lambda _=None: self.update_state())
            amount.textEdited.connect(lambda _=None: self.update_state(typing=True))
            row.addWidget(box, 1)
            row.addWidget(amount)
            lay.addLayout(row)
            self.rows[acc["id"]] = (box, amount)
        lay.addSpacing(6)
        lay.addWidget(divider())
        self.status = label("", "Sub", wrap=True)
        lay.addWidget(self.status)
        self.update_state()

    def checked(self) -> list[int]:
        return [aid for aid, (box, _) in self.rows.items() if box.isChecked()]

    def total(self):
        try:
            return self.get_total()
        except (TypeError, ValueError):
            return None

    def update_state(self, typing: bool = False):
        total, checked = self.total(), self.checked()
        for aid, (box, amount) in self.rows.items():
            if box.isChecked() and len(checked) == 1:
                amount.setText(fmt_money(total).replace(",", "") if total else "")
                amount.setEnabled(False)
            elif box.isChecked():
                if not typing and not amount.isEnabled():
                    amount.clear()
                amount.setEnabled(True)
            else:
                amount.clear()
                amount.setEnabled(False)
        if not checked:
            self._status("Tick Cash, Vodafone Cash or InstaPay (required).", "Sub")
        elif len(checked) == 1:
            self._status("The whole amount is paid with this account.", "Sub")
        else:
            paid = 0.0
            for aid in checked:
                try:
                    paid += parse_number(self.rows[aid][1].text()) or 0
                except ValueError:
                    pass
            if total is None:
                return self._status(f"Entered {money(paid)}", "Sub")
            left = round(total - paid, 2)
            if abs(left) < 0.005:
                self._status(f"✓  {money(paid)} — matches the total exactly.", "Good")
            elif left > 0:
                self._status(f"Entered {money(paid)} · {money(left)} still missing.", "Bad")
            else:
                self._status(f"Entered {money(paid)} · {money(-left)} too much.", "Bad")

    def _status(self, text: str, name: str):
        self.status.setText(text)
        self.status.setObjectName(name)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def get(self):
        checked = self.checked()
        if len(checked) == 1:
            return [(checked[0], None)]
        return [(aid, self.rows[aid][1].text()) for aid in checked]


class CheckoutDialog(Dialog):
    """The bill (time frozen when opened) and how it was paid. Confirm closes the card."""

    def __init__(self, parent, db: Database, card):
        place = card["room_name"] if card["kind"] == "room" else f"Hall · {card['chairs']} chair(s)"
        super().__init__(parent, f"Checkout · {card['customer_name']}", 900, place)
        self.db, self.card = db, card
        self.end = datetime.now()
        bill = self.bill = db.compute_bill(card, self.end)
        cols = hbox(spacing=20)
        self.body.addLayout(cols, 1)

        left = vbox(spacing=10)
        rows = [("Started", card["start_time"][11:16]), ("Ended", self.end.strftime("%H:%M")),
                ("Time spent", fmt_duration(bill.minutes))]
        if card["kind"] == "room":
            rate = money(bill.rate) + " / hour"
            if card["discount_type"] != pricing.DISCOUNT_NONE:
                d = (f"{fmt_money(card['discount_value'])}%" if card["discount_type"] == pricing.DISCOUNT_PCT
                     else money(card["discount_value"]))
                rate = f"{money(bill.effective_rate)} / hour  (was {fmt_money(bill.rate)}, −{d})"
            rows += [("Hourly price", rate),
                     ("Room time", f"{bill.minutes} min ÷ 60 × {fmt_money(bill.effective_rate)} = {money(bill.time_cost)}")]
        else:
            rows += [("Billed hours", f"{int(bill.billed_hours)} h"),
                     ("Hall time", f"{fmt_money(bill.rate)} × {card['chairs']} chair(s) = {money(bill.time_cost)}")]
        items = db.card_items(card["id"])
        for it in items:
            rows.append((f"   {item_label(it)}", money(it["price"])))
        rows.append(("Drinks & add-ons", money(bill.items_total)))
        if bill.rounding:
            rows.append(("Rounded to the pound", f"{money(bill.exact_total)} → {money(bill.total)}"))
        table = Table(["", ""], money_cols=(1,), row_height=30)
        table.horizontalHeader().setVisible(False)
        table.set_rows([[k, v] for k, v in rows])
        box = panel()
        bl = vbox(box, margins=(6, 6, 6, 6))
        bl.addWidget(table)
        left.addWidget(box, 1)
        bar = panel("TotalBar")
        tl = hbox(bar, margins=(16, 12, 16, 12))
        tl.addWidget(label("TOTAL", "H3"))
        tl.addStretch(1)
        tl.addWidget(label(money(bill.total), "Big"))
        left.addWidget(bar)
        cols.addLayout(left, 11)

        right = vbox(spacing=8)
        right.addWidget(label("Payment", "H3"))
        right.addWidget(label("One account pays the whole bill. With more than one, type each amount — they must "
                              "add up exactly to the total.", "Sub", wrap=True))
        self.picker = PaymentPicker(db, lambda: self.bill.total)
        right.addWidget(self.picker)
        right.addStretch(1)
        cols.addLayout(right, 9)
        self.setMinimumHeight(min(720, 420 + 30 * len(items)))
        self.buttons("Confirm & close bill", self.save, cancel_text="Back")

    def save(self):
        try:
            self.db.close_card(self.card["id"], self.end, payments=self.picker.get())
        except PaymentError as e:
            alert(self, "Payment doesn't match", str(e))
            return
        except BookingError as e:
            return self.show_error(str(e))
        self.accept()


class ExpenseDialog(Dialog):
    def __init__(self, parent, db: Database, expense=None):
        super().__init__(parent, "Edit expense" if expense else "New expense", 480,
                         "Choose which account the money came from.")
        self.db, self.expense = db, expense
        self.desc = line_edit("e.g. Milk")
        self.amount = line_edit("e.g. 120")
        self.body.addWidget(field("What was it for", self.desc))
        self.body.addWidget(field(f"Amount ({CURRENCY})", self.amount))
        if expense:
            self.desc.setText(expense["description"])
            self.amount.setText(fmt_money(expense["amount"]).replace(",", ""))
        self.picker = PaymentPicker(db, lambda: parse_number(self.amount.text()),
                                    existing=db.payments_for("expense", expense["id"]) if expense else None)
        self.body.addWidget(field("Paid from (required)", self.picker))
        self.amount.textEdited.connect(lambda _: self.picker.update_state())
        self.buttons("Save", self.save)

    def save(self):
        try:
            amount = parse_number(self.amount.text())
            if self.expense:
                self.db.update_expense(self.expense["id"], self.desc.text(), amount, payments=self.picker.get())
            else:
                self.db.add_expense(self.desc.text(), amount, payments=self.picker.get())
        except ValueError:
            return self.show_error("Amount must be a number.")
        except PaymentError as e:
            alert(self, "Payment doesn't match", str(e))
            return
        except BookingError as e:
            return self.show_error(str(e))
        self.accept()


# ------------------------------------------------------------------------- menu cart
class MenuPickerDialog(Dialog):
    """Add drinks / food / add-ons from the menu: + puts one in the cart, − takes one out.
    Review shows the cart in a small window; Confirm there adds everything to the card."""

    def __init__(self, parent, db: Database, card):
        place = card["room_name"] if card["kind"] == "room" else f"Hall · {card['chairs']} chair(s)"
        super().__init__(parent, "Add from menu", 1040, f"{card['customer_name']}  ·  {place}")
        self.setMinimumHeight(680)
        self.db, self.card = db, card
        self.cart: dict[tuple[int, str | None], int] = {}
        self.menu = {r["id"]: r for r in db.list_menu()}
        self.counts: dict[tuple[int, str | None], object] = {}

        top = hbox(spacing=8)
        self.chips = QButtonGroup(self)
        for cat in ["All"] + db.menu_categories():
            b = button(cat, kind="Chip")
            b.setCheckable(True)
            b.setChecked(cat == "All")
            self.chips.addButton(b)
            top.addWidget(b)
        self.chips.buttonClicked.connect(lambda _: self.build_list())
        top.addStretch(1)
        self.search = line_edit("Search…", width=200)
        self.search.textChanged.connect(lambda _: self.build_list())
        top.addWidget(self.search)
        self.body.addLayout(top)

        cols = hbox(spacing=16)
        self.body.addLayout(cols, 1)
        listbox = panel()
        ll = vbox(listbox, margins=(4, 4, 4, 4))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.list_host = QWidget()
        self.list_lay = vbox(self.list_host, margins=(14, 8, 14, 8), spacing=0)
        self.scroll.setWidget(self.list_host)
        ll.addWidget(self.scroll)
        cols.addWidget(listbox, 1)

        side = panel()
        side.setFixedWidth(300)
        sl = vbox(side, margins=(16, 14, 16, 16), spacing=8)
        sl.addWidget(label("Cart", "H3"))
        self.cart_table = Table(["Item", "Total"], money_cols=(1,), row_height=30)
        sl.addWidget(self.cart_table, 1)
        self.cart_total = label("", "Big")
        self.cart_count = label("", "Sub")
        sl.addWidget(self.cart_total)
        sl.addWidget(self.cart_count)
        self.clear_btn = button("Clear cart", self.clear_cart)
        sl.addWidget(self.clear_btn)
        cols.addWidget(side)

        self.review_btn = self.buttons("Review & confirm", self.review, cancel_text="Close")
        self.build_list()
        self.update_cart()

    def build_list(self):
        self.list_host.setUpdatesEnabled(False)
        clear_layout(self.list_lay)
        self.counts = {}
        cat = self.chips.checkedButton().text()
        q = self.search.text().strip().lower()
        rows = [m for m in self.menu.values()
                if (cat == "All" or m["category"] == cat) and (not q or q in m["name"].lower())]
        if not rows:
            self.list_lay.addWidget(label("No items here. Add items in the Menu page.", "Sub"))
        last = None
        for m in rows:
            if m["category"] != last:
                last = m["category"]
                head = caps(last)
                head.setContentsMargins(0, 14 if self.list_lay.count() else 4, 0, 6)
                self.list_lay.addWidget(head)
            row = QWidget()
            rl = hbox(row, margins=(0, 6, 0, 6), spacing=10)
            rl.addWidget(label(m["name"], "H3"), 1)
            for size, price in Database.item_prices(m).items():
                rl.addWidget(self._counter(m["id"], size, price))
            self.list_lay.addWidget(row)
            self.list_lay.addWidget(divider())
        self.list_lay.addStretch(1)
        self.list_host.setUpdatesEnabled(True)

    def _counter(self, item_id, size, price):
        box = panel("Soft")
        lay = hbox(box, margins=(10, 4, 4, 4), spacing=6)
        lay.addWidget(label((f"{size}  " if size else "") + fmt_money(price)))
        lay.addWidget(button("−", lambda: self.change(item_id, size, -1), "Step"))
        n = label("", "H3")
        n.setFixedWidth(22)
        n.setAlignment(Qt.AlignCenter)
        lay.addWidget(n)
        lay.addWidget(button("+", lambda: self.change(item_id, size, 1), "StepPlus"))
        self.counts[(item_id, size)] = n
        self._show(item_id, size)
        return box

    def _show(self, item_id, size):
        n = self.counts.get((item_id, size))
        if n is not None:
            q = self.cart.get((item_id, size), 0)
            n.setText(str(q) if q else "·")

    def change(self, item_id, size, delta):
        key = (item_id, size)
        q = max(0, self.cart.get(key, 0) + delta)
        if q:
            self.cart[key] = q
        else:
            self.cart.pop(key, None)
        self._show(item_id, size)
        self.update_cart()

    def clear_cart(self):
        keys = list(self.cart)
        self.cart.clear()
        for k in keys:
            self._show(*k)
        self.update_cart()

    def lines(self):
        out = []
        for (item_id, size), qty in self.cart.items():
            m = self.menu[item_id]
            unit = Database.item_prices(m)[size]
            out.append((f"{qty} × {m['name']}" + (f" ({size})" if size else ""), qty, unit * qty))
        return out

    def update_cart(self):
        lines = self.lines()
        self.cart_table.set_rows([[l, fmt_money(t)] for l, _, t in lines])
        count = sum(self.cart.values())
        self.cart_total.setText(money(sum(t for _, _, t in lines)))
        self.cart_count.setText(f"{count} item(s)" if count else "Empty — press + next to an item.")
        self.review_btn.setEnabled(bool(lines))
        self.clear_btn.setEnabled(bool(lines))

    def review(self):
        if not self.cart:
            return
        lines = self.lines()
        d = Dialog(self, "Add these to the card?", 420)
        t = Table(["Item", "Total"], money_cols=(1,), row_height=30)
        t.set_rows([[l, money(tot)] for l, _, tot in lines])
        t.fit_height()
        d.body.addWidget(t)
        bar = panel("TotalBar")
        bl = hbox(bar, margins=(14, 10, 14, 10))
        bl.addWidget(label(f"TOTAL · {sum(q for _, q, _ in lines)} item(s)", "H3"))
        bl.addStretch(1)
        bl.addWidget(label(money(sum(t for _, _, t in lines)), "Big"))
        d.body.addWidget(bar)

        def ok():
            try:
                self.db.add_items(self.card["id"], [(i, s, q) for (i, s), q in self.cart.items()])
            except BookingError as e:
                return d.show_error(str(e))
            d.accept()

        d.buttons("Confirm", ok, cancel_text="Back")
        if d.exec() == QDialog.Accepted:
            self.accept()


# ------------------------------------------------------------------------- report
def qdate(d: date) -> QDate:
    return QDate(d.year, d.month, d.day)


def pydate(q: QDate) -> date:
    return date(q.year(), q.month(), q.day())


class ReportDialog(Dialog):
    """Excel report for a date range or a whole month (one sheet per day + a Total sheet)."""

    def __init__(self, parent, db: Database):
        super().__init__(parent, "Download money report", 480,
                         "Excel file: one sheet per day, then a Total sheet with payment analytics.")
        self.db = db
        self.first = db.first_data_date()
        today = date.today()
        row = hbox(spacing=8)
        self.mode = QButtonGroup(self)
        for i, t in enumerate(("Date range", "Month")):
            b = button(t, kind="Chip")
            b.setCheckable(True)
            b.setChecked(i == 0)
            self.mode.addButton(b, i)
            row.addWidget(b)
        row.addStretch(1)
        self.body.addLayout(row)
        self.stack = QStackedWidget()
        rng = QWidget()
        rl = QGridLayout(rng)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setHorizontalSpacing(12)
        self.start = QDateEdit()
        self.end = QDateEdit()
        for e, d in ((self.start, self.first or today), (self.end, today)):
            e.setCalendarPopup(True)
            e.setDisplayFormat("dd MMM yyyy")
            e.setDate(qdate(d))
            if self.first:
                e.setMinimumDate(qdate(self.first))
            e.setMaximumDate(qdate(today))
        rl.addWidget(field("From", self.start), 0, 0)
        rl.addWidget(field("To", self.end), 0, 1)
        hint = (f"Data starts on {self.first:%d %b %Y}. Dates must be between that day and today."
                if self.first else "There's no data yet.")
        rl.addWidget(label(hint, "Sub", wrap=True), 1, 0, 1, 2)
        mon = QWidget()
        ml = QGridLayout(mon)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setHorizontalSpacing(12)
        self.month = QComboBox()
        self.month.addItems(list(calendar.month_name)[1:])
        self.month.setCurrentIndex(today.month - 1)
        years = list(range(today.year, (self.first or today).year - 1, -1))
        self.year = QComboBox()
        self.year.addItems([str(y) for y in years])
        ml.addWidget(field("Month", self.month), 0, 0)
        ml.addWidget(field("Year", self.year), 0, 1)
        ml.addWidget(label("The current month runs up to today.", "Sub"), 1, 0, 1, 2)
        self.stack.addWidget(rng)
        self.stack.addWidget(mon)
        self.body.addWidget(self.stack)
        self.mode.idClicked.connect(lambda i: (self.stack.setCurrentIndex(i), self.error.hide()))
        self.buttons("Download", self.download)

    def period(self) -> tuple[date, date]:
        today = date.today()
        if self.mode.checkedId() == 1:
            y, m = int(self.year.currentText()), self.month.currentIndex() + 1
            if self.first is None or (y, m) < (self.first.year, self.first.month) or (y, m) > (today.year, today.month):
                raise ValueError(f"There's no data for {self.month.currentText()} {y}.")
            start = max(date(y, m, 1), self.first)
            end = min(date(y, m, calendar.monthrange(y, m)[1]), today)
        else:
            start, end = pydate(self.start.date()), pydate(self.end.date())
        error = self.db.check_period(start, end, today)
        if error:
            raise ValueError(error)
        return start, end

    def download(self):
        try:
            start, end = self.period()
        except ValueError as e:
            return self.show_error(str(e))
        import report
        path, _ = QFileDialog.getSaveFileName(self, "Save report",
                                              os.path.join(os.path.expanduser("~"), "Documents",
                                                           report.default_filename(start, end)),
                                              "Excel workbook (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            report.build_report(self.db, start, end, path)
        except PermissionError:
            return self.show_error("Couldn't save. Is that file open in Excel? Close it and try again.")
        except (OSError, ValueError) as e:
            return self.show_error(str(e))
        self.accept()
        alert(self.parent(), "Report saved", f"{os.path.basename(path)}\n{start:%d %b %Y} to {end:%d %b %Y}")
