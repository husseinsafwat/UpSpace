"""Login screen and the main window (dark sidebar + pages)."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QButtonGroup, QFrame, QLabel, QLineEdit, QMainWindow, QStackedWidget, QWidget

from config import LOGIN_EMAIL, LOGIN_PASSWORD_SHA256
from db import Database

from .customers import CustomersPage
from .inventory import InventoryPage
from .pages import HallPage, MenuPage, MoneyPage, RoomsPage, SettingsPage
from .widgets import button, field, hbox, label, line_edit, panel, vbox


class LoginView(QWidget):
    def __init__(self, assets_dir: str, on_success):
        super().__init__()
        self.setObjectName("Login")
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
        email = self.email.text().strip().lower()
        digest = hashlib.sha256(self.password.text().encode("utf-8")).hexdigest()
        if email == LOGIN_EMAIL.lower() and digest == LOGIN_PASSWORD_SHA256:
            self.on_success()
        else:
            self.error.setText("Wrong email or password.")
            self.password.clear()


class MainView(QWidget):
    PAGES = (("Rooms", RoomsPage), ("Hall", HallPage), ("Customers", CustomersPage), ("Menu", MenuPage),
             ("Inventory", InventoryPage), ("Money", MoneyPage), ("Settings", SettingsPage))

    def __init__(self, db: Database, on_logout):
        super().__init__()
        self.db = db
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
        out = button("Log out", on_logout, "NavFoot")
        foot.addWidget(out)
        sl.addLayout(foot)

        root.addWidget(side)
        root.addWidget(self.stack, 1)

        self._dirty = set()
        for p in self.pages.values():
            p.refresh()
        self.current = None
        self.show_page("Rooms")

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

    def open_restock(self, stock_item_id):
        """From an out-of-stock popup: go to the Inventory page and open Restock for that item."""
        self.show_page("Inventory")
        self.pages["Inventory"].restock(stock_item_id)

    def data_changed(self):
        """Something was saved. Refresh the visible page now, the rest when they're opened."""
        self._dirty = set(self.pages) - {self.current}
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
        self._swap(LoginView(self.assets, self.show_main))

    def show_main(self):
        self._swap(MainView(self.db, self.show_login))
