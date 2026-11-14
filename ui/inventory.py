"""Inventory page: stock items, restock / adjust, low-stock warnings and the movement history."""
from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QComboBox, QDialog, QWidget

from db import STOCK_REASONS, fmt_qty

from . import theme
from .dialogs import AdjustStockDialog, RestockDialog, StockItemDialog, confirm
from .pages import Page, actions, scroll, small
from .widgets import Table, button, caps, hbox, label, panel, vbox


def status_of(st) -> str:
    if st["quantity"] <= 0:
        return "Out of stock"
    if st["quantity"] <= st["low_level"]:
        return "Low"
    return "OK"


class InventoryPage(Page):
    def __init__(self, app):
        super().__init__(app, "Inventory", "What you have in stock. Menu items linked to a stock item take from it "
                                           "on every sale; removing an item from a card puts it back.")
        self.toolbar.addWidget(button("+ New item", lambda: self.edit(None), "Primary"))

        content = QWidget()
        content.setObjectName("Content")
        cl = vbox(content, margins=(0, 0, 8, 8), spacing=16)
        self.body.addWidget(scroll(content), 1)

        kpis = hbox(spacing=14)
        self.kpi = {}
        for key, title, dark in (("items", "Items in inventory", False), ("low", "Low stock", False),
                                 ("out", "Out of stock", True)):
            box = panel("KpiDark" if dark else "Kpi")
            bl = vbox(box, margins=(20, 16, 20, 16), spacing=4)
            bl.addWidget(caps(title))
            v = label("", "Big")
            bl.addWidget(v)
            self.kpi[key] = v
            kpis.addWidget(box, 1)
        cl.addLayout(kpis)

        box = panel()
        bl = vbox(box, margins=(18, 14, 18, 10), spacing=6)
        bl.addWidget(label("Stock", "H3"))
        self.table = Table(["Item", "In stock", "Alert at", "Status", "Sold as (menu)", ""], money_cols=(1, 2),
                           stretch=4, row_height=44)
        bl.addWidget(self.table)
        cl.addWidget(box)

        box = panel()
        bl = vbox(box, margins=(18, 14, 18, 10), spacing=6)
        head = hbox()
        head.addWidget(label("Movements", "H3"))
        head.addStretch(1)
        self.filter = QComboBox()
        self.filter.setMinimumWidth(220)
        self.filter.currentIndexChanged.connect(lambda _: self.fill_moves())
        head.addWidget(self.filter)
        bl.addLayout(head)
        self.moves = Table(["When", "Item", "Change", "Stock after", "Type", "Note"], money_cols=(2, 3), stretch=5,
                           row_height=34)
        bl.addWidget(self.moves)
        cl.addWidget(box)
        cl.addStretch(1)
        self._key = None

    def refresh(self):
        stock = self.db.list_stock()
        last_move = self.db.conn.execute("SELECT MAX(id) FROM stock_moves").fetchone()[0]
        menu = self.db.conn.execute("SELECT id, name, stock_item_id, stock_per_sale FROM menu_items "
                                    "WHERE active = 1 AND stock_item_id IS NOT NULL").fetchall()
        key = ([tuple(s) for s in stock], last_move, [tuple(m) for m in menu])
        if key == self._key:
            return
        self._key = key
        self.kpi["items"].setText(str(len(stock)))
        self.kpi["low"].setText(str(sum(1 for s in stock if status_of(s) == "Low")))
        self.kpi["out"].setText(str(sum(1 for s in stock if status_of(s) == "Out of stock")))
        uses: dict[int, list[str]] = {}
        for m in menu:
            uses.setdefault(m["stock_item_id"], []).append(m["name"])
        rows = []
        for st in stock:
            rows.append([st["name"], f"{fmt_qty(st['quantity'])} {st['unit']}", fmt_qty(st["low_level"]),
                         status_of(st), ", ".join(uses.get(st["id"], [])) or "Not on the menu (Edit to sell it)",
                         actions(small("Restock", lambda _=False, i=st["id"]: self.restock(i)),
                                 small("Adjust", lambda _=False, i=st["id"]: self.adjust(i)),
                                 small("Edit", lambda _=False, s=st: self.edit(s)),
                                 small("Remove", lambda _=False, s=st: self.remove(s), True))])
        if rows:
            self.table.set_rows(rows)
            for r, st in enumerate(stock):
                status = status_of(st)
                if status != "OK":
                    for c in (1, 3):
                        cell = self.table.item(r, c)
                        cell.setForeground(QColor(theme.RED))
                        f = cell.font()
                        f.setBold(True)
                        cell.setFont(f)
        else:
            self.table.set_rows([["Nothing in the inventory yet. Click “+ New item” to add one.", "", "", "", "", ""]])
        self.table.fit_height()

        current = self.filter.currentData()
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.addItem("All items", None)
        for st in stock:
            self.filter.addItem(st["name"], st["id"])
        self.filter.setCurrentIndex(max(0, self.filter.findData(current)))
        self.filter.blockSignals(False)
        self.fill_moves()

    def fill_moves(self):
        moves = self.db.stock_moves(self.filter.currentData(), limit=100)
        rows = []
        for m in moves:
            sign = "+" if m["change"] > 0 else "−"
            note = m["note"] or ""
            if m["card_id"] and m["reason"] in ("sale", "return"):
                c = self.db.get_card(m["card_id"])
                if c:
                    note = f"{note} · {c['customer_name']}".strip(" ·")
            rows.append([m["created_at"][:16], m["name"], f"{sign}{fmt_qty(abs(m['change']))}",
                         f"{fmt_qty(m['balance'])} {m['unit']}", STOCK_REASONS[m["reason"]], note])
        self.moves.set_rows(rows or [["", "No movements yet.", "", "", "", ""]])
        self.moves.fit_height(max_rows=14)

    def _done(self, dlg):
        if dlg.exec() == QDialog.Accepted:
            self.app.data_changed()
            return True
        return False

    def edit(self, item):
        self._done(StockItemDialog(self.window(), self.db, item))

    def restock(self, stock_item_id):
        st = self.db.get_stock_item(stock_item_id)
        if st is not None and st["active"]:
            self._done(RestockDialog(self.window(), self.db, st))

    def adjust(self, stock_item_id):
        self._done(AdjustStockDialog(self.window(), self.db, self.db.get_stock_item(stock_item_id)))

    def remove(self, st):
        used = self.db.menu_items_for_stock(st["id"])
        extra = (" These menu items will stop taking from stock: " + ", ".join(m["name"] for m in used) + ".") \
            if used else ""
        if confirm(self.window(), "Remove inventory item?", f"“{st['name']}” will be removed from the inventory."
                                                            + extra, "Remove"):
            self.db.remove_stock_item(st["id"])
            self.app.data_changed()

