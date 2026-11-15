"""The five screens: Rooms, Hall, Menu, Money and Settings."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import QStringListModel, Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (QComboBox, QCompleter, QDialog, QFrame, QGridLayout, QScrollArea, QSizePolicy,
                               QWidget)

import pricing
from config import CURRENCY
from db import BookingError, Database, NameMismatch, account_label, card_perm, fmt_qty, item_label
from pricing import fmt_duration, fmt_money

from .dialogs import (AccountDialog, CashDialog, CheckoutDialog, DiscountDialog, EditItemsDialog, ExpenseDialog,
                      MenuItemDialog, MenuPickerDialog, ReportDialog, ResetDialog, RoomDialog, UserDialog, alert,
                      confirm, name_mismatch_popup)
from .widgets import (Table, button, caps, divider, field, hbox, label, line_edit, money, panel,
                      vbox)


def scroll(widget: QWidget) -> QScrollArea:
    s = QScrollArea()
    s.setWidgetResizable(True)
    s.setFrameShape(QFrame.NoFrame)
    s.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    s.setWidget(widget)
    return s


def actions(*buttons) -> QWidget:
    """A row of small buttons for a table cell."""
    w = QWidget()
    lay = hbox(w, margins=(0, 3, 8, 3), spacing=6)
    lay.addStretch(1)
    for b in buttons:
        b.setFixedHeight(30)
        lay.addWidget(b)
    return w


def small(text, on_click, danger=False):
    b = button(text, on_click, "Danger" if danger else None)
    b.setStyleSheet("padding: 3px 10px;")
    f = b.font()
    f.setWeight(QFont.DemiBold)
    b.setMinimumWidth(QFontMetrics(f).horizontalAdvance(text) + 28)   # bold text never gets clipped
    return b


class Page(QWidget):
    """A screen with a title row. Subclasses fill `self.body`."""

    def __init__(self, app, title: str, subtitle: str = ""):
        super().__init__()
        self.setObjectName("Page")
        self.app, self.db = app, app.db
        self.outer = vbox(self, margins=(32, 26, 32, 24), spacing=18)
        head = hbox(spacing=10)
        titles = vbox(spacing=2)
        titles.addWidget(label(title, "H1"))
        self.subtitle = label(subtitle, "Sub", wrap=True)
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        self.toolbar = hbox(spacing=8)
        head.addLayout(self.toolbar)
        self.outer.addLayout(head)
        self.body = vbox(spacing=16)
        self.outer.addLayout(self.body, 1)

    def refresh(self):
        pass

    def tick(self):
        pass


# ============================================================================ booking cards
class CardWidget(QFrame):
    """One open booking: live timer, running cost, items and actions."""

    def __init__(self, page: "BookingPage", card):
        super().__init__()
        self.setObjectName("Card")
        self.page, self.db, self.card = page, page.db, card
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        lay = vbox(self, margins=(8, 8, 8, 12), spacing=0)

        head = panel("CardHead")
        hl = vbox(head, margins=(14, 10, 14, 10), spacing=1)
        place = card["room_name"] if card["kind"] == "room" else f"Hall · {card['chairs']} chair(s)"
        hl.addWidget(caps(place))
        hl.addWidget(label(card["customer_name"], "H3"))
        sub = f"Started {card['start_time'][11:16]}" + (f"  ·  {card['phone']}" if card["phone"] else "")
        hl.addWidget(label(sub, "Caps"))
        lay.addWidget(head)

        inner = vbox(margins=(8, 12, 8, 0), spacing=6)
        lay.addLayout(inner)
        self.timer = label("00:00:00", "Timer")
        inner.addWidget(self.timer)
        self.rate = label("", "Sub")
        inner.addWidget(self.rate)

        items = self.db.card_items(card["id"])
        kind = card["kind"]
        can_items = self.db.can(card_perm(kind, "items"))
        if items:
            box = panel("Soft")
            bl = vbox(box, margins=(10, 6, 4, 6), spacing=0)
            for it in items:
                row = hbox(spacing=4)
                row.addWidget(label(item_label(it)), 1)
                row.addWidget(label(fmt_money(it["price"])))
                if can_items:
                    x = button("×", lambda _=False, iid=it["id"]: self.remove_item(iid), "Link")
                    x.setToolTip("Remove this item")
                    row.addWidget(x)
                bl.addLayout(row)
            inner.addSpacing(4)
            inner.addWidget(box)

        inner.addSpacing(4)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)
        self.time_v, self.items_v = label(""), label("")
        for r, (k, v) in enumerate((("Time", self.time_v), ("Items", self.items_v))):
            grid.addWidget(label(k, "Sub"), r, 0)
            v.setAlignment(Qt.AlignRight)
            grid.addWidget(v, r, 1)
        inner.addLayout(grid)
        inner.addWidget(divider())
        tot = hbox()
        tot.addWidget(label("Total", "H3"))
        tot.addStretch(1)
        self.total = label("", "Money")
        tot.addWidget(self.total)
        inner.addLayout(tot)
        inner.addSpacing(6)

        row = hbox(spacing=6)
        if can_items:
            row.addWidget(button("+ Items", self.add_items), 1)
            if items:
                row.addWidget(button("Edit items", self.edit_items), 1)
        if kind == "room" and self.db.can("rooms.discount"):
            row.addWidget(button("Discount", self.discount), 1)
        if self.db.can(card_perm(kind, "cancel")):
            row.addWidget(button("Cancel", self.cancel, "Danger"), 1)
        if row.count():
            inner.addLayout(row)
        if self.db.can(card_perm(kind, "checkout")):
            inner.addWidget(button("Checkout", self.checkout, "Primary"))
        self.tick()

    def tick(self):
        now = datetime.now()
        secs = max(0, int((now - datetime.strptime(self.card["start_time"], "%Y-%m-%d %H:%M:%S")).total_seconds()))
        self.timer.setText(f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}")
        bill = self.db.compute_bill(self.card, now)
        c = self.card
        if c["kind"] == "room":
            rate = f"{fmt_money(bill.effective_rate)} {CURRENCY} / hour"
            if c["discount_type"] == pricing.DISCOUNT_EGP:
                rate += f"  ·  −{fmt_money(c['discount_value'])} {CURRENCY} discount"
            elif c["discount_type"] == pricing.DISCOUNT_PCT:
                rate += f"  ·  −{fmt_money(c['discount_value'])}% discount"
        else:
            rate = f"Billed so far: {int(bill.billed_hours)} h × {c['chairs']} chair(s)"
        self.rate.setText(rate)
        self.time_v.setText(money(bill.time_cost))
        self.items_v.setText(money(bill.items_total))
        self.total.setText(money(bill.total))

    def _done(self, dlg):
        if dlg.exec() == QDialog.Accepted:
            self.page.app.data_changed()

    def add_items(self):
        picker = MenuPickerDialog(self.window(), self.db, self.card)
        self._done(picker)
        if picker.goto_stock is not None:          # "Open Inventory" from an out-of-stock popup
            self.page.app.open_restock(picker.goto_stock)

    def edit_items(self):
        self._done(EditItemsDialog(self.window(), self.db, self.card))

    def discount(self):
        self._done(DiscountDialog(self.window(), self.db, self.card))

    def checkout(self):
        self._done(CheckoutDialog(self.window(), self.db, self.card))

    def remove_item(self, item_id):
        try:
            self.db.delete_item(item_id)
        except BookingError as e:
            alert(self.window(), "Can't remove item", str(e))
        self.page.app.data_changed()

    def cancel(self):
        if confirm(self.window(), "Cancel booking?",
                   f"{self.card['customer_name']}'s card will be removed without a bill. "
                   "Use this only for a booking made by mistake.", "Cancel booking"):
            self.db.cancel_card(self.card["id"])
            self.page.app.data_changed()


class CardGrid(QWidget):
    """Cards in as many columns as fit (each at least 290 px wide)."""

    MIN_W = 290

    def __init__(self):
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 6, 0)
        self.grid.setSpacing(14)
        self.cards: list[QWidget] = []
        self._cols = 0

    def set_cards(self, cards):
        for w in self.cards:
            self.grid.removeWidget(w)
            w.deleteLater()
        self.cards = list(cards)
        self._cols = 0
        self._layout()

    def _layout(self):
        cols = max(1, min(4, (self.width() + 14) // (self.MIN_W + 14)))
        if cols == self._cols:
            return
        self._cols = cols
        for w in self.cards:
            self.grid.removeWidget(w)
        for c in range(6):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)
        for i, w in enumerate(self.cards):
            self.grid.addWidget(w, i // cols, i % cols, Qt.AlignTop)
        self.grid.setRowStretch(len(self.cards) // cols + 1, 1)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout()


class BookingPage(Page):
    """Form on the left, live cards on the right."""

    kind = "room"
    form_title = ""

    def __init__(self, app, title, subtitle):
        super().__init__(app, title, subtitle)
        row = hbox(spacing=20)
        self.body.addLayout(row, 1)

        form = panel()
        form.setFixedWidth(310)
        fl = vbox(form, margins=(22, 20, 22, 20), spacing=12)
        fl.addWidget(label(self.form_title, "H2"))
        self.phone = line_edit("Required, e.g. 01001234567")
        self.name = line_edit("Required")
        fl.addWidget(field("Phone", self.phone))
        self.who = label("", "Sub", wrap=True)
        self.who.hide()
        fl.addWidget(self.who)
        fl.addWidget(field("Customer name", self.name))
        self.phone.textChanged.connect(self.lookup)
        self._suggest_setup()
        self.build_extra(fl)
        self.error = label("", "Error", wrap=True)
        self.error.hide()
        fl.addWidget(self.error)
        self.start_btn = button("Start & create card", self.start, "Primary")
        self.start_btn.setMinimumHeight(38)
        fl.addWidget(self.start_btn)
        fl.addWidget(divider())
        self.info = label("", "Sub", wrap=True)
        fl.addWidget(self.info)
        fl.addStretch(1)
        self.phone.returnPressed.connect(self.name.setFocus)
        self.name.returnPressed.connect(self.start)
        row.addWidget(form)
        if not self.db.can(card_perm(self.kind, "open")):     # may look at the cards but not open new ones
            form.hide()

        right = vbox(spacing=10)
        self.list_title = label("", "H3")
        right.addWidget(self.list_title)
        self.empty = label("No open cards yet. Start one from the form on the left.", "Sub")
        right.addWidget(self.empty)
        self.cards = CardGrid()
        right.addWidget(scroll(self.cards), 1)
        row.addLayout(right, 1)
        self._key = None

    def build_extra(self, layout):
        pass

    # --- name / phone suggestions from the customers already saved
    def _suggest_setup(self):
        self._suggest_rows: dict[str, object] = {}
        self._filling = False
        for edit in (self.phone, self.name):
            model = QStringListModel(self)
            comp = QCompleter(model, self)
            comp.setCompletionMode(QCompleter.UnfilteredPopupCompletion)
            comp.setCaseSensitivity(Qt.CaseInsensitive)
            comp.setWidget(edit)
            comp.activated[str].connect(self._pick_suggestion)
            edit.textEdited.connect(lambda text, c=comp, m=model: self._suggest(text, c, m))
            edit._completer = comp

    def _suggest(self, text, comp, model):
        rows = self.db.search_customers(text)
        self._suggest_rows = {f"{r['name']}  ·  {r['phone']}": r for r in rows}
        model.setStringList(list(self._suggest_rows))
        if rows:
            comp.complete()
        else:
            comp.popup().hide()

    def _pick_suggestion(self, text):
        cust = self._suggest_rows.get(text)
        if cust is None:
            return
        # set both fields after the completer has finished with the line edit
        QTimer.singleShot(0, lambda: self.fill_customer(cust))

    def fill_customer(self, cust):
        self.phone.setText(cust["phone"])        # also shows "Returning customer…"
        self.name.setText(cust["name"])
        self.name.setFocus()

    def lookup(self, text):
        """A known phone fills in the registered name; a new one says it will be saved."""
        cust = self.db.find_customer(text)
        if cust is not None:
            st = self.db.customer_stats(cust["id"])
            last = f" · last {st['last_visit'][:10]}" if st["last_visit"] else ""
            self.name.setText(cust["name"])
            self.who.setText(f"Returning customer · {st['visits']} visit(s){last}")
            self.who.show()
        elif sum(c.isdigit() for c in text) >= 8:
            self.who.setText("New customer · will be saved with this phone")
            self.who.show()
        else:
            self.who.hide()

    def open_card(self):
        raise NotImplementedError

    def start(self):
        try:
            self.open_card()
        except NameMismatch as e:
            if not name_mismatch_popup(self.window(), e.customer):
                return
            self.name.setText(e.customer["name"])
            return self.start()
        except BookingError as e:
            self.fail(str(e))
            return
        self.reset_form()
        self.app.data_changed()

    def fail(self, msg):
        self.error.setText(msg)
        self.error.show()

    def reset_form(self):
        self.name.clear()
        self.phone.clear()
        self.who.hide()
        self.error.hide()

    def refresh(self):
        open_cards = self.db.open_cards(self.kind)
        key = [(tuple(c), tuple(tuple(i) for i in self.db.card_items(c["id"]))) for c in open_cards]
        if key != self._key:            # rebuild cards only when something about them changed
            self._key = key
            self.cards.set_cards([CardWidget(self, c) for c in open_cards])
            self.list_title.setText(f"Open cards  ·  {len(open_cards)}")
            self.empty.setVisible(not open_cards)

    def tick(self):
        for w in self.cards.cards:
            w.tick()


class RoomsPage(BookingPage):
    kind = "room"
    form_title = "Reserve a room"

    def __init__(self, app):
        super().__init__(app, "Rooms", "Private rooms billed by the hour.")

    def build_extra(self, fl):
        self.room = QComboBox()
        fl.addWidget(field("Room", self.room))

    def refresh(self):
        super().refresh()
        free = self.db.free_rooms()
        all_rooms = self.db.list_rooms()
        current = self.room.currentData()
        self.room.blockSignals(True)
        self.room.clear()
        for r in free:
            self.room.addItem(f"{r['name']}  ·  {fmt_money(r['price_per_hour'])} {CURRENCY}/h", r["id"])
        if not free:
            self.room.addItem("No free rooms", None)
        i = self.room.findData(current)
        self.room.setCurrentIndex(max(0, i))
        self.room.blockSignals(False)
        self.room.setEnabled(bool(free))
        self.start_btn.setEnabled(bool(free))
        if not all_rooms:
            self.info.setText("No rooms yet. Add rooms in Settings.")
        else:
            busy = len(all_rooms) - len(free)
            self.info.setText(f"{len(free)} free · {busy} busy out of {len(all_rooms)} room(s). "
                              "Busy rooms are hidden from the list until checkout.")

    def open_card(self):
        room_id = self.room.currentData()
        if room_id is None:
            raise BookingError("Please choose a free room.")
        self.db.open_room_card(self.name.text(), self.phone.text(), room_id)


class HallPage(BookingPage):
    kind = "hall"
    form_title = "Book hall chairs"

    def __init__(self, app):
        super().__init__(app, "Hall", "Shared hall billed per chair per hour.")

    def build_extra(self, fl):
        row = hbox(spacing=6)
        minus = button("−", lambda: self.step(-1))
        plus = button("+", lambda: self.step(1))
        for b in (minus, plus):
            b.setFixedSize(38, 36)
        self.chairs = line_edit()
        self.chairs.setAlignment(Qt.AlignCenter)
        self.chairs.setText("1")
        row.addWidget(minus)
        row.addWidget(self.chairs, 1)
        row.addWidget(plus)
        w = QWidget()
        w.setLayout(row)
        fl.addWidget(field("Number of chairs", w))
        self.free_label = label("", "H3")
        fl.addWidget(self.free_label)

    def step(self, delta):
        try:
            n = int(self.chairs.text())
        except ValueError:
            n = 1
        self.chairs.setText(str(max(1, min(self.db.hall_chairs_total(), n + delta))))

    def refresh(self):
        super().refresh()
        free = self.db.free_chairs()
        self.free_label.setText(f"Free chairs: {free} / {self.db.hall_chairs_total()}")
        self.start_btn.setEnabled(free > 0)
        tiers = self.db.hall_tiers()
        self.info.setText(f"Per chair: {' + '.join(fmt_money(t) for t in tiers)} {CURRENCY} for hours "
                          f"1–{len(tiers)} (max {fmt_money(sum(tiers))}). Minimum 1 hour. Under 30 min extra is "
                          "dropped, 30 min or more counts as a full hour. The whole group starts together.")

    def reset_form(self):
        super().reset_form()
        self.chairs.setText("1")

    def open_card(self):
        self.db.open_hall_card(self.name.text(), self.phone.text(), self.chairs.text())


# ============================================================================ menu
class MenuPage(Page):
    def __init__(self, app):
        super().__init__(app, "Menu", "Cards can only add items from this menu. A new price applies to items "
                                      "added from now on; items already on a card or bill keep their price.")
        self.editable = self.db.can("menu.edit")
        if self.editable:
            self.toolbar.addWidget(button("+ New item", lambda: self.edit(None), "Primary"))
        box = panel()
        bl = vbox(box, margins=(6, 6, 6, 6))
        self.table = Table(["Item", "Category", "Inventory", "Price", ""], money_cols=(3,), stretch=0, row_height=44)
        bl.addWidget(self.table)
        self.body.addWidget(box, 1)
        self._key = None

    def refresh(self):
        items = self.db.list_menu()
        stock = {st["id"]: st for st in self.db.list_stock()}
        key = ([tuple(r) for r in items], [tuple(r) for r in stock.values()])
        if key == self._key:
            return
        self._key = key
        rows = []
        for it in items:
            st = stock.get(it["stock_item_id"])
            per = f"{fmt_qty(it['stock_per_sale'])} × " if st and it["stock_per_sale"] != 1 else ""
            inv = f"{per}{st['name']}  ·  {fmt_qty(st['quantity'])} left" if st else "—"
            prices = Database.item_prices(it)
            if None in prices:
                price = money(prices[None])
            else:
                price = "  ·  ".join(f"{s} {fmt_money(p)}" for s, p in prices.items()) + f" {CURRENCY}"
            rows.append([it["name"], it["category"], inv, price,
                         actions(small("Edit", lambda _=False, i=it: self.edit(i)),
                                 small("Remove", lambda _=False, i=it: self.remove(i), danger=True))
                         if self.editable else ""])
        self.table.set_rows(rows)
        if not items:
            self.table.setRowCount(1)
            self.table.setSpan(0, 0, 1, 5)
            self.table.set_rows([["The menu is empty. Click “+ New item” to add one."]])

    def edit(self, item):
        if MenuItemDialog(self.window(), self.db, item).exec() == QDialog.Accepted:
            self.app.data_changed()

    def remove(self, item):
        if confirm(self.window(), "Remove item?", f"“{item['name']}” will be taken off the menu. Bills that "
                                                 "already have it keep its name and price.", "Remove"):
            self.db.remove_menu_item(item["id"])
            self.app.data_changed()


# ============================================================================ money
class MoneyPage(Page):
    def __init__(self, app):
        super().__init__(app, "Money", "Daily income, expenses and where the money is.")
        db = self.db
        self.day = date.today()
        self.see_in = db.can("money.view_in")
        self.see_out = db.can("money.view_expenses")
        self.past = db.can("money.past_dates")
        self.cash_edit = db.can("money.cash")
        if db.can("money.report"):
            self.toolbar.addWidget(button("Download report", lambda: ReportDialog(self.window(), self.db).exec()))
        if db.can("money.add_expense"):
            self.toolbar.addWidget(button("+ Add expense", self.add_expense, "Primary"))

        content = QWidget()
        content.setObjectName("Content")
        cl = vbox(content, margins=(0, 0, 8, 8), spacing=16)
        self.body.addWidget(scroll(content), 1)

        bar = hbox(spacing=8)
        prev, nxt = button("‹", lambda: self.shift(-1)), button("›", lambda: self.shift(1))
        for b in (prev, nxt):
            b.setFixedSize(36, 34)
            b.setStyleSheet("font-size: 18px; padding: 0 0 3px 0;")
        self.day_label = label("", "H2")
        self.day_label.setMinimumWidth(250)
        self.day_label.setAlignment(Qt.AlignCenter)
        if self.past:
            bar.addWidget(prev)
        bar.addWidget(self.day_label)
        if self.past:
            bar.addWidget(nxt)
            bar.addWidget(button("Today", self.go_today))
        bar.addStretch(1)
        self.running = label("", "Sub")
        bar.addWidget(self.running)
        self.running.setVisible(self.see_in)
        cl.addLayout(bar)

        kpis = hbox(spacing=14)
        self.kpi = {}
        for key, title, dark, show in (("income", "Money in", False, self.see_in),
                                       ("expenses", "Expenses", False, self.see_out),
                                       ("net", "Total now  (in − expenses)", True, self.see_in and self.see_out)):
            box = panel("KpiDark" if dark else "Kpi")
            bl = vbox(box, margins=(20, 16, 20, 16), spacing=4)
            bl.addWidget(caps(title))
            v = label("", "Big")
            bl.addWidget(v)
            self.kpi[key] = v
            box.setVisible(show)
            kpis.addWidget(box, 1)
        cl.addLayout(kpis)

        # --- cash drawer
        self.cash_box = panel()
        cb = vbox(self.cash_box, margins=(18, 14, 18, 12), spacing=8)
        head = hbox(spacing=8)
        tl = vbox(spacing=2)
        tl.addWidget(label("Cash drawer", "H3"))
        tl.addWidget(label("Opening cash and cash added are not income. The day starts at 0 unless you set it.",
                           "Sub", wrap=True))
        head.addLayout(tl, 1)
        self.opening_btn = button("Edit opening cash", self.edit_opening)
        self.add_cash_btn = button("+ Add cash", self.add_cash)
        head.addWidget(self.opening_btn, 0, Qt.AlignTop)
        head.addWidget(self.add_cash_btn, 0, Qt.AlignTop)
        cb.addLayout(head)
        self.cash_table = Table(["Cash", "Amount", ""], money_cols=(1,), row_height=36)
        self.cash_table.horizontalHeader().hide()
        cb.addWidget(self.cash_table)
        self.cash_box.setVisible(self.see_in or self.cash_edit)
        cl.addWidget(self.cash_box)

        cols = hbox(spacing=16)
        left, right = vbox(spacing=16), vbox(spacing=16)
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        cl.addLayout(cols)

        self.breakdown = Table(["Breakdown", "Amount"], money_cols=(1,), row_height=34)
        self.breakdown.horizontalHeader().hide()
        self.accounts = Table(["Account", "In", "Out", "Balance"], money_cols=(1, 2, 3), row_height=34)
        b1 = self._section("Breakdown", self.breakdown)
        b2 = self._section("By payment account", self.accounts)
        b1.setVisible(self.see_in)
        b2.setVisible(self.see_in and self.see_out)
        left.addWidget(b1)
        right.addWidget(b2)
        self.expenses = Table(["Time", "Expense", "Paid from", "By", "Amount", ""], money_cols=(4,), stretch=1,
                              row_height=42)
        self.bills = Table(["Closed", "Customer", "Place", "Paid with", "Duration", "By", "Total"], money_cols=(6,),
                           stretch=1, row_height=36)
        b3 = self._section("Expenses", self.expenses)
        b4 = self._section("Closed bills", self.bills)
        b3.setVisible(self.see_out)
        b4.setVisible(self.see_in)
        cl.addWidget(b3)
        cl.addWidget(b4)
        cl.addStretch(1)
        self._key = None

    @staticmethod
    def _section(title, table):
        box = panel()
        bl = vbox(box, margins=(18, 14, 18, 10), spacing=6)
        bl.addWidget(label(title, "H3"))
        bl.addWidget(table)
        bl.addStretch(1)
        return box

    def shift(self, days):
        if not self.past:
            return
        self.day = min(date.today(), self.day + timedelta(days=days)) if days > 0 else self.day + timedelta(days=days)
        self.refresh()

    def go_today(self):
        self.day = date.today()
        self.refresh()

    def _done(self, dlg):
        if dlg.exec() == QDialog.Accepted:
            self._key = None
            self.app.data_changed()

    def add_expense(self):
        self._done(ExpenseDialog(self.window(), self.db))

    def edit_expense(self, e):
        self._done(ExpenseDialog(self.window(), self.db, e))

    def delete_expense(self, e):
        if confirm(self.window(), "Delete expense?", f"“{e['description']}” ({money(e['amount'])}) will be "
                                                    "removed.", "Delete"):
            self.db.delete_expense(e["id"])
            self._key = None
            self.app.data_changed()

    def edit_opening(self):
        self._done(CashDialog(self.window(), self.db, self.day, "opening",
                              current=self.db.cash_drawer(self.day)["opening"]))

    def add_cash(self):
        self._done(CashDialog(self.window(), self.db, self.day, "add"))

    def edit_cash(self, entry):
        self._done(CashDialog(self.window(), self.db, self.day, "add", entry))

    def delete_cash(self, entry):
        if confirm(self.window(), "Delete added cash?", f"{money(entry['amount'])} added to the drawer will be "
                                                       "removed.", "Delete"):
            self.db.delete_cash(entry["id"])
            self._key = None
            self.app.data_changed()

    def update_running(self):
        n, est = self.db.running_estimate()
        self.running.setText(f"Still running: {n} open card(s), about {money(est)} so far" if n
                             else "No open cards right now")

    def tick(self):
        if not self.past and self.day != date.today():     # past midnight: move to the new day
            self.day = date.today()
            self.refresh()
        self.update_running()

    def _paid(self, kind, ref):
        return " + ".join(p["label"].split(" · ")[0] for p in self.db.payments_for(kind, ref)) or "not recorded"

    def refresh(self):
        if not self.past:
            self.day = date.today()
        d = self.day
        self.day_label.setText(("Today · " if d == date.today() else "") + d.strftime("%a %d %b %Y"))
        self.update_running()
        s = self.db.summary(d, d)
        cash = self.db.cash_drawer(d, s["accounts"])
        key = (d, tuple(tuple(c) for c in s["cards"]), tuple(tuple(e) for e in s["expenses"]),
               tuple(tuple(a.items()) for a in s["accounts"]), cash["opening"],
               tuple(tuple(r) for r in cash["added"]),
               tuple(tuple(p) for p in self.db.conn.execute("SELECT * FROM payments ORDER BY id DESC LIMIT 50")))
        if key == self._key:
            return
        self._key = key
        names = self.db.user_names()
        self.kpi["income"].setText(money(s["income_total"]))
        self.kpi["expenses"].setText(money(s["expenses_total"]))
        self.kpi["net"].setText(money(s["net"]))

        # cash drawer
        can_cash = self.cash_edit and (d == date.today() or self.past)
        self.opening_btn.setVisible(can_cash)
        self.add_cash_btn.setVisible(can_cash)
        rows = [["Opening cash", money(cash["opening"]), ""]]
        for r in cash["added"]:
            who = names.get(r["created_by"])
            text = f"Cash added {r['created_at'][11:16]}" + (f"  ·  {r['note']}" if r["note"] else "") + \
                   (f"  ·  {who}" if who else "")
            rows.append([text, "+ " + money(r["amount"]),
                         actions(small("Edit", lambda _=False, r=r: self.edit_cash(r)),
                                 small("Delete", lambda _=False, r=r: self.delete_cash(r), True))
                         if can_cash else ""])
        if self.see_in:
            rows.append(["Cash taken at checkout", "+ " + money(cash["cash_in"]), ""])
        if self.see_out:
            rows.append(["Cash paid for expenses", "− " + money(cash["cash_out"]), ""])
        if self.see_in:
            rows.append(["Cash in drawer now", money(cash["drawer"]), ""])
        self.cash_table.set_rows(rows, bold_last=self.see_in)
        self.cash_table.fit_height()

        bd = [
            ["Room bookings", f"{s['room_bookings']}  ·  {fmt_duration(s['room_minutes'])}"],
            ["Room time", money(s["room_time_income"])],
            ["Hall bookings", f"{s['hall_bookings']}  ·  {s['hall_chairs']} chairs  ·  "
                              f"{fmt_money(s['hall_chair_hours'])} chair-h"],
            ["Hall time", money(s["hall_time_income"])],
            ["Drinks & add-ons", money(s["items_income"])],
            ["Rounding to the pound", money(s["rounding"])],
        ]
        if self.see_out:
            bd += [["Expenses", "− " + money(s["expenses_total"])], ["Total now", money(s["net"])]]
        else:
            bd += [["Money in", money(s["income_total"])]]
        self.breakdown.set_rows(bd, bold_last=True)
        self.breakdown.fit_height()

        accs = s["accounts"]
        rows = [[a["label"] + ("" if a["active"] else "  (removed)"), fmt_money(a["in"]), fmt_money(a["out"]),
                 fmt_money(a["balance"])] for a in accs]
        rows.append(["Total", fmt_money(sum(a["in"] for a in accs)), fmt_money(sum(a["out"] for a in accs)),
                     fmt_money(sum(a["balance"] for a in accs))])
        self.accounts.set_rows(rows, bold_last=True)
        self.accounts.fit_height()

        can_edit = self.db.can("money.edit_expense")
        if s["expenses"]:
            self.expenses.set_rows([[e["created_at"][11:16], e["description"], self._paid("expense", e["id"]),
                                     names.get(e["created_by"], ""), money(e["amount"]),
                                     actions(small("Edit", lambda _=False, e=e: self.edit_expense(e)),
                                             small("Delete", lambda _=False, e=e: self.delete_expense(e), True))
                                     if can_edit else ""]
                                    for e in s["expenses"]])
        else:
            self.expenses.set_rows([["", "No expenses for this day.", "", "", "", ""]])
        self.expenses.fit_height()

        if s["cards"]:
            rows = []
            for c in reversed(s["cards"]):
                place = c["room_name"] if c["kind"] == "room" else f"Hall × {c['chairs']}"
                rows.append([c["end_time"][11:16], c["customer_name"], place, self._paid("card", c["id"]),
                             fmt_duration(c["minutes"]), names.get(c["closed_by"], ""), money(c["total"])])
            self.bills.set_rows(rows)
        else:
            self.bills.set_rows([["", "No closed bills for this day.", "", "", "", "", ""]])
        self.bills.fit_height()


# ============================================================================ settings
class SettingsPage(Page):
    def __init__(self, app):
        super().__init__(app, "Settings", "Hall prices, rooms and payment accounts.")
        content = QWidget()
        content.setObjectName("Content")
        cl = vbox(content, margins=(0, 0, 8, 8), spacing=16)
        self.body.addWidget(scroll(content), 1)

        # --- hall
        hall = panel()
        hl = vbox(hall, margins=(22, 18, 22, 18), spacing=10)
        hl.addWidget(label("Hall", "H2"))
        hl.addWidget(label("New values apply to new hall bookings; open cards keep the prices they started with. "
                           "A chair never pays more than the sum of the hours.", "Sub", wrap=True))
        row = hbox(spacing=12)
        self.chairs = line_edit(width=110)
        self.chairs.setAlignment(Qt.AlignCenter)
        row.addWidget(field("Chairs in the hall", self.chairs))
        row.addSpacing(12)
        self.tiers = []
        for i in range(4):
            e = line_edit(width=120)
            e.setAlignment(Qt.AlignCenter)
            self.tiers.append(e)
            row.addWidget(field(f"Hour {i + 1}  ({CURRENCY}/chair)", e))
        row.addStretch(1)
        save = button("Save hall", self.save_hall, "Primary")
        save.setMinimumWidth(120)
        row.addWidget(save, 0, Qt.AlignBottom)
        if not self.db.can("settings.hall"):
            save.hide()
            for e in [self.chairs] + self.tiers:
                e.setReadOnly(True)
        hl.addLayout(row)
        self.hall_msg = label("", "Sub")
        hl.addWidget(self.hall_msg)
        cl.addWidget(hall)

        self.edit_rooms = self.db.can("settings.rooms")
        self.edit_accounts = self.db.can("settings.accounts")
        # --- rooms
        self.rooms = Table(["Room", "Price per hour", "Status", ""], money_cols=(1,), row_height=44)
        cl.addWidget(self._section("Rooms", "Each room needs a name and a price per hour. A new price applies to "
                                            "new bookings.", "+ New room" if self.edit_rooms else None,
                                   lambda: self.edit_room(None), self.rooms))
        # --- accounts
        self.accounts = Table(["Account", "Method", ""], row_height=44)
        cl.addWidget(self._section("Payment accounts", "Cash is always there. Add each Vodafone Cash number and "
                                                       "InstaPay account you use.",
                                   "+ New account" if self.edit_accounts else None,
                                   lambda: self.edit_account(None), self.accounts))
        # --- users and reset: admin only
        self.admin = self.db.is_admin()
        if self.admin:
            self.users = Table(["Name", "Email", "Role", "Status", ""], stretch=1, row_height=44)
            cl.addWidget(self._section("Users", "Only the admin sees this. A new user can do nothing until you tick "
                                                "their permissions (or apply the “Normal user” template).",
                                       "+ New user", lambda: self.edit_user(None), self.users))
            danger = panel()
            dl = hbox(danger, margins=(22, 18, 22, 18), spacing=12)
            t = vbox(spacing=2)
            t.addWidget(label("Danger zone", "H2"))
            t.addWidget(label("Reset app data deletes all bookings, bills, expenses, customers and stock movements. "
                              "Menu, rooms, prices, payment accounts and users are kept. A backup is saved first.",
                              "Sub", wrap=True))
            dl.addLayout(t, 1)
            dl.addWidget(button("Reset app data…", self.reset_data, "Danger"), 0, Qt.AlignVCenter)
            cl.addWidget(danger)
        cl.addStretch(1)
        self._key = None
        self.fill_hall()

    @staticmethod
    def _section(title, sub, add_text, on_add, table):
        box = panel()
        bl = vbox(box, margins=(22, 18, 22, 12), spacing=8)
        head = hbox()
        t = vbox(spacing=2)
        t.addWidget(label(title, "H2"))
        t.addWidget(label(sub, "Sub", wrap=True))
        head.addLayout(t, 1)
        if add_text:
            head.addWidget(button(add_text, on_add, "Primary"), 0, Qt.AlignTop)
        bl.addLayout(head)
        bl.addWidget(table)
        return box

    def fill_hall(self, note: str = ""):
        tiers = self.db.hall_tiers()
        self.chairs.setText(str(self.db.hall_chairs_total()))
        for e, t in zip(self.tiers, tiers + [0] * 4):
            e.setText(fmt_money(t).replace(",", ""))
        self.hall_msg.setObjectName("Sub")
        self.hall_msg.setText(f"Now: {self.db.hall_chairs_total()} chairs · per chair "
                              f"{' + '.join(fmt_money(t) for t in tiers)} = max {money(sum(tiers))}.{note}")
        self.hall_msg.style().polish(self.hall_msg)

    def save_hall(self):
        try:
            self.db.set_hall_settings(self.chairs.text(),
                                      [float(e.text().replace(",", "") or 0) for e in self.tiers])
        except ValueError:
            return self._hall_error("Prices must be numbers.")
        except BookingError as e:
            return self._hall_error(str(e))
        self.fill_hall("  Saved.")
        self.app.data_changed()

    def _hall_error(self, msg):
        self.hall_msg.setObjectName("Error")
        self.hall_msg.setText(msg)
        self.hall_msg.style().polish(self.hall_msg)

    def refresh(self):
        rooms = self.db.list_rooms()
        busy = self.db.busy_room_ids()
        accounts = self.db.list_accounts()
        users = self.db.list_users() if self.admin else []
        key = (tuple(tuple(r) for r in rooms), tuple(sorted(busy)), tuple(tuple(a) for a in accounts),
               tuple(tuple(u) for u in users))
        if key == self._key:
            return
        self._key = key
        if rooms:
            self.rooms.set_rows([[r["name"], money(r["price_per_hour"]), "In use" if r["id"] in busy else "Free",
                                  actions(small("Edit", lambda _=False, r=r: self.edit_room(r)),
                                          small("Remove", lambda _=False, r=r: self.remove_room(r), True))
                                  if self.edit_rooms else ""]
                                 for r in rooms])
        else:
            self.rooms.set_rows([["No rooms yet. Click “+ New room” to add one.", "", "", ""]])
        self.rooms.fit_height()
        from db import METHODS
        rows = []
        for a in accounts:
            if a["method"] == "cash" or not self.edit_accounts:
                rows.append([account_label(a), METHODS[a["method"]],
                             label("Always available   " if a["method"] == "cash" else "", "Sub",
                                   align=Qt.AlignRight | Qt.AlignVCenter)])
            else:
                rows.append([account_label(a), METHODS[a["method"]],
                             actions(small("Edit", lambda _=False, a=a: self.edit_account(a)),
                                     small("Remove", lambda _=False, a=a: self.remove_account(a), True))])
        self.accounts.set_rows(rows)
        self.accounts.fit_height()
        if self.admin:
            out = []
            for u in users:
                role = "Admin · everything" if u["is_admin"] else f"{len(self.db.user_perms(u['id']))} permission(s)"
                btns = [small("Edit", lambda _=False, u=u: self.edit_user(u))]
                if not u["is_admin"]:
                    btns.append(small("Delete", lambda _=False, u=u: self.delete_user(u), True))
                out.append([u["name"], u["email"], role, "Active" if u["active"] else "Disabled", actions(*btns)])
            self.users.set_rows(out)
            self.users.fit_height()

    def edit_user(self, user):
        if UserDialog(self.window(), self.db, user).exec() == QDialog.Accepted:
            self._key = None
            self.app.data_changed()

    def delete_user(self, user):
        if not confirm(self.window(), "Delete user?", f"{user['name']} ({user['email']}) won't be able to sign in. "
                                                     "Bookings and expenses they made keep their name.", "Delete"):
            return
        try:
            self.db.delete_user(user["id"])
        except BookingError as e:
            return alert(self.window(), "Can't delete user", str(e))
        self._key = None
        self.app.data_changed()

    def reset_data(self):
        d = ResetDialog(self.window(), self.db)
        if d.exec() == QDialog.Accepted:
            self._key = None
            self.app.data_changed()
            where = f"\n\nA backup was saved to:\n{d.backup_path}" if d.backup_path else ""
            alert(self.window(), "App data was reset", "All bookings, bills, expenses, customers and stock movements "
                                                       "were deleted." + where)

    def edit_room(self, room):
        if RoomDialog(self.window(), self.db, room).exec() == QDialog.Accepted:
            self.app.data_changed()

    def remove_room(self, room):
        if not confirm(self.window(), "Remove room?", f"“{room['name']}” will no longer be bookable. "
                                                     "Old bills keep its name.", "Remove"):
            return
        try:
            self.db.remove_room(room["id"])
        except BookingError as e:
            alert(self.window(), "Can't remove room", str(e))
            return
        self.app.data_changed()

    def edit_account(self, acc):
        if AccountDialog(self.window(), self.db, acc).exec() == QDialog.Accepted:
            self.app.data_changed()

    def remove_account(self, acc):
        if confirm(self.window(), "Remove account?", f"{account_label(acc)} won't be offered at checkout any "
                                                    "more. Old bills and expenses keep its name.", "Remove"):
            self.db.remove_account(acc["id"])
            self.app.data_changed()
