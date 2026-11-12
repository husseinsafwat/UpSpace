"""Small building blocks shared by every screen."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from config import CURRENCY
from pricing import fmt_money


def money(value) -> str:
    return f"{fmt_money(value)} {CURRENCY}"


def parse_number(text: str):
    text = (text or "").strip().replace(",", "")
    return float(text) if text else None


def label(text: str = "", name: str | None = None, wrap: bool = False, align=None) -> QLabel:
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    if wrap:
        w.setWordWrap(True)
    if align is not None:
        w.setAlignment(align)
    return w


def caps(text: str) -> QLabel:
    return label(text.upper(), "Caps")


def button(text: str, on_click=None, kind: str | None = None, width: int | None = None) -> QPushButton:
    b = QPushButton(text.replace("&", "&&"))      # a single & would become a keyboard shortcut
    if kind:
        b.setObjectName(kind)
    b.setCursor(Qt.PointingHandCursor)
    if width:
        b.setFixedWidth(width)
    if on_click:
        b.clicked.connect(on_click)
    return b


def line_edit(placeholder: str = "", width: int | None = None, align_right: bool = False) -> QLineEdit:
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    if width:
        e.setFixedWidth(width)
    if align_right:
        e.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return e


def panel(name: str = "Panel") -> QFrame:
    f = QFrame()
    f.setObjectName(name)
    return f


def divider() -> QFrame:
    f = QFrame()
    f.setObjectName("Divider")
    f.setFrameShape(QFrame.NoFrame)
    return f


def vbox(widget: QWidget | None = None, margins=(0, 0, 0, 0), spacing: int = 8) -> QVBoxLayout:
    lay = QVBoxLayout(widget) if widget is not None else QVBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay


def hbox(widget: QWidget | None = None, margins=(0, 0, 0, 0), spacing: int = 8) -> QHBoxLayout:
    lay = QHBoxLayout(widget) if widget is not None else QHBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay


def field(title: str, widget: QWidget) -> QWidget:
    """Small caps title above an input."""
    w = QWidget()
    lay = vbox(w, spacing=4)
    lay.addWidget(caps(title))
    lay.addWidget(widget)
    return w


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def expanding(w: QWidget) -> QWidget:
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    return w


class Table(QTableWidget):
    """Read-only, clean table. Columns in `money_cols` are right-aligned."""

    def __init__(self, headers: list[str], money_cols=(), stretch: int = 0, row_height: int = 38):
        super().__init__(0, len(headers))
        self.money_cols = set(money_cols)
        self.setHorizontalHeaderLabels([h.upper() for h in headers])
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(row_height)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self.setShowGrid(False)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        h = self.horizontalHeader()
        h.setHighlightSections(False)
        for c in range(len(headers)):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
            if c in self.money_cols:
                self.horizontalHeaderItem(c).setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            else:
                self.horizontalHeaderItem(c).setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        h.setSectionResizeMode(stretch, QHeaderView.Stretch)

    def set_rows(self, rows: list[list], bold_last: bool = False) -> None:
        self.setUpdatesEnabled(False)
        self.clearContents()
        self.setRowCount(len(rows))
        widget_cols: dict[int, int] = {}
        for r, values in enumerate(rows):
            for c, v in enumerate(values):
                if isinstance(v, QWidget):
                    self.setCellWidget(r, c, v)
                    widget_cols[c] = max(widget_cols.get(c, 0), v.sizeHint().width())
                    continue
                item = QTableWidgetItem("" if v is None else str(v))
                if c in self.money_cols:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                else:
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                if bold_last and r == len(rows) - 1:
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                self.setItem(r, c, item)
        for c, w in widget_cols.items():          # columns holding buttons: wide enough for them
            self.horizontalHeader().setSectionResizeMode(c, QHeaderView.Fixed)
            self.setColumnWidth(c, w + 24)
        self.setUpdatesEnabled(True)

    def fit_height(self, max_rows: int | None = None) -> None:
        """Make the table exactly as tall as its rows (no inner scrolling)."""
        rows = self.rowCount() if max_rows is None else min(self.rowCount(), max_rows)
        head = 0 if self.horizontalHeader().isHidden() else self.horizontalHeader().sizeHint().height()
        self.setFixedHeight(head + sum(self.rowHeight(r) for r in range(rows)) + 4)
