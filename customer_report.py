"""Excel export of customer statistics: all customers ranked, or one customer's summary and visit history."""
from __future__ import annotations

from datetime import date

from openpyxl import Workbook

from config import CURRENCY
from db import Database
from pricing import fmt_duration
from report import BOLD, LINE, MONEY, SUB, _header, _items_table, _title, _widths


def period_text(start: date | None, end: date | None) -> str:
    return "All time" if start is None else f"{start:%d %b %Y} – {(end or date.today()):%d %b %Y}"


def default_filename(customer=None, start: date | None = None, end: date | None = None) -> str:
    who = "".join(ch for ch in customer["name"] if ch.isalnum() or ch in " -_").strip().replace(" ", "_") \
        if customer else "All_customers"
    when = "all_time" if start is None else f"{start:%Y-%m-%d}_to_{(end or date.today()):%Y-%m-%d}"
    return f"UpSpace_customers_{who}_{when}.xlsx"


def _place(c) -> str:
    return c["room_name"] if c["kind"] == "room" else f"Hall × {c['chairs']} chair(s)"


def _kv(ws, row: int, pairs) -> int:
    for k, v in pairs:
        ws.cell(row=row, column=1, value=k)
        cell = ws.cell(row=row, column=2, value=v)
        if isinstance(v, float):
            cell.number_format = MONEY
        row += 1
    return row + 1


def _summary_pairs(s) -> list:
    return [
        ("Visits", s["visits"]),
        ("Room visits", s["room_visits"]),
        ("Hall visits", s["hall_visits"]),
        ("Time spent", fmt_duration(s["minutes"])),
        ("Room time", fmt_duration(s["room_minutes"])),
        ("Hall chair-hours", round(float(s["hall_chair_hours"]), 2)),
        (f"Time cost ({CURRENCY})", float(s["time_spent"])),
        ("Drinks & add-ons (items)", s["items_count"]),
        (f"Drinks & add-ons ({CURRENCY})", float(s["items_spent"])),
        (f"Total spent ({CURRENCY})", float(s["total"])),
        (f"Average per visit ({CURRENCY})", float(s["average"])),
        ("First visit", (s["first_visit"] or "")[:10]),
        ("Last visit", (s["last_visit"] or "")[:10]),
    ]


def build_customer_report(db: Database, customer_id: int | None, start: date | None, end: date | None,
                          path: str) -> None:
    wb = Workbook()
    period = period_text(start, end)
    s = db.customer_stats(customer_id, start, end)

    if customer_id is None:
        ws = wb.active
        ws.title = "Customers"
        _title(ws, "UpSpace · Customers", f"{period} · closed bills only")
        row = _kv(ws, 4, [("Customers with visits", s["customers"]), ("Visits", s["visits"]),
                          (f"Total spent ({CURRENCY})", float(s["total"])),
                          (f"Average per visit ({CURRENCY})", float(s["average"]))])
        heads = ["#", "Customer", "Phone", "Visits", "Time", "Items", f"Items ({CURRENCY})",
                 f"Total spent ({CURRENCY})", "Last visit"]
        _header(ws, row, heads, money_cols=(4, 6, 7, 8))
        row += 1
        top = db.top_customers(start, end)
        for i, r in enumerate(top, start=1):
            values = [i, r["name"], r["phone"], r["visits"], fmt_duration(r["minutes"] or 0), int(r["items_count"]),
                      round(r["items_spent"] or 0, 2), round(r["total"] or 0, 2), (r["last_visit"] or "")[:10]]
            for c, v in enumerate(values, start=1):
                cell = ws.cell(row=row, column=c, value=v)
                if c in (7, 8):
                    cell.number_format = MONEY
            row += 1
        if not top:
            ws.cell(row=row, column=1, value="No customer visits in this period.").font = SUB
        _widths(ws, [6, 26, 16, 9, 12, 9, 14, 18, 14])
        items = wb.create_sheet("Items")
        _title(items, "Drinks & add-ons ordered by customers", period)
        _items_table(items, 4, s["items"], "Most ordered")
        _widths(items, [30, 12, 8, 14])
    else:
        cust = db.get_customer(customer_id)
        ws = wb.active
        ws.title = "Summary"
        _title(ws, f"UpSpace · {cust['name']}", f"Phone {cust['phone']} · {period} · closed bills only")
        row = 4
        if cust["notes"]:
            ws.cell(row=row, column=1, value="Notes")
            ws.cell(row=row, column=2, value=cust["notes"])
            row += 2
        _kv(ws, row, _summary_pairs(s))
        _widths(ws, [30, 20])

        visits = wb.create_sheet("Visits")
        _title(visits, f"Visits · {cust['name']}", period)
        heads = ["Date", "Place", "Start", "End", "Duration", "Items", f"Time ({CURRENCY})", f"Items ({CURRENCY})",
                 f"Total ({CURRENCY})", "Paid with"]
        _header(visits, 4, heads, money_cols=(7, 8, 9))
        row = 5
        for c in s["cards"]:
            paid = " + ".join(f"{p['label']} {p['amount']:g}" for p in db.payments_for("card", c["id"]))
            values = [c["start_time"][:10], _place(c), c["start_time"][11:16], c["end_time"][11:16],
                      fmt_duration(c["minutes"] or 0), db.card_items_text(c["id"]), round(c["time_cost"] or 0, 2),
                      round(c["items_total"] or 0, 2), round(c["total"] or 0, 2), paid]
            for col, v in enumerate(values, start=1):
                cell = visits.cell(row=row, column=col, value=v)
                if col in (7, 8, 9):
                    cell.number_format = MONEY
            row += 1
        if s["cards"]:
            for col, v in ((1, "Total"), (7, float(s["time_spent"])), (8, float(s["items_spent"])),
                           (9, float(s["total"]))):
                cell = visits.cell(row=row, column=col, value=v)
                cell.font, cell.border = BOLD, LINE
                if col > 1:
                    cell.number_format = MONEY
        else:
            visits.cell(row=row, column=1, value="No visits in this period.").font = SUB
        _widths(visits, [12, 22, 8, 8, 10, 40, 12, 12, 12, 34])

        items = wb.create_sheet("Items")
        _title(items, f"Drinks & add-ons · {cust['name']}", period)
        _items_table(items, 4, s["items"], "Ordered")
        _widths(items, [30, 12, 8, 14])
    wb.save(path)
