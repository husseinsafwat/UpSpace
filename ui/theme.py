"""UpSpace look: monochrome brand colors, Inter font, one Qt stylesheet for the whole app."""
from __future__ import annotations

import os

from PySide6.QtGui import QFont, QFontDatabase

from config import CHARCOAL, DANGER, IVORY, STONE, WHITE

INK = CHARCOAL            # text, primary buttons, sidebar
PAPER = IVORY             # page background
MUTED = STONE             # secondary text
LINE = "#E4E0D6"          # borders and dividers
LINE_STRONG = "#CFCAC0"   # input borders
SURFACE = WHITE           # panels
HOVER_DARK = "#2C2C29"
HOVER_LIGHT = "#EFEBE2"
SELECT = "#F1EEE6"
RED = DANGER
OK = "#2F6B3A"

FAMILY = "Inter"


def load_fonts(assets_dir: str) -> str:
    """Load the bundled Inter fonts. Returns the family to use."""
    global FAMILY
    fonts_dir = os.path.join(assets_dir, "fonts")
    loaded = False
    if os.path.isdir(fonts_dir):
        for f in sorted(os.listdir(fonts_dir)):
            if f.lower().endswith((".otf", ".ttf")):
                if QFontDatabase.addApplicationFont(os.path.join(fonts_dir, f)) >= 0:
                    loaded = True
    FAMILY = "Inter" if loaded or "Inter" in QFontDatabase.families() else "Segoe UI"
    return FAMILY


def base_font() -> QFont:
    f = QFont(FAMILY)
    f.setPixelSize(13)
    f.setHintingPreference(QFont.PreferNoHinting)
    return f


def stylesheet() -> str:
    return f"""
* {{ font-family: "{FAMILY}"; font-size: 13px; color: {INK}; }}
QMainWindow, QWidget#Page, QWidget#Content, QDialog, QWidget#Login {{ background: {PAPER}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}

/* ---------- sidebar ---------- */
QFrame#Sidebar {{ background: {INK}; }}
QLabel#Brand {{ color: {PAPER}; font-size: 20px; font-weight: 800; letter-spacing: 3px; }}
QLabel#Tagline {{ color: {MUTED}; font-size: 10px; font-weight: 600; letter-spacing: 2px; }}
QLabel#SideClock {{ color: {PAPER}; font-size: 12px; font-weight: 600; }}
QLabel#SideDate {{ color: {MUTED}; font-size: 11px; }}
QPushButton#Nav {{
    color: #C9C6BE; background: transparent; border: none; border-left: 3px solid transparent;
    text-align: left; padding: 11px 20px; font-size: 14px; font-weight: 600; }}
QPushButton#Nav:hover {{ background: {HOVER_DARK}; color: {PAPER}; }}
QPushButton#Nav:checked {{ background: #24241F; color: {PAPER}; border-left: 3px solid {PAPER}; }}
QPushButton#NavFoot {{
    color: #C9C6BE; background: transparent; border: 1px solid #45443F; border-radius: 6px;
    padding: 7px 12px; font-size: 12px; }}
QPushButton#NavFoot:hover {{ background: {HOVER_DARK}; color: {PAPER}; }}

/* ---------- text ---------- */
QLabel#H1 {{ font-size: 22px; font-weight: 700; }}
QLabel#H2 {{ font-size: 16px; font-weight: 700; }}
QLabel#H3 {{ font-size: 14px; font-weight: 700; }}
QLabel#Sub {{ color: {MUTED}; font-size: 12px; }}
QLabel#Caps {{ color: {MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
QLabel#Error {{ color: {RED}; font-size: 12px; }}
QLabel#Good {{ color: {OK}; font-size: 12px; font-weight: 600; }}
QLabel#Bad {{ color: {RED}; font-size: 12px; font-weight: 600; }}
QLabel#Money {{ font-size: 15px; font-weight: 700; }}
QLabel#Big {{ font-size: 24px; font-weight: 700; }}
QLabel#Timer {{ font-size: 26px; font-weight: 700; letter-spacing: 1px; }}

/* ---------- panels ---------- */
QFrame#Panel {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 10px; }}
QFrame#Panel QLabel {{ background: transparent; }}
QFrame#Kpi {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 10px; }}
QFrame#KpiDark {{ background: {INK}; border: 1px solid {INK}; border-radius: 10px; }}
QFrame#KpiDark QLabel {{ color: {PAPER}; background: transparent; }}
QFrame#KpiDark QLabel#Caps {{ color: {MUTED}; }}
QFrame#Card {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 10px; }}
QFrame#CardHead {{ background: {INK}; border-radius: 8px; }}
QFrame#CardHead QLabel {{ color: {PAPER}; background: transparent; }}
QFrame#CardHead QLabel#Caps {{ color: {MUTED}; }}
QFrame#Soft {{ background: {PAPER}; border: none; border-radius: 6px; }}
QFrame#TotalBar {{ background: {INK}; border-radius: 8px; }}
QFrame#TotalBar QLabel {{ color: {PAPER}; background: transparent; }}
QFrame#Divider {{ background: {LINE}; max-height: 1px; min-height: 1px; border: none; }}

/* ---------- buttons ---------- */
QPushButton {{
    background: {SURFACE}; border: 1px solid {LINE_STRONG}; border-radius: 6px; padding: 7px 14px;
    font-weight: 600; }}
QPushButton:hover {{ background: {HOVER_LIGHT}; }}
QPushButton:disabled {{ color: #B5B2AA; border-color: {LINE}; background: {SURFACE}; }}
QPushButton#Primary {{ background: {INK}; color: {PAPER}; border: 1px solid {INK}; }}
QPushButton#Primary:hover {{ background: {HOVER_DARK}; }}
QPushButton#Primary:disabled {{ background: #8E8C86; border-color: #8E8C86; color: #E9E6DE; }}
QPushButton#Danger {{ color: {RED}; border-color: #D9B3B3; }}
QPushButton#Danger:hover {{ background: #F7ECEC; }}
QPushButton#Link {{ background: transparent; border: none; color: {MUTED}; padding: 2px 6px; font-weight: 700; }}
QPushButton#Link:hover {{ color: {RED}; }}
QPushButton#Step {{ padding: 0; min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
    font-size: 15px; }}
QPushButton#StepPlus {{ padding: 0; min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
    font-size: 15px; background: {INK}; color: {PAPER}; border: 1px solid {INK}; }}
QPushButton#StepPlus:hover {{ background: {HOVER_DARK}; }}
QPushButton#Chip {{ border-radius: 13px; padding: 5px 14px; font-weight: 600; }}
QPushButton#Chip:checked {{ background: {INK}; color: {PAPER}; border-color: {INK}; }}

/* ---------- inputs ---------- */
QLineEdit, QComboBox, QSpinBox, QDateEdit {{
    background: {SURFACE}; border: 1px solid {LINE_STRONG}; border-radius: 6px; padding: 7px 10px;
    selection-background-color: {INK}; selection-color: {PAPER}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus {{ border: 1px solid {INK}; }}
QLineEdit:disabled {{ background: {SELECT}; color: {MUTED}; }}
QComboBox::drop-down, QDateEdit::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {LINE_STRONG};
    selection-background-color: {SELECT}; selection-color: {INK}; outline: none; padding: 4px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {LINE_STRONG}; border-radius: 4px;
    background: {SURFACE}; }}
QCheckBox::indicator:checked {{ background: {INK}; border-color: {INK}; image: none; }}
QRadioButton {{ spacing: 8px; }}

/* ---------- tables ---------- */
QTableWidget {{ background: {SURFACE}; border: none; gridline-color: transparent; outline: none;
    selection-background-color: {SELECT}; selection-color: {INK}; alternate-background-color: #FBFAF7; }}
QTableWidget::item {{ padding: 0 10px; border-bottom: 1px solid #F0EDE6; }}
QHeaderView::section {{ background: {SURFACE}; color: {MUTED}; border: none; border-bottom: 1px solid {LINE};
    padding: 8px 10px; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
QTableCornerButton::section {{ background: {SURFACE}; border: none; }}

/* ---------- scrollbars ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #D3CFC5; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
QScrollBar:horizontal {{ height: 0; }}

QToolTip {{ background: {INK}; color: {PAPER}; border: none; padding: 6px; }}
QMessageBox {{ background: {PAPER}; }}
"""
