"""Login screen and the main window (dark sidebar + pages)."""
from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QButtonGroup, QFrame, QLabel, QLineEdit, QMainWindow, QStackedWidget, QWidget

from auth import PAGE_ACCESS
from db import BookingError, Database

from .customers import CustomersPage
from .dialogs import ChangePasswordDialog
from .inventory import InventoryPage
from .pages import HallPage, MenuPage, MoneyPage, RoomsPage, SettingsPage
from .widgets import button, field, hbox, label, line_edit, panel, vbox


class LoginView(QWidget):
    def __init__(self, db: Database, assets_dir: str, on_success):
        super().__init__()
        self.setObjectName("Login")
        self.db = db
        self.on_success = on_success
        outer = vbox(self)
        outer.addStretch(1)
        row = hbox()
        row.addStretch(1)
        card = panel()
        card.setFixedWidth(400)
        cl = vbox(card, margins=(40, 36, 40, 36), spacing=14)
        logo = QPixmap(os.path.join(assets_dir, "UPSPACE_Full_Logo_Black_Transparent.png"))
        if not logo.isNull():
            pic = QLabel()
            pic.setPixmap(logo.scaledToWidth(170, Qt.SmoothTransformation))
            pic.setAlignment(Qt.AlignCenter)
            cl.addWidget(pic)
        else:
            cl.addWidget(label("UPSPACE", "H1", align=Qt.AlignCenter))
        cl.addWidget(label("Bookings & billing", "Sub", align=Qt.AlignCenter))
        cl.addSpacing(6)
        self.email = line_edit()
        self.password = line_edit()
        self.password.setEchoMode(QLineEdit.Password)
        cl.addWidget(field("Email", self.email))
        cl.addWidget(field("Password", self.password))
        self.error = label("", "Error", align=Qt.AlignCenter)
        cl.addWidget(self.error)
        b = button("Sign in", self.submit, "Primary")
        b.setMinimumHeight(40)
        cl.addWidget(b)
        self.email.returnPressed.connect(self.password.setFocus)
        self.password.returnPressed.connect(self.submit)
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)
        QTimer.singleShot(0, self.email.setFocus)

    def submit(self):
        try:
            user = self.db.authenticate(self.email.text(), self.password.text())
        except BookingError as e:
            self.error.setText(str(e))
            self.password.clear()
            return
        self.on_success(user)


class MainView(QWidget):
    PAGES = (("Rooms", RoomsPage), ("Hall", HallPage), ("Customers", CustomersPage), ("Menu", MenuPage),
             ("Inventory", InventoryPage), ("Money", MoneyPage), ("Settings", SettingsPage))

    def __init__(self, db: Database, on_logout):
        super().__init__()
        self.db = db
        self.user = db.user
        root = hbox(self, spacing=0)

        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(220)
        sl = vbox(side, margins=(0, 26, 0, 20), spacing=2)
        brand = vbox(margins=(23, 0, 20, 0), spacing=2)
        brand.addWidget(label("UPSPACE", "Brand"))
        brand.addWidget(label("WORK • MEET • GROW", "Tagline"))
        sl.addLayout(brand)
        sl.addSpacing(30)

        self.stack = QStackedWidget()
        self.pages = {}
        self.nav = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for name, cls in self.PAGES:
            if not db.can_any(*PAGE_ACCESS[name]):
                continue                     # the user has no right to see this page
            page = cls(self)
            self.pages[name] = page
            self.stack.addWidget(page)
            b = button(name, lambda _=False, n=name: self.show_page(n), "Nav")
            b.setCheckable(True)
            group.addButton(b)
            self.nav[name] = b
            sl.addWidget(b)
        sl.addStretch(1)
        foot = vbox(margins=(23, 0, 20, 0), spacing=2)
        self.clock = label("", "SideClock")
        self.date = label("", "SideDate")
        foot.addWidget(self.clock)
        foot.addWidget(self.date)
        foot.addSpacing(12)
        if self.user is not None:
            who = label(self.user["name"] + ("  ·  admin" if self.user["is_admin"] else ""), "SideDate")
            who.setWordWrap(True)
            foot.addWidget(who)
            foot.addSpacing(4)
            foot.addWidget(button("Change password", self.change_password, "NavFoot"))
        out = button("Log out", on_logout, "NavFoot")
        foot.addWidget(out)
        sl.addLayout(foot)

        root.addWidget(side)
        root.addWidget(self.stack, 1)

        if not self.pages:                   # no page allowed at all
            empty = QWidget()
            el = vbox(empty, margins=(40, 40, 40, 40))
            el.addWidget(label("No pages yet", "H1"))
            el.addWidget(label("Your account has no permissions yet. Ask the admin to give you access.", "Sub"))
            el.addStretch(1)
            self.stack.addWidget(empty)

        self._dirty = set()
        for p in self.pages.values():
            p.refresh()
        self.current = None
        if self.pages:
            self.show_page(next(iter(self.pages)))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self._seconds = 0
        self.tick()

    def show_page(self, name):
        page = self.pages[name]
        if name in self._dirty:             # refresh before showing, so the switch is one clean paint
            self._dirty.discard(name)
            page.refresh()
        self.current = name
        self.nav[name].setChecked(True)
        self.stack.setCurrentWidget(page)

    def can(self, perm: str) -> bool:
        return self.db.can(perm)

    def change_password(self):
        ChangePasswordDialog(self, self.db).exec()

    def open_restock(self, stock_item_id):
        """From an out-of-stock popup: go to the Inventory page and open Restock for that item."""
        if "Inventory" not in self.pages:
            from .dialogs import alert
            alert(self, "Out of stock", "Ask someone with Inventory access to restock it.")
            return
        self.show_page("Inventory")
        self.pages["Inventory"].restock(stock_item_id)

    def data_changed(self):
        """Something was saved. Refresh the visible page now, the rest when they're opened."""
        self._dirty = set(self.pages) - {self.current}
        if self.current in self.pages:
            self.pages[self.current].refresh()

    def tick(self):
        now = datetime.now()
        self.clock.setText(now.strftime("%H:%M:%S"))
        self.date.setText(now.strftime("%A %d %B %Y"))
        self._seconds += 1
        if self.current in ("Rooms", "Hall"):
            self.pages[self.current].tick()
        elif self.current == "Money" and self._seconds % 30 == 0:
            self.pages["Money"].tick()


class MainWindow(QMainWindow):
    def __init__(self, db: Database, assets_dir: str):
        super().__init__()
        self.db, self.assets = db, assets_dir
        self.setWindowTitle("UpSpace · Bookings & Billing")
        self.resize(1360, 840)
        self.setMinimumSize(1120, 700)
        self.show_login()

    def _swap(self, view):
        old = self.centralWidget()
        self.setCentralWidget(view)
        if old is not None:
            old.deleteLater()

    def show_login(self):
        self.db.sign_in(None)
        self._swap(LoginView(self.db, self.assets, self.show_main))

    def show_main(self, user=None):
        """Open the app as `user` (the admin when not given)."""
        if user is None:
            user = self.db.conn.execute("SELECT * FROM users WHERE is_admin = 1 AND deleted = 0 ORDER BY id").fetchone()
        self.db.sign_in(user)
        self._swap(MainView(self.db, self.show_login))
