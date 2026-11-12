"""SQLite storage for rooms, cards (bookings), items, the drinks menu, settings and expenses."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pricing
from config import HALL_HOUR_TIERS, HALL_TOTAL_CHAIRS

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
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS menu_items (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,
    category  TEXT NOT NULL,
    price     REAL,                  -- single price (item without sizes)
    price_s   REAL,                  -- sized item: Small / Medium / Large (blank = not sold in that size)
    price_m   REAL,
    price_l   REAL,
    active    INTEGER NOT NULL DEFAULT 1,
    position  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS payment_accounts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    method    TEXT NOT NULL CHECK (method IN ('cash', 'vodafone', 'instapay')),
    number    TEXT,                  -- phone number / InstaPay address (empty for Cash)
    active    INTEGER NOT NULL DEFAULT 1,
    position  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL CHECK (kind IN ('card', 'expense')),   -- money in (bill) / out (expense)
    ref_id      INTEGER NOT NULL,                                    -- cards.id or expenses.id
    account_id  INTEGER NOT NULL REFERENCES payment_accounts(id),
    label       TEXT NOT NULL,                                       -- account name when paid
    amount      REAL NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payments_ref ON payments(kind, ref_id);
CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status);
CREATE INDEX IF NOT EXISTS idx_cards_end ON cards(end_time);
CREATE INDEX IF NOT EXISTS idx_expenses_created ON expenses(created_at);
"""


# Columns added after v1.1. Old databases get them on start-up (ALTER TABLE ... ADD COLUMN).
MIGRATIONS = {
    "cards": [("hall_tiers", "TEXT")],                 # hall prices copied onto the card at start
    "items": [("qty", "INTEGER NOT NULL DEFAULT 1"),   # price = qty x unit_price (line total)
              ("size", "TEXT"),
              ("unit_price", "REAL"),
              ("menu_item_id", "INTEGER")],
}

SIZES = (("S", "Small", "price_s"), ("M", "Medium", "price_m"), ("L", "Large", "price_l"))
SIZE_NAMES = {code: name for code, name, _ in SIZES}

# Starting menu (from the UpSpace drinks menu). Everything is editable in the Menu tab.
# A number = one price; a dict = prices per size.
DEFAULT_MENU = [
    ("Hot Drinks", [("Tea", 15), ("Flavors Tea", 15), ("Tea with Milk", 35), ("Herbs", 20),
                    ("Espresso", {"S": 30, "M": 40}), ("Macchiato", {"S": 30, "M": 40}), ("Affogato", 45),
                    ("Cortado", 50), ("Americano", {"S": 45, "M": 55}), ("Latte", {"S": 55, "M": 65}),
                    ("Cappuccino", 65), ("Flat White", 60), ("Spanish Latte", 70), ("Mocha", 70),
                    ("Hot Chocolate", {"S": 45, "M": 55}), ("Turkish Coffee", {"S": 20, "M": 30}),
                    ("French Coffee", 40)]),
    ("Iced Coffee", [("Iced Americano", 55), ("Iced Latte", 55), ("Iced Cappuccino", 60), ("Iced Matcha", 60),
                     ("Iced Spanish Latte", 70), ("Iced Mocha", 70)]),
    ("Frappe", [("Coffee Frappe", 55), ("Caramel Frappe", 60), ("Chocolate Frappe", 60), ("Mocha Frappe", 65),
                ("Lotus Frappe", 70), ("Oreo Frappe", 70)]),
    ("Smoothies", [("Mango Smoothie", 55), ("Passion Fruit Smoothie", 50), ("Lemon Mint", 45),
                   ("Blueberry Smoothie", 40), ("Strawberry Smoothie", 40)]),
    ("Milk Shakes", [("Vanilla Shake", 50), ("Chocolate Shake", 50), ("Caramel Shake", 50),
                     ("Strawberry Shake", 55), ("Mango Shake", 60), ("Blueberry Shake", 55), ("Kinder Shake", 65),
                     ("Oreo Shake", 65), ("Lotus Shake", 65)]),
    ("Mojito", [("Classic Mojito", 50), ("Blue Mojito", 55), ("Sherry Cola", 55), ("Hammer Head", 70),
                ("Super Scotch", 55), ("Redbull Mojito", 70), ("Redbull Mix", 80), ("Special Groot", 60)]),
    ("Add-ons", [("Ice Cream Ball", 15), ("Flavor", 15)]),
]


def item_label(row) -> str:
    """'2 × Latte (M)' for a card item row; old free-text items show just their name."""
    qty = row["qty"] or 1
    name = row["name"] + (f" ({row['size']})" if row["size"] else "")
    return f"{qty} × {name}" if qty > 1 or row["menu_item_id"] else name


METHODS = {"cash": "Cash", "vodafone": "Vodafone Cash", "instapay": "InstaPay"}


def account_label(acc) -> str:
    """'Cash', 'Vodafone Cash · 01001234567'."""
    name = METHODS[acc["method"]]
    return f"{name} · {acc['number']}" if acc["number"] else name


class PaymentError(Exception):
    """Payment amounts don't match the bill / expense. The UI shows it in a popup."""


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
    total: float          # rounded to the nearest pound
    exact_total: float = 0.0

    @property
    def rounding(self) -> float:
        return round(self.total - self.exact_total, 2)


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self._seed_menu()
        if self.conn.execute("SELECT COUNT(*) FROM payment_accounts WHERE method = 'cash'").fetchone()[0] == 0:
            self.conn.execute("INSERT INTO payment_accounts (method, number, position) VALUES ('cash', NULL, 0)")
        self.conn.commit()

    def _migrate(self) -> None:
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def _seed_menu(self) -> None:
        """Fill the menu once, on first start. After that it's only changed from the Menu tab."""
        if self.get_setting("menu_seeded"):
            return
        if self.conn.execute("SELECT COUNT(*) FROM menu_items").fetchone()[0] == 0:
            pos = 0
            for category, entries in DEFAULT_MENU:
                for name, price in entries:
                    pos += 1
                    sized = isinstance(price, dict)
                    self.conn.execute(
                        "INSERT INTO menu_items (name, category, price, price_s, price_m, price_l, position) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (name, category, None if sized else price, *(
                            (price.get(c) if sized else None) for c, _, _ in SIZES), pos))
        self.set_setting("menu_seeded", "1", commit=False)

    # --------------------------------------------------------------- settings
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str, commit: bool = True) -> None:
        self.conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        if commit:
            self.conn.commit()

    def hall_chairs_total(self) -> int:
        return int(self.get_setting("hall_chairs", str(HALL_TOTAL_CHAIRS)))

    def hall_tiers(self) -> list[float]:
        return pricing.tiers_from_text(self.get_setting("hall_tiers")) or list(map(float, HALL_HOUR_TIERS))

    def set_hall_settings(self, chairs, tiers) -> None:
        """Chairs in the hall and the per-chair price for hours 1-4. New prices apply to new hall cards."""
        try:
            chairs = int(str(chairs).strip())
        except (TypeError, ValueError):
            raise BookingError("Number of chairs must be a whole number.")
        if chairs < 1:
            raise BookingError("The hall needs at least 1 chair.")
        used = self.chairs_in_use()
        if chairs < used:
            raise BookingError(f"{used} chair(s) are in use right now. The total can't be less than that.")
        clean = []
        for i, t in enumerate(tiers, start=1):
            try:
                t = float(t)
            except (TypeError, ValueError):
                raise BookingError(f"Hour {i} price must be a number.")
            if t < 0:
                raise BookingError(f"Hour {i} price can't be negative.")
            clean.append(t)
        if not clean or sum(clean) <= 0:
            raise BookingError("Enter the price per chair for the hours.")
        self.set_setting("hall_chairs", str(chairs), commit=False)
        self.set_setting("hall_tiers", pricing.tiers_to_text(clean), commit=False)
        self.conn.commit()

    # -------------------------------------------------------- payment accounts
    def list_accounts(self, active_only: bool = True):
        sql = "SELECT * FROM payment_accounts"
        if active_only:
            sql += " WHERE active = 1"
        return self.conn.execute(sql + " ORDER BY CASE method WHEN 'cash' THEN 0 WHEN 'vodafone' THEN 1 ELSE 2 END, "
                                       "position, id").fetchall()

    def cash_account_id(self) -> int:
        return self.conn.execute("SELECT id FROM payment_accounts WHERE method = 'cash' ORDER BY id").fetchone()[0]

    def _check_account(self, method: str, number: str, exclude_id=None) -> str:
        if method not in ("vodafone", "instapay"):
            raise BookingError("Choose Vodafone Cash or InstaPay.")
        number = " ".join((number or "").split())
        if not number:
            raise BookingError("Enter the number (or InstaPay address).")
        row = self.conn.execute("SELECT id FROM payment_accounts WHERE active = 1 AND method = ? "
                                "AND lower(number) = lower(?) AND id IS NOT ?", (method, number, exclude_id)).fetchone()
        if row:
            raise BookingError(f"{METHODS[method]} {number} is already added.")
        return number

    def add_account(self, method: str, number: str) -> int:
        number = self._check_account(method, number)
        pos = self.conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM payment_accounts").fetchone()[0]
        cur = self.conn.execute("INSERT INTO payment_accounts (method, number, position) VALUES (?, ?, ?)",
                                (method, number, pos))
        self.conn.commit()
        return cur.lastrowid

    def update_account(self, account_id: int, method: str, number: str) -> None:
        acc = self.get_account(account_id)
        if acc is None or acc["method"] == "cash":
            raise BookingError("Cash can't be edited.")
        number = self._check_account(method, number, account_id)
        self.conn.execute("UPDATE payment_accounts SET method = ?, number = ? WHERE id = ?", (method, number, account_id))
        self.conn.commit()

    def remove_account(self, account_id: int) -> None:
        acc = self.get_account(account_id)
        if acc is None or acc["method"] == "cash":
            raise BookingError("Cash can't be removed.")
        # Soft delete: old bills and expenses keep the account's name.
        self.conn.execute("UPDATE payment_accounts SET active = 0 WHERE id = ?", (account_id,))
        self.conn.commit()

    def get_account(self, account_id: int):
        return self.conn.execute("SELECT * FROM payment_accounts WHERE id = ?", (account_id,)).fetchone()

    def _check_payments(self, payments, total: float, what: str) -> list[tuple]:
        """payments: [(account_id, amount or None), ...].

        One account → it takes the whole amount (its own amount may be left empty).
        Two or more → every amount is required and they must add up exactly to the total.
        Returns [(account_row, amount)]. Raises PaymentError with a message for the popup.
        """
        chosen = [(a, amt) for a, amt in (payments or []) if a is not None]
        if not chosen:
            raise PaymentError(f"Choose how the {what} was paid (Cash, Vodafone Cash or InstaPay).")
        ids = [a for a, _ in chosen]
        if len(set(ids)) != len(ids):
            raise PaymentError("Each account can only be chosen once.")
        rows = []
        for account_id, _ in chosen:
            acc = self.get_account(account_id)
            if acc is None or not acc["active"]:
                raise PaymentError("That payment account no longer exists. Choose another one.")
            rows.append(acc)
        total = round(float(total), 2)
        if len(chosen) == 1:
            return [(rows[0], total)]
        out = []
        for acc, (_, amount) in zip(rows, chosen):
            if amount is None or (isinstance(amount, str) and not amount.strip()):
                raise PaymentError(f"Enter the amount for {account_label(acc)}.")
            try:
                amount = round(float(str(amount).replace(",", "")), 2)
            except ValueError:
                raise PaymentError(f"The amount for {account_label(acc)} must be a number.")
            if amount <= 0:
                raise PaymentError(f"The amount for {account_label(acc)} must be more than 0.")
            out.append((acc, amount))
        paid = round(sum(a for _, a in out), 2)
        if abs(paid - total) > 0.001:
            diff = pricing.fmt_money(abs(paid - total))
            more_less = f"{diff} more than" if paid > total else f"{diff} less than"
            raise PaymentError(f"The payments add up to {pricing.fmt_money(paid)}, but the {what} is "
                               f"{pricing.fmt_money(total)} ({more_less} it).\n"
                               f"The amounts must add up exactly to {pricing.fmt_money(total)}.")
        return out

    def _save_payments(self, kind: str, ref_id: int, checked, when: str) -> None:
        self.conn.execute("DELETE FROM payments WHERE kind = ? AND ref_id = ?", (kind, ref_id))
        self.conn.executemany(
            "INSERT INTO payments (kind, ref_id, account_id, label, amount, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [(kind, ref_id, acc["id"], account_label(acc), amount, when) for acc, amount in checked])

    def payments_for(self, kind: str, ref_id: int):
        return self.conn.execute("SELECT * FROM payments WHERE kind = ? AND ref_id = ? ORDER BY id",
                                 (kind, ref_id)).fetchall()

    def account_totals(self, start: date, end: date) -> list[dict]:
        """Per account: money in (bills closed), money out (expenses) and the balance, for the period.
        Uses the bill's close time and the expense's time, like the Money page."""
        lo, hi = self._range(start, end)
        rows = self.conn.execute(
            "SELECT p.account_id, p.kind, SUM(p.amount) AS amount FROM payments p "
            "LEFT JOIN cards c ON p.kind = 'card' AND c.id = p.ref_id "
            "LEFT JOIN expenses e ON p.kind = 'expense' AND e.id = p.ref_id "
            "WHERE (p.kind = 'card' AND c.status = 'closed' AND c.end_time >= ? AND c.end_time < ?) "
            "   OR (p.kind = 'expense' AND e.created_at >= ? AND e.created_at < ?) "
            "GROUP BY p.account_id, p.kind", (lo, hi, lo, hi)).fetchall()
        sums: dict[int, dict] = {}
        for r in rows:
            d = sums.setdefault(r["account_id"], {"in": 0.0, "out": 0.0})
            d["in" if r["kind"] == "card" else "out"] += r["amount"]
        out = []
        for acc in self.list_accounts(active_only=False):
            d = sums.get(acc["id"])
            if d is None and not acc["active"]:
                continue
            d = d or {"in": 0.0, "out": 0.0}
            out.append({"id": acc["id"], "label": account_label(acc), "method": acc["method"],
                        "active": bool(acc["active"]), "in": round(d["in"], 2), "out": round(d["out"], 2),
                        "balance": round(d["in"] - d["out"], 2)})
        return out

    def payment_stats(self, start: date, end: date) -> dict:
        """For the report: per method money in/out and how many bills used it, plus mixed-payment bills."""
        lo, hi = self._range(start, end)
        bills = self.conn.execute(
            "SELECT c.id, GROUP_CONCAT(DISTINCT a.method) AS methods, COUNT(p.id) AS n FROM cards c "
            "LEFT JOIN payments p ON p.kind = 'card' AND p.ref_id = c.id "
            "LEFT JOIN payment_accounts a ON a.id = p.account_id "
            "WHERE c.status = 'closed' AND c.end_time >= ? AND c.end_time < ? GROUP BY c.id", (lo, hi)).fetchall()
        methods = {m: {"label": name, "in": 0.0, "out": 0.0, "bills": 0} for m, name in METHODS.items()}
        for acc in self.account_totals(start, end):
            methods[acc["method"]]["in"] += acc["in"]
            methods[acc["method"]]["out"] += acc["out"]
        for b in bills:
            for m in (b["methods"] or "").split(","):
                if m:
                    methods[m]["bills"] += 1
        for m in methods.values():
            m["in"], m["out"] = round(m["in"], 2), round(m["out"], 2)
            m["balance"] = round(m["in"] - m["out"], 2)
        return {"methods": methods, "bills": len(bills),
                "mixed": sum(1 for b in bills if b["n"] > 1),
                "not_recorded": sum(1 for b in bills if b["n"] == 0)}

    # ------------------------------------------------------------------- menu
    def list_menu(self, active_only: bool = True):
        sql = "SELECT * FROM menu_items"
        if active_only:
            sql += " WHERE active = 1"
        return self.conn.execute(sql + " ORDER BY position, id").fetchall()

    def menu_categories(self) -> list[str]:
        seen = []
        for r in self.list_menu():
            if r["category"] not in seen:
                seen.append(r["category"])
        return seen

    @staticmethod
    def item_prices(menu_item) -> dict[str | None, float]:
        """{None: price} for a one-price item, or {"S": .., "M": ..} for the sizes it's sold in."""
        sized = {c: menu_item[col] for c, _, col in SIZES if menu_item[col] is not None}
        return sized or {None: menu_item["price"]}

    @staticmethod
    def _money_or_none(value, label: str):
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        try:
            value = float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            raise BookingError(f"{label} must be a number.")
        if value <= 0:
            raise BookingError(f"{label} must be more than 0.")
        return value

    def _check_menu_input(self, name, category, price, sizes, exclude_id=None):
        name = (name or "").strip()
        category = (category or "").strip()
        if not name:
            raise BookingError("Item name is required.")
        if not category:
            raise BookingError("Category is required.")
        row = self.conn.execute(
            "SELECT id FROM menu_items WHERE active = 1 AND lower(name) = lower(?) AND id IS NOT ?",
            (name, exclude_id)).fetchone()
        if row:
            raise BookingError(f'"{name}" is already on the menu.')
        if sizes is None:
            price = self._money_or_none(price, "Price")
            if price is None:
                raise BookingError("Price is required.")
            return name, category, price, None, None, None
        sized = [self._money_or_none(sizes.get(c), f"{label} price") for c, label, _ in SIZES]
        if sum(p is not None for p in sized) < 2:
            raise BookingError("An item with sizes needs a price for at least two sizes.")
        return (name, category, None, *sized)

    def add_menu_item(self, name, category, price=None, sizes: dict | None = None) -> int:
        """sizes=None → one price. sizes={"S": 30, "M": 40} → sold in those sizes."""
        values = self._check_menu_input(name, category, price, sizes)
        pos = self.conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM menu_items").fetchone()[0]
        cur = self.conn.execute("INSERT INTO menu_items (name, category, price, price_s, price_m, price_l, position) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?)", (*values, pos))
        self.conn.commit()
        return cur.lastrowid

    def update_menu_item(self, item_id: int, name, category, price=None, sizes: dict | None = None) -> None:
        values = self._check_menu_input(name, category, price, sizes, exclude_id=item_id)
        self.conn.execute("UPDATE menu_items SET name = ?, category = ?, price = ?, price_s = ?, price_m = ?, "
                          "price_l = ? WHERE id = ?", (*values, item_id))
        self.conn.commit()

    def remove_menu_item(self, item_id: int) -> None:
        # Soft delete: bills already made keep the item's name and price.
        self.conn.execute("UPDATE menu_items SET active = 0 WHERE id = ?", (item_id,))
        self.conn.commit()

    def get_menu_item(self, item_id: int):
        return self.conn.execute("SELECT * FROM menu_items WHERE id = ?", (item_id,)).fetchone()

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
        return self.hall_chairs_total() - self.chairs_in_use()

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
            "INSERT INTO cards (kind, customer_name, phone, chairs, hall_tiers, start_time) "
            "VALUES ('hall', ?, ?, ?, ?, ?)",
            (customer, (phone or "").strip() or None, chairs, pricing.tiers_to_text(self.hall_tiers()),
             now_str(when)))
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
    def card_tiers(self, card) -> list[float]:
        """Hall prices the card started with (cards from before v1.2 use the current settings)."""
        return pricing.tiers_from_text(card["hall_tiers"]) or self.hall_tiers()

    def add_items(self, card_id: int, lines) -> None:
        """Add menu items to an open card in one go.

        lines: [(menu_item_id, size or None, qty), ...]. The name and price are copied from the menu
        now, so later menu changes don't touch this card's bill.
        """
        card = self.get_card(card_id)
        if card is None or card["status"] != "open":
            raise BookingError("This card is not open.")
        rows = []
        for menu_item_id, size, qty in lines:
            try:
                qty = int(qty)
            except (TypeError, ValueError):
                raise BookingError("Quantity must be a whole number.")
            if qty < 1:
                continue
            item = self.get_menu_item(menu_item_id)
            if item is None or not item["active"]:
                raise BookingError("That item is no longer on the menu.")
            prices = self.item_prices(item)
            if size not in prices:
                raise BookingError(f'Choose a size for "{item["name"]}".')
            unit = prices[size]
            rows.append((card_id, item["name"], round(unit * qty, 2), now_str(), qty, size, unit, item["id"]))
        if not rows:
            raise BookingError("Nothing to add. Use + to put items in the cart.")
        self.conn.executemany(
            "INSERT INTO items (card_id, name, price, created_at, qty, size, unit_price, menu_item_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        self.conn.commit()

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
            tiers = self.card_tiers(card)
            billed_hours = pricing.hall_billed_hours(minutes)
            time_cost = pricing.hall_time_cost(minutes, card["chairs"], tiers)
            rate = eff = pricing.hall_price_per_chair(billed_hours, tiers)
        exact = round(time_cost + items_total, 2)
        return Bill(minutes, billed_hours, rate, eff, time_cost, round(items_total, 2),
                    pricing.round_pound(exact), exact)

    def close_card(self, card_id: int, end: datetime | None = None, payments=None) -> Bill:
        """payments: [(account_id, amount or None), ...], see _check_payments. Required."""
        card = self.get_card(card_id)
        if card is None or card["status"] != "open":
            raise BookingError("This card is not open.")
        end = end or datetime.now()
        bill = self.compute_bill(card, end)
        checked = self._check_payments(payments, bill.total, "bill")
        self._save_payments("card", card_id, checked, now_str(end))
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

    def add_expense(self, description: str, amount, when: datetime | None = None, payments=None) -> int:
        """payments: which account(s) the money came from, same rules as a bill. Required."""
        description, amount = self._check_expense(description, amount)
        checked = self._check_payments(payments, amount, "expense")
        ts = now_str(when)
        cur = self.conn.execute("INSERT INTO expenses (description, amount, created_at) VALUES (?, ?, ?)",
                                (description, amount, ts))
        self._save_payments("expense", cur.lastrowid, checked, ts)
        self.conn.commit()
        return cur.lastrowid

    def update_expense(self, expense_id: int, description: str, amount, payments=None) -> None:
        description, amount = self._check_expense(description, amount)
        checked = self._check_payments(payments, amount, "expense")
        row = self.conn.execute("SELECT created_at FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        self.conn.execute("UPDATE expenses SET description = ?, amount = ? WHERE id = ?",
                          (description, amount, expense_id))
        self._save_payments("expense", expense_id, checked, row["created_at"] if row else now_str())
        self.conn.commit()

    def delete_expense(self, expense_id: int) -> None:
        self.conn.execute("DELETE FROM payments WHERE kind = 'expense' AND ref_id = ?", (expense_id,))
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
        # Bills are rounded to the pound; the difference is shown as its own line.
        s["rounding"] = round(sum(c["total"] - c["time_cost"] - c["items_total"] for c in cards), 2)
        s["income_total"] = round(sum(c["total"] for c in cards), 2)
        s["accounts"] = self.account_totals(start, end)
        s["net"] = round(s["income_total"] - s["expenses_total"], 2)
        return s

    def first_data_date(self) -> date | None:
        """The first day the app has anything recorded (a booking or an expense)."""
        row = self.conn.execute(
            "SELECT MIN(d) AS d FROM (SELECT MIN(start_time) AS d FROM cards WHERE status != 'cancelled' "
            "UNION ALL SELECT MIN(created_at) FROM expenses)").fetchone()
        return parse_ts(row["d"]).date() if row and row["d"] else None

    def check_period(self, start: date, end: date, today: date | None = None) -> str | None:
        """Error message for a report period, or None if it's fine.
        The period must be inside [first day with data, today]."""
        today = today or date.today()
        first = self.first_data_date()
        if first is None:
            return "There's no data yet, so there's nothing to report."
        if start > end:
            return "The From date must be on or before the To date."
        if end > today:
            return f"The To date can't be after today ({today:%d %b %Y})."
        if start < first:
            return f"The From date can't be before {first:%d %b %Y}, the first day the app has data."
        return None

    def items_sold_between(self, start: date, end: date):
        """Items on bills closed in the period, added up per item and size (most sold first)."""
        lo, hi = self._range(start, end)
        return self.conn.execute(
            "SELECT i.name, i.size, SUM(COALESCE(i.qty, 1)) AS qty, SUM(i.price) AS total "
            "FROM items i JOIN cards c ON c.id = i.card_id "
            "WHERE c.status = 'closed' AND c.end_time >= ? AND c.end_time < ? "
            "GROUP BY i.name, COALESCE(i.size, '') ORDER BY qty DESC, total DESC, i.name",
            (lo, hi)).fetchall()

    def running_estimate(self) -> tuple[int, float]:
        open_cards = self.open_cards()
        return len(open_cards), round(sum(self.compute_bill(c).total for c in open_cards), 2)
