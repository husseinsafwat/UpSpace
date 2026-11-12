"""Excel money report: one sheet per day that has activity, then a "Total" sheet for the period."""
from __future__ import annotations

from datetime import date, timedelta

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from config import CHARCOAL, CURRENCY, IVORY, SOFT, STONE
from db import METHODS, SIZE_NAMES, Database

MONEY = '#,##0.##'
DARK = PatternFill("solid", fgColor=CHARCOAL[1:])
LIGHT = PatternFill("solid", fgColor=SOFT[1:])
TITLE = Font(name="Calibri", size=16, bold=True, color=CHARCOAL[1:])
SUB = Font(name="Calibri", size=10, color=STONE[1:])
HEAD = Font(name="Calibri", bold=True, color=IVORY[1:])
BOLD = Font(name="Calibri", bold=True)
LINE = Border(top=Side(style="thin", color=CHARCOAL[1:]))


def default_filename(start: date, end: date) -> str:
    return f"UpSpace_{start:%Y-%m-%d}_to_{end:%Y-%m-%d}.xlsx"


def days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _header(ws, row: int, labels, money_cols=()):
    for c, text in enumerate(labels, start=1):
        cell = ws.cell(row=row, column=c, value=text)
        cell.font, cell.fill = HEAD, DARK
        cell.alignment = Alignment(horizontal="right" if c in money_cols else "left")


def _title(ws, title: str, subtitle: str):
    ws["A1"] = title
    ws["A1"].font = TITLE
    ws["A2"] = subtitle
    ws["A2"].font = SUB


def _widths(ws, widths):
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _items_table(ws, row: int, items, heading: str) -> int:
    ws.cell(row=row, column=1, value=heading).font = BOLD
    row += 1
    _header(ws, row, ["Item", "Size", "Qty", f"Total ({CURRENCY})"], money_cols=(3, 4))
    row += 1
    if not items:
        ws.cell(row=row, column=1, value="No items.").font = SUB
        return row + 2
    for it in items:
        ws.cell(row=row, column=1, value=it["name"])
        ws.cell(row=row, column=2, value=SIZE_NAMES.get(it["size"], it["size"] or ""))
        ws.cell(row=row, column=3, value=int(it["qty"]))
        ws.cell(row=row, column=4, value=round(it["total"], 2)).number_format = MONEY
        row += 1
    for c, v in ((1, "Total"), (3, sum(int(i["qty"]) for i in items)),
                 (4, round(sum(i["total"] for i in items), 2))):
        cell = ws.cell(row=row, column=c, value=v)
        cell.font, cell.border = BOLD, LINE
        if c == 4:
            cell.number_format = MONEY
    for c in (2,):
        ws.cell(row=row, column=c).border = LINE
    return row + 2


PCT = '0%'


def _accounts_table(ws, row: int, s: dict, stats: dict, heading: str) -> int:
    """Money in / out / balance per account and per method, with each one's share of money in."""
    income = sum(a["in"] for a in s["accounts"]) or 0
    ws.cell(row=row, column=1, value=heading).font = BOLD
    row += 1
    _header(ws, row, ["Account", "Share of money in", "", "Money in", "Money out", "Balance"], money_cols=(4, 5, 6))
    row += 1
    for a in s["accounts"]:
        ws.cell(row=row, column=1, value=a["label"] + ("" if a["active"] else " (removed)"))
        cell = ws.cell(row=row, column=2, value=(a["in"] / income) if income else 0)
        cell.number_format = PCT
        for c, k in ((4, "in"), (5, "out"), (6, "balance")):
            ws.cell(row=row, column=c, value=a[k]).number_format = MONEY
        row += 1
    row += 1
    _header(ws, row, ["Method", "Bills paid with it", "Share of money in", "Money in", "Money out", "Balance"],
            money_cols=(4, 5, 6))
    row += 1
    for m in stats["methods"].values():
        ws.cell(row=row, column=1, value=m["label"])
        ws.cell(row=row, column=2, value=m["bills"])
        ws.cell(row=row, column=3, value=(m["in"] / income) if income else 0).number_format = PCT
        for c, k in ((4, "in"), (5, "out"), (6, "balance")):
            ws.cell(row=row, column=c, value=m[k]).number_format = MONEY
        row += 1
    for c, v in ((1, "Total"), (2, stats["bills"]), (4, round(income, 2)),
                 (5, round(sum(a["out"] for a in s["accounts"]), 2)),
                 (6, round(sum(a["balance"] for a in s["accounts"]), 2))):
        cell = ws.cell(row=row, column=c, value=v)
        cell.font, cell.border = BOLD, LINE
        if c >= 4:
            cell.number_format = MONEY
    for c in (3,):
        ws.cell(row=row, column=c).border = LINE
    row += 1
    note = f"{stats['bills']} bill(s), {stats['mixed']} paid with more than one account."
    if stats["not_recorded"]:
        note += f" {stats['not_recorded']} closed before payment methods were added (not recorded)."
    ws.cell(row=row, column=1, value=note).font = SUB
    return row + 2


def _day_sheet(wb, db: Database, d: date, s: dict):
    ws = wb.create_sheet(d.strftime("%Y-%m-%d"))
    _title(ws, f"UpSpace · {d:%A %d %B %Y}", "Money from bills closed this day. Amounts in " + CURRENCY + ".")
    rows = [
        ("Room bookings", s["room_bookings"], None),
        ("Room hours", round(s["room_minutes"] / 60, 2), None),
        ("Room time", s["room_time_income"], MONEY),
        ("Hall bookings", s["hall_bookings"], None),
        ("Hall chairs", s["hall_chairs"], None),
        ("Hall chair-hours", s["hall_chair_hours"], None),
        ("Hall time", s["hall_time_income"], MONEY),
        ("Drinks & add-ons", s["items_income"], MONEY),
        ("Rounding to the pound", s["rounding"], MONEY),
        ("Money in", s["income_total"], MONEY),
        ("Expenses", s["expenses_total"], MONEY),
        ("Total (in − expenses)", s["net"], MONEY),
    ]
    r = 4
    _header(ws, r, ["Summary", "", "", "Amount"], money_cols=(4,))
    for label, value, fmt in rows:
        r += 1
        ws.cell(row=r, column=1, value=label)
        cell = ws.cell(row=r, column=4, value=value)
        if fmt:
            cell.number_format = fmt
        if label in ("Money in", "Total (in − expenses)"):
            for c in range(1, 5):
                ws.cell(row=r, column=c).font = BOLD
                ws.cell(row=r, column=c).fill = LIGHT
    r = _accounts_table(ws, r + 2, s, db.payment_stats(d, d), "Payments by account")
    r = _items_table(ws, r, db.items_sold_between(d, d), "Items consumed")

    ws.cell(row=r, column=1, value="Expenses").font = BOLD
    r += 1
    _header(ws, r, ["Description", "Time", "Paid from", f"Amount ({CURRENCY})"], money_cols=(4,))
    r += 1
    if not s["expenses"]:
        ws.cell(row=r, column=1, value="No expenses.").font = SUB
    for e in s["expenses"]:
        ws.cell(row=r, column=1, value=e["description"])
        ws.cell(row=r, column=2, value=e["created_at"][11:16])
        paid = db.payments_for("expense", e["id"])
        ws.cell(row=r, column=3, value=" + ".join(
            p["label"] + (f" ({p['amount']:g})" if len(paid) > 1 else "") for p in paid) or "Not recorded")
        ws.cell(row=r, column=4, value=e["amount"]).number_format = MONEY
        r += 1
    _widths(ws, [30, 14, 34, 16, 14, 14])
    ws.sheet_view.showGridLines = False


def build_report(db: Database, start: date, end: date, path: str) -> dict:
    """Write the .xlsx file. Returns the period totals."""
    error = db.check_period(start, end)
    if error:
        raise ValueError(error)
    wb = Workbook()
    wb.remove(wb.active)
    per_day = []
    for d in days(start, end):
        s = db.summary(d, d)
        per_day.append((d, s))
        if s["cards"] or s["expenses"]:
            _day_sheet(wb, db, d, s)

    ws = wb.create_sheet("Total")
    _title(ws, f"UpSpace · {start:%d %b %Y} to {end:%d %b %Y}",
           f"{len(per_day)} day(s). Amounts in {CURRENCY}. Days with no activity show as 0.")
    method_keys = [f"in_{m}" for m in METHODS]
    cols = (["Date", "Room bookings", "Room time", "Hall bookings", "Hall chairs", "Hall time",
             "Drinks & add-ons", "Rounding", "Money in"] + [f"In: {name}" for name in METHODS.values()]
            + ["Expenses", "Total (in − expenses)"])
    money_cols = tuple(c for c in range(3, len(cols) + 1) if c not in (4, 5))
    _header(ws, 4, cols, money_cols=money_cols)
    keys = (["room_bookings", "room_time_income", "hall_bookings", "hall_chairs", "hall_time_income",
             "items_income", "rounding", "income_total"] + method_keys + ["expenses_total", "net"])
    r = 4
    for d, s in per_day:
        for m in METHODS:
            s[f"in_{m}"] = round(sum(a["in"] for a in s["accounts"] if a["method"] == m), 2)
        r += 1
        ws.cell(row=r, column=1, value=d.strftime("%a %d %b %Y"))
        for c, k in enumerate(keys, start=2):
            cell = ws.cell(row=r, column=c, value=s[k])
            if c in money_cols:
                cell.number_format = MONEY
    r += 1
    totals = {k: round(sum(s[k] for _, s in per_day), 2) for k in keys}
    ws.cell(row=r, column=1, value="TOTAL")
    for c, k in enumerate(keys, start=2):
        cell = ws.cell(row=r, column=c, value=totals[k])
        if c in money_cols:
            cell.number_format = MONEY
    for c in range(1, len(cols) + 1):
        ws.cell(row=r, column=c).font = HEAD
        ws.cell(row=r, column=c).fill = DARK
    r = _accounts_table(ws, r + 2, db.summary(start, end), db.payment_stats(start, end),
                        "Payments by account and method (whole period)")
    _items_table(ws, r, db.items_sold_between(start, end), "Items consumed in the period")
    _widths(ws, [34, 14, 14, 14, 14, 14, 16, 12, 14, 14, 16, 14, 14, 20])
    ws.freeze_panes = "B5"
    ws.sheet_view.showGridLines = False
    wb.active = wb.index(ws)
    wb.save(path)
    return totals
