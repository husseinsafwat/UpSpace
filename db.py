"""SQLite storage for rooms, cards (bookings), items and expenses."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pricing
from config import HALL_TOTAL_CHAIRS

TS = "%Y-%m-%d %H:%M:%S"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    price_per_hour REAL NOT NULL,
    active         INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS cards (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL CHECK (kind IN ('room', 'hall')),
    customer_name  TEXT NOT NULL,
    phone          TEXT,
    room_id        INTEGER REFERENCES rooms(id),
    room_name      TEXT,
    rate           REAL,              -- room price per hour at booking time
    chairs         INTEGER,
    discount_type  TEXT NOT NULL DEFAULT 'none',
    discount_value REAL NOT NULL DEFAULT 0,
    start_time     TEXT NOT NULL,
    end_time       TEXT,
    status         TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'cancelled')),
    minutes        INTEGER,
    billed_hours   REAL,
    time_cost      REAL,
    items_total    REAL,
    total          REAL
);
CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id    INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    price      REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS expenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    amount      REAL NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status);
CREATE INDEX IF NOT EXISTS idx_cards_end ON cards(end_time);
CREATE INDEX IF NOT EXISTS idx_expenses_created ON expenses(created_at);
"""


class BookingError(Exception):
    """A rule was broken (room busy, not enough chairs, bad input...)."""


def now_str(dt: datetime | None = None) -> str:
    return (dt or datetime.now()).strftime(TS)


def parse_ts(value: str) -> datetime:
    return datetime.strptime(value, TS)


@dataclass
class Bill:
    minutes: int
    billed_hours: float
    rate: float | None
    effective_rate: float | None
    time_cost: float
    items_total: float
    total: float


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------------ rooms
    def list_rooms(self, active_only: bool = True):
        sql = "SELECT * FROM rooms"
        if active_only:
            sql += " WHERE active = 1"
        return self.conn.execute(sql + " ORDER BY name COLLATE NOCASE").fetchall()

    def _check_room_input(self, name: str, price) -> tuple[str, float]:
        name = (name or "").strip()
        if not name:
            raise BookingError("Room name is required.")
        try:
            price = float(price)
        except (TypeError, ValueError):
            raise BookingError("Price per hour must be a number.")
        if price <= 0:
            raise BookingError("Price per hour must be more than 0.")
        return name, price

    def _name_taken(self, name: str, exclude_id: int | None = None) -> bool:
        row = self.conn.execute(
            "SELECT id FROM rooms WHERE active = 1 AND lower(name) = lower(?) AND id IS NOT ?",
            (name, exclude_id)).fetchone()
        return row is not None

    def add_room(self, name: str, price) -> int:
        name, price = self._check_room_input(name, price)
        if self._name_taken(name):
            raise BookingError(f'A room called "{name}" already exists.')
        cur = self.conn.execute("INSERT INTO rooms (name, price_per_hour) VALUES (?, ?)", (name, price))
        self.conn.commit()
        return cur.lastrowid

    def update_room(self, room_id: int, name: str, price) -> None:
        name, price = self._check_room_input(name, price)
        if self._name_taken(name, room_id):
            raise BookingError(f'A room called "{name}" already exists.')
        self.conn.execute("UPDATE rooms SET name = ?, price_per_hour = ? WHERE id = ?", (name, price, room_id))
        self.conn.commit()

    def remove_room(self, room_id: int) -> None:
        if room_id in self.busy_room_ids():
            raise BookingError("This room is in use. Check the customer out first.")
        # Soft delete keeps old bills intact.
        self.conn.execute("UPDATE rooms SET active = 0 WHERE id = ?", (room_id,))
        self.conn.commit()

    def busy_room_ids(self) -> set[int]:
        rows = self.conn.execute("SELECT room_id FROM cards WHERE kind = 'room' AND status = 'open'")
        return {r["room_id"] for r in rows}

    def free_rooms(self):
        busy = self.busy_room_ids()
        return [r for r in self.list_rooms() if r["id"] not in busy]

    # ------------------------------------------------------------------ cards
    def chairs_in_use(self) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(chairs), 0) AS n FROM cards WHERE kind = 'hall' AND status = 'open'").fetchone()
        return int(row["n"])

    def free_chairs(self) -> int:
        return HALL_TOTAL_CHAIRS - self.chairs_in_use()

    @staticmethod
    def _check_customer(name: str) -> str:
        name = (name or "").strip()
        if not name:
            raise BookingError("Customer name is required.")
        return name

    def open_room_card(self, customer: str, phone: str, room_id: int, when: datetime | None = None) -> int:
        customer = self._check_customer(customer)
        room = self.conn.execute("SELECT * FROM rooms WHERE id = ? AND active = 1", (room_id,)).fetchone()
        if room is None:
            raise BookingError("Please choose a room.")
        if room_id in self.busy_room_ids():
            raise BookingError(f'"{room["name"]}" is already reserved right now.')
        cur = self.conn.execute(
            "INSERT INTO cards (kind, customer_name, phone, room_id, room_name, rate, start_time) "
            "VALUES ('room', ?, ?, ?, ?, ?, ?)",
            (customer, (phone or "").strip() or None, room_id, room["name"], room["price_per_hour"], now_str(when)))
        self.conn.commit()
        return cur.lastrowid

    def open_hall_card(self, customer: str, phone: str, chairs, when: datetime | None = None) -> int:
        customer = self._check_customer(customer)
        try:
            chairs = int(chairs)
        except (TypeError, ValueError):
            raise BookingError("Number of chairs must be a whole number.")
        if chairs < 1:
            raise BookingError("Number of chairs must be at least 1.")
        free = self.free_chairs()
        if chairs > free:
            raise BookingError(f"Only {free} chair(s) are free in the hall.")
        cur = self.conn.execute(
            "INSERT INTO cards (kind, customer_name, phone, chairs, start_time) VALUES ('hall', ?, ?, ?, ?)",
            (customer, (phone or "").strip() or None, chairs, now_str(when)))
        self.conn.commit()
        return cur.lastrowid

    def get_card(self, card_id: int):
        return self.conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()

    def open_cards(self, kind: str | None = None):
        sql, args = "SELECT * FROM cards WHERE status = 'open'", []
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        return self.conn.execute(sql + " ORDER BY start_time", args).fetchall()

    def set_discount(self, card_id: int, discount_type: str, value) -> None:
        card = self.get_card(card_id)
        if card is None or card["status"] != "open":
            raise BookingError("This card is not open.")
        if card["kind"] != "room":
            raise BookingError("Discounts are for rooms only.")
        try:
            value = float(value or 0)
        except (TypeError, ValueError):
            raise BookingError("Discount must be a number.")
        if discount_type == pricing.DISCOUNT_NONE:
            value = 0
        error = pricing.validate_discount(discount_type, value, card["rate"])
        if error:
            raise BookingError(error)
        self.conn.execute("UPDATE cards SET discount_type = ?, discount_value = ? WHERE id = ?",
                          (discount_type, value, card_id))
        self.conn.commit()

    def cancel_card(self, card_id: int) -> None:
        """Remove a booking made by mistake. It is not billed or counted."""
        self.conn.execute("UPDATE cards SET status = 'cancelled', end_time = ? WHERE id = ? AND status = 'open'",
                          (now_str(), card_id))
        self.conn.commit()

    # ------------------------------------------------------------------ items
    def add_item(self, card_id: int, name: str, price) -> int:
        name = (name or "").strip()
        if not name:
            raise BookingError("Item name is required.")
        try:
            price = float(price)
        except (TypeError, ValueError):
            raise BookingError("Item price must be a number.")
        if price <= 0:
            raise BookingError("Item price must be more than 0.")
        card = self.get_card(card_id)
        if card is None or card["status"] != "open":
            raise BookingError("This card is not open.")
        cur = self.conn.execute("INSERT INTO items (card_id, name, price, created_at) VALUES (?, ?, ?, ?)",
                                (card_id, name, price, now_str()))
        self.conn.commit()
        return cur.lastrowid

    def delete_item(self, item_id: int) -> None:
        self.conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        self.conn.commit()

    def card_items(self, card_id: int):
        return self.conn.execute("SELECT * FROM items WHERE card_id = ? ORDER BY id", (card_id,)).fetchall()

    def items_total(self, card_id: int) -> float:
        row = self.conn.execute("SELECT COALESCE(SUM(price), 0) AS t FROM items WHERE card_id = ?",
                                (card_id,)).fetchone()
        return float(row["t"])

    # ---------------------------------------------------------------- billing
    def compute_bill(self, card, end: datetime | None = None) -> Bill:
        end = end or datetime.now()
        minutes = pricing.elapsed_minutes((end - parse_ts(card["start_time"])).total_seconds())
        items_total = self.items_total(card["id"])
        if card["kind"] == "room":
            eff = pricing.discounted_rate(card["rate"], card["discount_type"], card["discount_value"])
            time_cost = pricing.room_time_cost(minutes, card["rate"], card["discount_type"], card["discount_value"])
            billed_hours = round(minutes / 60.0, 2)
            rate = card["rate"]
        else:
            billed_hours = pricing.hall_billed_hours(minutes)
            time_cost = pricing.hall_time_cost(minutes, card["chairs"])
            rate = eff = pricing.hall_price_per_chair(billed_hours)
        return Bill(minutes, billed_hours, rate, eff, time_cost, round(items_total, 2),
                    round(time_cost + items_total, 2))

    def close_card(self, card_id: int, end: datetime | None = None) -> Bill:
        card = self.get_card(card_id)
        if card is None or card["status"] != "open":
            raise BookingError("This card is not open.")
        end = end or datetime.now()
        bill = self.compute_bill(card, end)
        self.conn.execute(
            "UPDATE cards SET status = 'closed', end_time = ?, minutes = ?, billed_hours = ?, "
            "time_cost = ?, items_total = ?, total = ? WHERE id = ?",
            (now_str(end), bill.minutes, bill.billed_hours, bill.time_cost, bill.items_total, bill.total, card_id))
        self.conn.commit()
        return bill

    # --------------------------------------------------------------- expenses
    @staticmethod
    def _check_expense(description: str, amount) -> tuple[str, float]:
        description = (description or "").strip()
        if not description:
            raise BookingError("Expense description is required.")
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            raise BookingError("Expense amount must be a number.")
        if amount <= 0:
            raise BookingError("Expense amount must be more than 0.")
        return description, amount

    def add_expense(self, description: str, amount, when: datetime | None = None) -> int:
        description, amount = self._check_expense(description, amount)
        cur = self.conn.execute("INSERT INTO expenses (description, amount, created_at) VALUES (?, ?, ?)",
                                (description, amount, now_str(when)))
        self.conn.commit()
        return cur.lastrowid

    def update_expense(self, expense_id: int, description: str, amount) -> None:
        description, amount = self._check_expense(description, amount)
        self.conn.execute("UPDATE expenses SET description = ?, amount = ? WHERE id = ?",
                          (description, amount, expense_id))
        self.conn.commit()

    def delete_expense(self, expense_id: int) -> None:
        self.conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        self.conn.commit()

    # ---------------------------------------------------------------- summary
    @staticmethod
    def _range(start: date, end: date) -> tuple[str, str]:
        return (datetime.combine(start, datetime.min.time()).strftime(TS),
                datetime.combine(end + timedelta(days=1), datetime.min.time()).strftime(TS))

    def expenses_between(self, start: date, end: date):
        lo, hi = self._range(start, end)
        return self.conn.execute(
            "SELECT * FROM expenses WHERE created_at >= ? AND created_at < ? ORDER BY created_at",
            (lo, hi)).fetchall()

    def closed_cards_between(self, start: date, end: date):
        lo, hi = self._range(start, end)
        return self.conn.execute(
            "SELECT * FROM cards WHERE status = 'closed' AND end_time >= ? AND end_time < ? ORDER BY end_time",
            (lo, hi)).fetchall()

    def summary(self, start: date, end: date) -> dict:
        cards = self.closed_cards_between(start, end)
        rooms = [c for c in cards if c["kind"] == "room"]
        halls = [c for c in cards if c["kind"] == "hall"]
        expenses = self.expenses_between(start, end)
        s = {
            "room_bookings": len(rooms),
            "room_minutes": sum(c["minutes"] for c in rooms),
            "room_time_income": round(sum(c["time_cost"] for c in rooms), 2),
            "hall_bookings": len(halls),
            "hall_chairs": sum(c["chairs"] for c in halls),
            "hall_chair_hours": sum(c["chairs"] * c["billed_hours"] for c in halls),
            "hall_time_income": round(sum(c["time_cost"] for c in halls), 2),
            "items_income": round(sum(c["items_total"] for c in cards), 2),
            "expenses": expenses,
            "expenses_total": round(sum(e["amount"] for e in expenses), 2),
            "cards": cards,
        }
        s["income_total"] = round(s["room_time_income"] + s["hall_time_income"] + s["items_income"], 2)
        s["net"] = round(s["income_total"] - s["expenses_total"], 2)
        return s

    def running_estimate(self) -> tuple[int, float]:
        open_cards = self.open_cards()
        return len(open_cards), round(sum(self.compute_bill(c).total for c in open_cards), 2)
