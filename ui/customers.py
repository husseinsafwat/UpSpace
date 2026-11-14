"""Customers page with two tabs: the customer list (add / edit) and statistics (filters, history, Excel)."""
from __future__ import annotations

import os
from datetime import date, timedelta

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QCompleter, QDateEdit, QDialog, QFileDialog, QStackedWidget,
                               QWidget)

from db import SIZE_NAMES, BookingError
from pricing import fmt_duration, fmt_money

from .dialogs import CustomerDialog, alert, confirm
from .pages import Page, actions, scroll, small
from .widgets import Table, button, caps, field, hbox, label, line_edit, money, panel, vbox

PERIODS = ("All time", "This month", "Last 30 days", "This year", "Custom")


def kpi(title: str, dark: bool = False):
    box = panel("KpiDark" if dark else "Kpi")
    bl = vbox(box, margins=(20, 16, 20, 16), spacing=4)
    bl.addWidget(caps(title))
    v = label("", "Big")
    bl.addWidget(v)
    sub = label("", "Sub")
    bl.addWidget(sub)
    return box, v, sub


def section(title: str, widget, right=None):
    box = panel()
    bl = vbox(box, margins=(18, 14, 18, 10), spacing=6)
    head = hbox()
    head.addWidget(label(title, "H3"))
    head.addStretch(1)
    if right is not None:
        head.addWidget(right)
    bl.addLayout(head)
    bl.addWidget(widget)
    bl.addStretch(1)
    return box


def place(c) -> str:
    return c["room_name"] if c["kind"] == "room" else f"Hall × {c['chairs']}"


class CustomersPage(Page):
    def __init__(self, app):
        super().__init__(app, "Customers", "Every customer is saved by phone number when a card starts.")
        self.tabs = QButtonGroup(self)
        for i, t in enumerate(("Customers", "Statistics")):
            b = button(t, kind="Chip")
            b.setCheckable(True)
            b.setChecked(i == 0)
            self.tabs.addButton(b, i)
            self.toolbar.addWidget(b)
        self.tabs.idClicked.connect(self.switch)
        self.stack = QStackedWidget()
        self.manage = ManageTab(self)
        self.stats = StatsTab(self)
        self.stack.addWidget(self.manage)
        self.stack.addWidget(self.stats)
        self.body.addWidget(self.stack, 1)

    def switch(self, i: int):
        self.tabs.button(i).setChecked(True)
        (self.manage, self.stats)[i].refresh()
        self.stack.setCurrentIndex(i)

    def show_stats(self, customer_id):
        self.stats.select(customer_id)
        self.switch(1)

    def refresh(self):
        self.stack.currentWidget().refresh()


# ============================================================================ manage
class ManageTab(QWidget):
    def __init__(self, page: CustomersPage):
        super().__init__()
        self.page, self.db = page, page.db
        lay = vbox(self, spacing=14)
        bar = hbox(spacing=10)
        self.search = line_edit("Search by name or phone…", width=320)
        self.search.textChanged.connect(lambda _: self.refresh(force=True))
        bar.addWidget(self.search)
        self.count = label("", "Sub")
        bar.addWidget(self.count)
        bar.addStretch(1)
        bar.addWidget(button("+ New customer", lambda: self.edit(None), "Primary"))
        lay.addLayout(bar)
        box = panel()
        bl = vbox(box, margins=(6, 6, 6, 6))
        self.table = Table(["Customer", "Phone", "Visits", "Total spent", "Last visit", "Customer since", "Notes", ""],
                           money_cols=(2, 3), stretch=6, row_height=44)
        bl.addWidget(self.table)
        lay.addWidget(box, 1)
        self._key = None

    def refresh(self, force: bool = False):
        rows = self.db.list_customers(self.search.text())
        key = (self.search.text(), [tuple(r) for r in rows])
        if key == self._key and not force:
            return
        self._key = key
        total = len(self.db.list_customers()) if self.search.text().strip() else len(rows)
        self.count.setText(f"{len(rows)} of {total} customer(s)" if self.search.text().strip()
                           else f"{total} customer(s)")
        out = []
        for c in rows:
            name = c["name"] + ("  ·  here now" if c["open_cards"] else "")
            out.append([name, c["phone"], c["visits"], money(c["spent"]),
                        (c["last_visit"] or "")[:10] or "—", c["created_at"][:10], c["notes"] or "",
                        actions(small("Statistics", lambda _=False, i=c["id"]: self.page.show_stats(i)),
                                small("Edit", lambda _=False, c=c: self.edit(c)),
                                small("Remove", lambda _=False, c=c: self.remove(c), True))])
        self.table.set_rows(out or [["No customers found.", "", "", "", "", "", "", ""]])

    def edit(self, customer):
        if CustomerDialog(self.window(), self.db, customer).exec() == QDialog.Accepted:
            self.page.app.data_changed()

    def remove(self, c):
        if not confirm(self.window(), "Remove customer?", f"“{c['name']}” ({c['phone']}) will be removed.",
                       "Remove"):
            return
        try:
            self.db.remove_customer(c["id"])
        except BookingError as e:
            alert(self.window(), "Can't remove customer", str(e))
            return
        self.page.app.data_changed()


# ============================================================================ statistics
class StatsTab(QWidget):
    def __init__(self, page: CustomersPage):
        super().__init__()
        self.page, self.db = page, page.db
        lay = vbox(self, spacing=14)

        bar = hbox(spacing=12)
        self.who = QComboBox()
        self.who.setEditable(True)
        self.who.setInsertPolicy(QComboBox.NoInsert)
        self.who.setMinimumWidth(320)
        self.who.completer().setFilterMode(Qt.MatchContains)
        self.who.completer().setCompletionMode(QCompleter.PopupCompletion)
        self.who.lineEdit().setPlaceholderText("Type a name or phone…")
        self.who.activated.connect(lambda _: self.refresh(force=True))
        bar.addWidget(field("Customer", self.who))
        self.period = QComboBox()
        self.period.addItems(PERIODS)
        self.period.currentIndexChanged.connect(self._period_changed)
        bar.addWidget(field("Period", self.period))
        self.start, self.end = QDateEdit(), QDateEdit()
        today = date.today()
        for e, d in ((self.start, today.replace(day=1)), (self.end, today)):
            e.setCalendarPopup(True)
            e.setDisplayFormat("dd MMM yyyy")
            e.setDate(QDate(d.year, d.month, d.day))
            e.setMaximumDate(QDate(today.year, today.month, today.day))
            e.dateChanged.connect(lambda _: self.refresh(force=True))
        self.start_f, self.end_f = field("From", self.start), field("To", self.end)
        bar.addWidget(self.start_f)
        bar.addWidget(self.end_f)
        bar.addStretch(1)
        exp = button("Export to Excel", self.export)
        bar.addWidget(exp, 0, Qt.AlignBottom)
        lay.addLayout(bar)
        self._period_changed(0)

        content = QWidget()
        content.setObjectName("Content")
        self.cl = vbox(content, margins=(0, 0, 8, 8), spacing=16)
        lay.addWidget(scroll(content), 1)

        self.profile = panel()
        pl = hbox(self.profile, margins=(20, 14, 20, 14), spacing=24)
        self.p_name = label("", "H2")
        self.p_info = label("", "Sub", wrap=True)
        left = vbox(spacing=2)
        left.addWidget(self.p_name)
        left.addWidget(self.p_info)
        pl.addLayout(left, 1)
        self.cl.addWidget(self.profile)

        row1, row2 = hbox(spacing=14), hbox(spacing=14)
        self.k = {}
        for key, title, dark in (("visits", "Visits", False), ("time", "Time spent", False),
                                 ("items", "Drinks & add-ons", False), ("total", "Total spent", True)):
            box, v, sub = kpi(title, dark)
            self.k[key] = (v, sub)
            row1.addWidget(box, 1)
        for key, title in (("rooms", "Rooms"), ("hall", "Hall"), ("avg", "Average per visit"),
                           ("last", "Last visit")):
            box, v, sub = kpi(title)
            v.setObjectName("H2")
            self.k[key] = (v, sub)
            row2.addWidget(box, 1)
        self.cl.addLayout(row1)
        self.cl.addLayout(row2)

        self.top = Table(["#", "Customer", "Phone", "Visits", "Time", "Items", "Items spend", "Total spent",
                          "Last visit"], money_cols=(3, 5, 6, 7), stretch=1, row_height=36)
        self.top.cellDoubleClicked.connect(self._open_top)
        self.top_box = section("Top customers", self.top, label("Double-click a customer to open their stats", "Sub"))
        self.cl.addWidget(self.top_box)

        cols = hbox(spacing=16)
        self.items = Table(["Item", "Size", "Qty", "Total"], money_cols=(2, 3), stretch=0, row_height=32)
        self.items_box = section("Most ordered", self.items)
        cols.addWidget(self.items_box, 2)
        self.split = Table(["", "Visits", "Time", "Money"], money_cols=(1, 2, 3), row_height=32)
        self.split_box = section("Rooms vs hall", self.split)
        cols.addWidget(self.split_box, 3)
        self.cl.addLayout(cols)

        self.history = Table(["Date", "Place", "Time", "Duration", "Items", "Time cost", "Items", "Total",
                              "Paid with"], money_cols=(5, 6, 7), stretch=4, row_height=36)
        self.history_box = section("Visit history", self.history)
        self.cl.addWidget(self.history_box)
        self.cl.addStretch(1)
        self._key = None
        self._selected = None

    # ------------------------------------------------------------- filters
    def _period_changed(self, i):
        custom = PERIODS[i] == "Custom"
        self.start_f.setVisible(custom)
        self.end_f.setVisible(custom)
        if hasattr(self, "history"):
            self.refresh(force=True)

    def range(self):
        today = date.today()
        p = self.period.currentText()
        if p == "All time":
            return None, None
        if p == "This month":
            return today.replace(day=1), today
        if p == "Last 30 days":
            return today - timedelta(days=29), today
        if p == "This year":
            return today.replace(month=1, day=1), today
        s, e = self.start.date(), self.end.date()
        return date(s.year(), s.month(), s.day()), date(e.year(), e.month(), e.day())

    def customer_id(self):
        """The chosen customer, or None for all customers (also when the typed text matches nobody)."""
        i = self.who.findText(self.who.currentText())
        return self.who.itemData(i) if i >= 0 else None

    def select(self, customer_id):
        self._selected = customer_id
        self._fill_combo()

    def _fill_combo(self):
        customers = self.db.list_customers()
        want = self._selected if self._selected is not None else self.customer_id()
        self.who.blockSignals(True)
        self.who.clear()
        self.who.addItem("All customers", None)
        for c in sorted(customers, key=lambda c: c["name"].casefold()):
            self.who.addItem(f"{c['name']}  ·  {c['phone']}", c["id"])
        self.who.setCurrentIndex(max(0, self.who.findData(want)))
        self.who.blockSignals(False)
        self._selected = None

    def _open_top(self, row, _col):
        item = self.top.item(row, 0)
        if item is not None and item.data(Qt.UserRole) is not None:
            self.select(item.data(Qt.UserRole))
            self.refresh(force=True)

    # ------------------------------------------------------------- refresh
    def refresh(self, force: bool = False):
        self._fill_combo()                     # picks up customers added since last time, keeps the choice
        cid = self.customer_id()
        start, end = self.range()
        if start is not None and start > end:
            start, end = end, start
        s = self.db.customer_stats(cid, start, end)
        key = (cid, start, end, s["visits"], s["total"], self.db.conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0])
        if key == self._key and not force:
            return
        self._key = key
        one = cid is not None
        self.profile.setVisible(one)
        self.top_box.setVisible(not one)
        self.history_box.setVisible(one)
        period = "All time" if start is None else f"{start:%d %b %Y} – {end:%d %b %Y}"

        if one:
            c = self.db.get_customer(cid)
            self.p_name.setText(c["name"])
            info = f"{c['phone']}  ·  customer since {c['created_at'][:10]}  ·  {period}"
            if c["notes"]:
                info += f"  ·  {c['notes']}"
            self.p_info.setText(info)

        self._set("visits", str(s["visits"]), f"{s['customers']} customer(s)" if not one else
                  (f"first {s['first_visit'][:10]}" if s["first_visit"] else "no visits"))
        self._set("time", fmt_duration(s["minutes"]), f"{fmt_money(s['hall_chair_hours'])} hall chair-hours")
        self._set("items", str(s["items_count"]), money(s["items_spent"]))
        self._set("total", money(s["total"]), period)
        self._set("rooms", f"{s['room_visits']} visit(s)", fmt_duration(s["room_minutes"]))
        self._set("hall", f"{s['hall_visits']} visit(s)", fmt_duration(s["hall_minutes"]))
        self._set("avg", money(s["average"]), "per closed bill")
        self._set("last", (s["last_visit"] or "—")[:10], (s["last_visit"] or "")[11:16])

        rooms = [x for x in s["cards"] if x["kind"] == "room"]
        halls = [x for x in s["cards"] if x["kind"] == "hall"]
        self.split.set_rows([
            ["Rooms", len(rooms), fmt_duration(s["room_minutes"]), money(sum(x["total"] for x in rooms))],
            ["Hall", len(halls), fmt_duration(s["hall_minutes"]), money(sum(x["total"] for x in halls))],
            ["Total", s["visits"], fmt_duration(s["minutes"]), money(s["total"])],
        ], bold_last=True)
        self.split.fit_height()

        items = s["items"][:15]
        self.items.set_rows([[i["name"], SIZE_NAMES.get(i["size"], ""), int(i["qty"]), money(i["total"])]
                             for i in items] or [["No drinks or add-ons.", "", "", ""]])
        self.items.fit_height()

        if not one:
            top = self.db.top_customers(start, end, limit=50)
            self.top.set_rows([[n, r["name"], r["phone"], r["visits"], fmt_duration(r["minutes"] or 0),
                                int(r["items_count"]), money(r["items_spent"] or 0), money(r["total"] or 0),
                                (r["last_visit"] or "")[:10]] for n, r in enumerate(top, start=1)]
                              or [["", "No customer visits in this period.", "", "", "", "", "", "", ""]])
            for r, row in enumerate(top):
                self.top.item(r, 0).setData(Qt.UserRole, row["id"])
            self.top.fit_height()
        else:
            hist = []
            for x in s["cards"]:
                paid = " + ".join(p["label"].split(" · ")[0] for p in self.db.payments_for("card", x["id"]))
                hist.append([x["start_time"][:10], place(x), f"{x['start_time'][11:16]}–{x['end_time'][11:16]}",
                             fmt_duration(x["minutes"] or 0), self.db.card_items_text(x["id"]) or "—",
                             money(x["time_cost"] or 0), money(x["items_total"] or 0), money(x["total"] or 0), paid])
            self.history.set_rows(hist or [["", "No visits in this period.", "", "", "", "", "", "", ""]])
            self.history.fit_height()

    def _set(self, key, value, sub=""):
        v, s = self.k[key]
        v.setText(value)
        s.setText(sub)

    # ------------------------------------------------------------- export
    def export(self):
        import customer_report
        cid = self.customer_id()
        start, end = self.range()
        cust = self.db.get_customer(cid) if cid is not None else None
        path, _ = QFileDialog.getSaveFileName(
            self.window(), "Export customer statistics",
            os.path.join(os.path.expanduser("~"), "Documents", customer_report.default_filename(cust, start, end)),
            "Excel workbook (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            customer_report.build_customer_report(self.db, cid, start, end, path)
        except PermissionError:
            return alert(self.window(), "Couldn't save", "Is that file open in Excel? Close it and try again.")
        except OSError as e:
            return alert(self.window(), "Couldn't save", str(e))
        alert(self.window(), "Exported", os.path.basename(path))

