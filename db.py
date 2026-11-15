"""SQLite storage for rooms, cards (bookings), items, the drinks menu, customers, inventory, settings and expenses."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import auth
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
CREATE TABLE IF NOT EXISTS customers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    phone       TEXT NOT NULL UNIQUE,              -- digits only; the customer's key
    name        TEXT NOT NULL,
    notes       TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stock_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    unit        TEXT NOT NULL DEFAULT 'pcs',
    quantity    REAL NOT NULL DEFAULT 0,
    low_level   REAL NOT NULL DEFAULT 5,           -- warn when the stock is at or below this
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stock_moves (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_item_id INTEGER NOT NULL REFERENCES stock_items(id),
    change        REAL NOT NULL,                   -- + in, - out
    reason        TEXT NOT NULL CHECK (reason IN ('start', 'restock', 'sale', 'return', 'take_out', 'count')),
    note          TEXT,
    card_id       INTEGER,
    expense_id    INTEGER,
    balance       REAL NOT NULL,                   -- stock after this move
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,                   -- salted PBKDF2, never the password itself
    is_admin      INTEGER NOT NULL DEFAULT 0,
    active        INTEGER NOT NULL DEFAULT 1,
    deleted       INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_perms (
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    perm     TEXT NOT NULL,
    PRIMARY KEY (user_id, perm)
);
CREATE TABLE IF NOT EXISTS perm_templates (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT NOT NULL UNIQUE COLLATE NOCASE,
    perms  TEXT NOT NULL                           -- comma-separated permission keys
);
CREATE TABLE IF NOT EXISTS cash_drawer (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    day         TEXT NOT NULL,                     -- YYYY-MM-DD
    kind        TEXT NOT NULL CHECK (kind IN ('opening', 'add')),
    amount      REAL NOT NULL,
    note        TEXT,
    created_at  TEXT NOT NULL,
    created_by  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cash_drawer_day ON cash_drawer(day);
CREATE INDEX IF NOT EXISTS idx_stock_moves_item ON stock_moves(stock_item_id, id);
CREATE INDEX IF NOT EXISTS idx_payments_ref ON payments(kind, ref_id);
CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status);
CREATE INDEX IF NOT EXISTS idx_cards_end ON cards(end_time);
CREATE INDEX IF NOT EXISTS idx_expenses_created ON expenses(created_at);
"""


# Columns added after v1.1. Old databases get them on start-up (ALTER TABLE ... ADD COLUMN).
MIGRATIONS = {
    "cards": [("hall_tiers", "TEXT"),                  # hall prices copied onto the card at start
              ("customer_id", "INTEGER"),              # v2.1: link to customers (by phone)
              ("customer_removed", "INTEGER NOT NULL DEFAULT 0"),  # v2.2: its customer was deleted
              ("opened_by", "INTEGER"),                # v2.2: user who opened / closed the card
              ("closed_by", "INTEGER")],
    "expenses": [("created_by", "INTEGER")],
    "items": [("qty", "INTEGER NOT NULL DEFAULT 1"),   # price = qty x unit_price (line total)
              ("size", "TEXT"),
              ("unit_price", "REAL"),
              ("menu_item_id", "INTEGER"),
              ("stock_item_id", "INTEGER"),          # v2.1: stock taken for this line (given back on remove)
              ("stock_qty", "REAL")],
    "menu_items": [("stock_item_id", "INTEGER"),       # v2.1: inventory item used by one sale
                   ("stock_per_sale", "REAL NOT NULL DEFAULT 1")],
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


class PermissionDenied(BookingError):
    """The signed-in user isn't allowed to do this."""


class NameMismatch(BookingError):
    """The phone is already registered under another name. The UI offers to use the registered name."""

    def __init__(self, customer):
        self.customer = customer
        super().__init__(f"This phone ({customer['phone']}) is already registered with the name "
                         f"\"{customer['name']}\".")


class OutOfStock(BookingError):
    """Not enough of an inventory item for the items being added. The UI offers to open the Inventory."""

    def __init__(self, stock_item, needed: float, menu_name: str):
        self.stock_item, self.needed, self.menu_name = stock_item, needed, menu_name
        have = fmt_qty(stock_item["quantity"])
        super().__init__(f"\"{menu_name}\" is out of stock. It needs {fmt_qty(needed)} {stock_item['unit']} of "
                         f"\"{stock_item['name']}\", and only {have} {stock_item['unit']} left.\n"
                         "Add quantity to it in the Inventory page first.")


def card_perm(kind: str, action: str) -> str:
    """Permission key for an action on a card: ('room', 'checkout') -> 'rooms.checkout'."""
    return f"{'rooms' if kind == 'room' else 'hall'}.{action}"


def fmt_qty(q) -> str:
    q = float(q or 0)
    return str(int(q)) if q == int(q) else f"{q:g}"


def normalize_phone(phone) -> str:
    """Keep the digits only ('0100 123-4567' → '01001234567'). Raises BookingError if it isn't a phone."""
    digits = re.sub(r"\D", "", str(phone or ""))
    if not digits:
        raise BookingError("Phone number is required.")
    if not 8 <= len(digits) <= 15:
        raise BookingError("Phone number must have 8 to 15 digits.")
    return digits


STOCK_REASONS = {"start": "Opening stock", "restock": "Restock", "sale": "Sold on a card", "return": "Returned",
                 "take_out": "Taken out", "count": "Stock count"}


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
        self.path = path
        self.user = None                 # signed-in user row (None = no restrictions, e.g. tests and start-up)
        self._perms: set[str] = set()
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self._seed_menu()
        self._import_customers()
        self._seed_users()
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

    def _import_customers(self) -> None:
        """v2.1: create customers from old cards that have a phone (newest name wins) and link the cards."""
        rows = self.conn.execute("SELECT id, customer_name, phone, start_time FROM cards "
                                 "WHERE customer_id IS NULL AND customer_removed = 0 AND phone IS NOT NULL AND phone != '' "
                                 "ORDER BY start_time DESC, id DESC").fetchall()
        for r in rows:
            try:
                phone = normalize_phone(r["phone"])
            except BookingError:
                continue
            cust = self.find_customer(phone)
            if cust is None:
                cid = self.conn.execute("INSERT INTO customers (phone, name, created_at) VALUES (?, ?, ?)",
                                        (phone, r["customer_name"].strip(), r["start_time"])).lastrowid
            else:
                cid = cust["id"]
                if r["start_time"] < cust["created_at"]:
                    self.conn.execute("UPDATE customers SET created_at = ? WHERE id = ?", (r["start_time"], cid))
            self.conn.execute("UPDATE cards SET customer_id = ? WHERE id = ?", (cid, r["id"]))

    # ------------------------------------------------------------ users / auth
    def _seed_users(self) -> None:
        """First start of v2.2: the admin account and the "Normal user" template."""
        if self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            self.conn.execute("INSERT INTO users (email, name, password_hash, is_admin, created_at) "
                              "VALUES (?, 'Admin', ?, 1, ?)",
                              (auth.ADMIN_EMAIL, auth.hash_password(auth.ADMIN_PASSWORD), now_str()))
        if not self.get_setting("templates_seeded"):
            self.conn.execute("INSERT OR IGNORE INTO perm_templates (name, perms) VALUES (?, ?)",
                              (auth.NORMAL_USER, ",".join(auth.NORMAL_USER_PERMS)))
            self.set_setting("templates_seeded", "1", commit=False)

    def authenticate(self, email, password):
        """The user row for a correct email + password, else raise PermissionDenied with the reason."""
        row = self.conn.execute("SELECT * FROM users WHERE email = ? AND deleted = 0",
                                ((email or "").strip(),)).fetchone()
        if row is None or not auth.check_password(password or "", row["password_hash"]):
            raise PermissionDenied("Wrong email or password.")
        if not row["active"]:
            raise PermissionDenied("This account is disabled. Ask the admin.")
        return row

    def sign_in(self, user) -> None:
        """Set the signed-in user (None signs out). Permissions are checked against this user."""
        self.user = user
        self._perms = set(self.user_perms(user["id"])) if user is not None else set()

    def is_admin(self) -> bool:
        return self.user is None or bool(self.user["is_admin"])

    def can(self, perm: str) -> bool:
        """True when the signed-in user may do `perm` (the admin, and no user at all, may do anything)."""
        return self.is_admin() or perm in self._perms

    def can_any(self, *perms: str) -> bool:
        return any(self.can(p) for p in perms)

    def _need(self, perm: str) -> None:
        if not self.can(perm):
            raise PermissionDenied("You don't have permission to do this. Ask the admin.")

    def _need_admin(self) -> None:
        if not self.is_admin():
            raise PermissionDenied("Only the admin can do this.")

    def user_id(self):
        return self.user["id"] if self.user is not None else None

    def get_user(self, user_id):
        return self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def list_users(self):
        return self.conn.execute("SELECT * FROM users WHERE deleted = 0 ORDER BY is_admin DESC, name COLLATE NOCASE"
                                 ).fetchall()

    def user_names(self) -> dict:
        """{user id: name} for everyone, deleted users included (old records still show who did them)."""
        return {r["id"]: r["name"] for r in self.conn.execute("SELECT id, name FROM users")}

    def user_perms(self, user_id) -> list[str]:
        return [r["perm"] for r in self.conn.execute("SELECT perm FROM user_perms WHERE user_id = ?", (user_id,))]

    @staticmethod
    def _check_password_input(password) -> str:
        password = password or ""
        if len(password) < 4:
            raise BookingError("The password must have at least 4 characters.")
        return password

    def _check_user_input(self, name, email, exclude_id=None):
        name = " ".join((name or "").split())
        if not name:
            raise BookingError("Name is required.")
        email = (email or "").strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise BookingError("Enter a valid email, e.g. sara@gmail.com.")
        other = self.conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if other is not None and other["id"] != exclude_id:
            raise BookingError("Another user already has this email.")
        return name, email

    def _set_perms(self, user_id, perms) -> None:
        perms = [p for p in dict.fromkeys(perms or []) if p in auth.ALL_PERMISSIONS]
        self.conn.execute("DELETE FROM user_perms WHERE user_id = ?", (user_id,))
        self.conn.executemany("INSERT INTO user_perms (user_id, perm) VALUES (?, ?)", [(user_id, p) for p in perms])

    def add_user(self, name, email, password, perms=()) -> int:
        """Admin only. A new user starts with exactly the permissions given (none by default)."""
        self._need_admin()
        name, email = self._check_user_input(name, email)
        password = self._check_password_input(password)
        cur = self.conn.execute("INSERT INTO users (email, name, password_hash, created_at) VALUES (?, ?, ?, ?)",
                                (email, name, auth.hash_password(password), now_str()))
        self._set_perms(cur.lastrowid, perms)
        self.conn.commit()
        return cur.lastrowid

    def update_user(self, user_id, name, email, perms=None, active=True, password=None) -> None:
        """Admin only. The admin account always keeps every right and can't be disabled."""
        self._need_admin()
        u = self.get_user(user_id)
        if u is None or u["deleted"]:
            raise BookingError("That user no longer exists.")
        name, email = self._check_user_input(name, email, exclude_id=user_id)
        if u["is_admin"]:
            active = True
        self.conn.execute("UPDATE users SET name = ?, email = ?, active = ? WHERE id = ?",
                          (name, email, 1 if active else 0, user_id))
        if password:
            self.conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                              (auth.hash_password(self._check_password_input(password)), user_id))
        if perms is not None and not u["is_admin"]:
            self._set_perms(user_id, perms)
        self.conn.commit()
        if self.user is not None and self.user["id"] == user_id:
            self.sign_in(self.get_user(user_id))

    def delete_user(self, user_id) -> None:
        """Admin only. The account can't sign in again; bookings and expenses it made keep its name."""
        self._need_admin()
        u = self.get_user(user_id)
        if u is None or u["deleted"]:
            return
        if u["is_admin"]:
            raise BookingError("The admin account can't be deleted.")
        self.conn.execute("UPDATE users SET deleted = 1, active = 0, email = email || '#deleted' || id WHERE id = ?",
                          (user_id,))
        self.conn.execute("DELETE FROM user_perms WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def change_password(self, current, new, confirm) -> None:
        """The signed-in user changes their own password."""
        if self.user is None:
            raise PermissionDenied("Sign in first.")
        u = self.get_user(self.user["id"])
        if not auth.check_password(current or "", u["password_hash"]):
            raise BookingError("Your current password is wrong.")
        new = self._check_password_input(new)
        if new != (confirm or ""):
            raise BookingError("The new passwords don't match.")
        self.conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (auth.hash_password(new), u["id"]))
        self.conn.commit()
        self.sign_in(self.get_user(u["id"]))

    def list_templates(self):
        return self.conn.execute("SELECT * FROM perm_templates ORDER BY name COLLATE NOCASE").fetchall()

    @staticmethod
    def template_perms(template) -> list[str]:
        return [p for p in (template["perms"] or "").split(",") if p]

    def save_template(self, name, perms) -> int:
        """Admin only. Saving under an existing name replaces that template."""
        self._need_admin()
        name = " ".join((name or "").split())
        if not name:
            raise BookingError("Give the template a name.")
        perms = ",".join(p for p in dict.fromkeys(perms or []) if p in auth.ALL_PERMISSIONS)
        self.conn.execute("INSERT INTO perm_templates (name, perms) VALUES (?, ?) "
                          "ON CONFLICT(name) DO UPDATE SET perms = excluded.perms", (name, perms))
        self.conn.commit()
        return self.conn.execute("SELECT id FROM perm_templates WHERE name = ?", (name,)).fetchone()["id"]

    def delete_template(self, template_id) -> None:
        self._need_admin()
        self.conn.execute("DELETE FROM perm_templates WHERE id = ?", (template_id,))
        self.conn.commit()

    # ------------------------------------------------------------ reset (admin)
    RESET_TABLES = ("items", "payments", "cards", "expenses", "customers", "stock_moves", "cash_drawer")

    def reset_app_data(self, password, confirm) -> str | None:
        """Admin only: wipe all activity (bookings, bills, payments, expenses, customers, stock movements and
        cash drawer entries). Menu, rooms, prices, payment accounts, stock item names and users are kept and
        stock quantities go to 0. A backup copy of the database is saved first; its path is returned."""
        self._need_admin()
        if self.user is None:
            raise PermissionDenied("Sign in as the admin first.")
        if (password or "") != (confirm or ""):
            raise BookingError("The two passwords don't match.")
        if not auth.check_password(password or "", self.get_user(self.user["id"])["password_hash"]):
            raise BookingError("Wrong admin password.")
        backup = self.backup()
        for t in self.RESET_TABLES:
            self.conn.execute(f"DELETE FROM {t}")
        self.conn.execute("UPDATE stock_items SET quantity = 0")
        self.conn.execute("DELETE FROM sqlite_sequence WHERE name IN (%s)" % ",".join("?" * len(self.RESET_TABLES)),
                          self.RESET_TABLES)
        self.conn.commit()
        return backup

    def backup(self) -> str | None:
        """Copy the whole database to backups/upspace-YYYY-MM-DD-HHMMSS.db next to it (None for an in-memory db)."""
        import os
        if not self.path or self.path == ":memory:":
            return None
        folder = os.path.join(os.path.dirname(os.path.abspath(self.path)), "backups")
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, f"upspace-{datetime.now():%Y-%m-%d-%H%M%S}.db")
        target = sqlite3.connect(dest)
        try:
            self.conn.commit()
            self.conn.backup(target)
        finally:
            target.close()
        return dest

    # ------------------------------------------------------------ cash drawer
    def _check_cash_day(self, day: date) -> str:
        self._need("money.cash")
        if day != date.today() and not self.can("money.past_dates"):
            raise PermissionDenied("You can only change today's cash.")
        return day.isoformat()

    @staticmethod
    def _cash_amount(amount, allow_zero: bool) -> float:
        try:
            amount = round(float(str(amount if amount is not None else "").replace(",", "") or 0), 2)
        except ValueError:
            raise BookingError("The amount must be a number.")
        if amount < 0 or (amount == 0 and not allow_zero):
            raise BookingError("The amount must be more than 0." if not allow_zero else "The amount can't be negative.")
        return amount

    def set_opening_cash(self, day: date, amount) -> None:
        """The cash in the drawer when the day started (0 unless set)."""
        key = self._check_cash_day(day)
        amount = self._cash_amount(amount, allow_zero=True)
        self.conn.execute("DELETE FROM cash_drawer WHERE day = ? AND kind = 'opening'", (key,))
        self.conn.execute("INSERT INTO cash_drawer (day, kind, amount, created_at, created_by) "
                          "VALUES (?, 'opening', ?, ?, ?)", (key, amount, now_str(), self.user_id()))
        self.conn.commit()

    def add_cash(self, day: date, amount, note: str = "") -> int:
        """Extra cash put in the drawer during the day (not income)."""
        key = self._check_cash_day(day)
        amount = self._cash_amount(amount, allow_zero=False)
        cur = self.conn.execute("INSERT INTO cash_drawer (day, kind, amount, note, created_at, created_by) "
                                "VALUES (?, 'add', ?, ?, ?, ?)",
                                (key, amount, (note or "").strip() or None, now_str(), self.user_id()))
        self.conn.commit()
        return cur.lastrowid

    def update_cash(self, entry_id: int, amount, note: str = "") -> None:
        row = self.conn.execute("SELECT * FROM cash_drawer WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            return
        self._check_cash_day(date.fromisoformat(row["day"]))
        amount = self._cash_amount(amount, allow_zero=False)
        self.conn.execute("UPDATE cash_drawer SET amount = ?, note = ? WHERE id = ?",
                          (amount, (note or "").strip() or None, entry_id))
        self.conn.commit()

    def delete_cash(self, entry_id: int) -> None:
        row = self.conn.execute("SELECT * FROM cash_drawer WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            return
        self._check_cash_day(date.fromisoformat(row["day"]))
        self.conn.execute("DELETE FROM cash_drawer WHERE id = ?", (entry_id,))
        self.conn.commit()

    def cash_drawer(self, day: date, accounts=None) -> dict:
        """Opening cash, cash added, cash taken at checkout and cash paid for expenses on a day, and what
        should be in the drawer: opening + added + cash in − cash out. None of the drawer entries is income."""
        key = day.isoformat()
        rows = self.conn.execute("SELECT * FROM cash_drawer WHERE day = ? ORDER BY id", (key,)).fetchall()
        opening = next((r["amount"] for r in rows if r["kind"] == "opening"), 0.0)
        added = [r for r in rows if r["kind"] == "add"]
        accounts = accounts if accounts is not None else self.account_totals(day, day)
        cash_in = round(sum(a["in"] for a in accounts if a["method"] == "cash"), 2)
        cash_out = round(sum(a["out"] for a in accounts if a["method"] == "cash"), 2)
        added_total = round(sum(r["amount"] for r in added), 2)
        return {"opening": round(opening, 2), "added": added, "added_total": added_total,
                "cash_in": cash_in, "cash_out": cash_out,
                "drawer": round(opening + added_total + cash_in - cash_out, 2)}

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
        self._need("settings.hall")
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
        self._need("settings.accounts")
        number = self._check_account(method, number)
        pos = self.conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM payment_accounts").fetchone()[0]
        cur = self.conn.execute("INSERT INTO payment_accounts (method, number, position) VALUES (?, ?, ?)",
                                (method, number, pos))
        self.conn.commit()
        return cur.lastrowid

    def update_account(self, account_id: int, method: str, number: str) -> None:
        self._need("settings.accounts")
        acc = self.get_account(account_id)
        if acc is None or acc["method"] == "cash":
            raise BookingError("Cash can't be edited.")
        number = self._check_account(method, number, account_id)
        self.conn.execute("UPDATE payment_accounts SET method = ?, number = ? WHERE id = ?", (method, number, account_id))
        self.conn.commit()

    def remove_account(self, account_id: int) -> None:
        self._need("settings.accounts")
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
        if not self.can_any("menu.edit", "inventory.edit"):
            self._need("menu.edit")
        values = self._check_menu_input(name, category, price, sizes)
        pos = self.conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM menu_items").fetchone()[0]
        cur = self.conn.execute("INSERT INTO menu_items (name, category, price, price_s, price_m, price_l, position) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?)", (*values, pos))
        self.conn.commit()
        return cur.lastrowid

    def update_menu_item(self, item_id: int, name, category, price=None, sizes: dict | None = None) -> None:
        if not self.can_any("menu.edit", "inventory.edit"):
            self._need("menu.edit")
        values = self._check_menu_input(name, category, price, sizes, exclude_id=item_id)
        self.conn.execute("UPDATE menu_items SET name = ?, category = ?, price = ?, price_s = ?, price_m = ?, "
                          "price_l = ? WHERE id = ?", (*values, item_id))
        self.conn.commit()

    def set_menu_stock(self, item_id: int, stock_item_id, per_sale=1) -> None:
        """Link a menu item to an inventory item (None = not tracked, e.g. drinks made fresh)."""
        if not self.can_any("menu.edit", "inventory.edit"):
            self._need("menu.edit")
        if stock_item_id is not None:
            st = self.get_stock_item(stock_item_id)
            if st is None or not st["active"]:
                raise BookingError("Choose an inventory item.")
            try:
                per_sale = float(str(per_sale).replace(",", ""))
            except (TypeError, ValueError):
                raise BookingError("Units per sale must be a number.")
            if per_sale <= 0:
                raise BookingError("Units per sale must be more than 0.")
        else:
            per_sale = 1
        self.conn.execute("UPDATE menu_items SET stock_item_id = ?, stock_per_sale = ? WHERE id = ?",
                          (stock_item_id, per_sale, item_id))
        self.conn.commit()

    def remove_menu_item(self, item_id: int) -> None:
        self._need("menu.edit")
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
        self._need("settings.rooms")
        name, price = self._check_room_input(name, price)
        if self._name_taken(name):
            raise BookingError(f'A room called "{name}" already exists.')
        cur = self.conn.execute("INSERT INTO rooms (name, price_per_hour) VALUES (?, ?)", (name, price))
        self.conn.commit()
        return cur.lastrowid

    def update_room(self, room_id: int, name: str, price) -> None:
        self._need("settings.rooms")
        name, price = self._check_room_input(name, price)
        if self._name_taken(name, room_id):
            raise BookingError(f'A room called "{name}" already exists.')
        self.conn.execute("UPDATE rooms SET name = ?, price_per_hour = ? WHERE id = ?", (name, price, room_id))
        self.conn.commit()

    def remove_room(self, room_id: int) -> None:
        self._need("settings.rooms")
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
    def _check_name(name: str) -> str:
        name = " ".join((name or "").split())
        if not name:
            raise BookingError("Customer name is required.")
        return name

    def _booking_customer(self, name: str, phone: str, when: datetime | None):
        """The customer for a new card. A new phone registers a new customer; a known phone must come
        with its registered name (NameMismatch otherwise; names are changed only in the Customers tab)."""
        name = self._check_name(name)
        phone = normalize_phone(phone)
        cust = self.find_customer(phone)
        if cust is None:
            return None, name, phone
        if cust["name"].casefold() != name.casefold():
            raise NameMismatch(cust)
        return cust, cust["name"], phone

    def _link_customer(self, card_id: int, cust, name: str, phone: str, when: str) -> None:
        if cust is None:
            cid = self.conn.execute("INSERT INTO customers (phone, name, created_at) VALUES (?, ?, ?)",
                                    (phone, name, when)).lastrowid
        else:
            cid = cust["id"]
        self.conn.execute("UPDATE cards SET customer_id = ? WHERE id = ?", (cid, card_id))

    def open_room_card(self, customer: str, phone: str, room_id: int, when: datetime | None = None) -> int:
        self._need("rooms.open")
        cust, customer, phone = self._booking_customer(customer, phone, when)
        room = self.conn.execute("SELECT * FROM rooms WHERE id = ? AND active = 1", (room_id,)).fetchone()
        if room is None:
            raise BookingError("Please choose a room.")
        if room_id in self.busy_room_ids():
            raise BookingError(f'"{room["name"]}" is already reserved right now.')
        cur = self.conn.execute(
            "INSERT INTO cards (kind, customer_name, phone, room_id, room_name, rate, start_time, opened_by) "
            "VALUES ('room', ?, ?, ?, ?, ?, ?, ?)",
            (customer, phone, room_id, room["name"], room["price_per_hour"], now_str(when), self.user_id()))
        self._link_customer(cur.lastrowid, cust, customer, phone, now_str(when))
        self.conn.commit()
        return cur.lastrowid

    def open_hall_card(self, customer: str, phone: str, chairs, when: datetime | None = None) -> int:
        self._need("hall.open")
        cust, customer, phone = self._booking_customer(customer, phone, when)
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
            "INSERT INTO cards (kind, customer_name, phone, chairs, hall_tiers, start_time, opened_by) "
            "VALUES ('hall', ?, ?, ?, ?, ?, ?)",
            (customer, phone, chairs, pricing.tiers_to_text(self.hall_tiers()), now_str(when), self.user_id()))
        self._link_customer(cur.lastrowid, cust, customer, phone, now_str(when))
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
        self._need("rooms.discount")
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
        """Remove a booking made by mistake. It is not billed or counted; its items go back to stock."""
        card = self.get_card(card_id)
        if card is None or card["status"] != "open":
            return
        self._need(card_perm(card["kind"], "cancel"))
        for it in self.card_items(card_id):
            self._return_stock(it, card_id)
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
        self._need(card_perm(card["kind"], "items"))
        rows, need, names = [], {}, {}
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
            stock_id, stock_qty = self._menu_stock(item)
            if stock_id is not None:
                stock_qty *= qty
                need[stock_id] = need.get(stock_id, 0) + stock_qty
                names.setdefault(stock_id, item["name"])
            rows.append((card_id, item["name"], round(unit * qty, 2), now_str(), qty, size, unit, item["id"],
                         stock_id, stock_qty if stock_id is not None else None))
        if not rows:
            raise BookingError("Nothing to add. Use + to put items in the cart.")
        for stock_id, amount in need.items():          # all or nothing: check every stock item first
            st = self.get_stock_item(stock_id)
            if st["quantity"] + 1e-9 < amount:
                raise OutOfStock(st, amount, names[stock_id])
        for r in rows:
            self.conn.execute(
                "INSERT INTO items (card_id, name, price, created_at, qty, size, unit_price, menu_item_id, "
                "stock_item_id, stock_qty) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", r)
            if r[8] is not None:
                self._move(r[8], -r[9], "sale", f"{r[4]} × {r[1]}" + (f" ({r[5]})" if r[5] else ""), card_id=card_id)
        self.conn.commit()

    def _menu_stock(self, item):
        """(stock_item_id, units per sale) for a menu item linked to an active inventory item, else (None, 0)."""
        sid = item["stock_item_id"]
        if sid is None:
            return None, 0
        st = self.get_stock_item(sid)
        if st is None or not st["active"]:
            return None, 0
        return sid, float(item["stock_per_sale"] or 1)

    def _return_stock(self, item_row, card_id) -> None:
        if item_row["stock_item_id"] is not None and item_row["stock_qty"]:
            st = self.get_stock_item(item_row["stock_item_id"])
            if st is not None:
                self._move(st["id"], item_row["stock_qty"], "return", item_label(item_row), card_id=card_id)

    def delete_item(self, item_id: int) -> None:
        """Take an item off an open card; its stock goes back to the inventory."""
        row = self.conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            return
        card = self.get_card(row["card_id"])
        if card is None or card["status"] != "open":
            raise BookingError("This card is not open, so its items can't change.")
        self._need(card_perm(card["kind"], "items"))
        self._return_stock(row, row["card_id"])
        self.conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        self.conn.commit()

    def set_item_quantities(self, card_id: int, changes: dict) -> None:
        """Edit items already on an open card: {item_id: new quantity}. 0 removes the line. Lowering a
        quantity gives the stock back; raising it takes more (checked first, all or nothing)."""
        card = self.get_card(card_id)
        if card is None or card["status"] != "open":
            raise BookingError("This card is not open, so its items can't change.")
        self._need(card_perm(card["kind"], "items"))
        plan, need = [], {}
        for item_id, qty in changes.items():
            row = self.conn.execute("SELECT * FROM items WHERE id = ? AND card_id = ?", (item_id, card_id)).fetchone()
            if row is None:
                continue
            try:
                qty = int(qty)
            except (TypeError, ValueError):
                raise BookingError("Quantity must be a whole number.")
            if qty < 0:
                raise BookingError("Quantity can't be negative.")
            old = int(row["qty"] or 1)
            if qty == old:
                continue
            per_unit_stock = (row["stock_qty"] or 0) / old if row["stock_item_id"] is not None else 0
            delta_stock = per_unit_stock * (qty - old)
            if row["stock_item_id"] is not None and delta_stock > 0:
                need[row["stock_item_id"]] = need.get(row["stock_item_id"], 0) + delta_stock
            plan.append((row, old, qty, per_unit_stock, delta_stock))
        for stock_id, amount in need.items():
            st = self.get_stock_item(stock_id)
            if st is not None and st["quantity"] + 1e-9 < amount:
                name = next(r["name"] for r, *_ in plan if r["stock_item_id"] == stock_id)
                raise OutOfStock(st, amount, name)
        for row, old, qty, per_unit_stock, delta_stock in plan:
            label_ = item_label(row)
            if row["stock_item_id"] is not None and delta_stock and self.get_stock_item(row["stock_item_id"]):
                self._move(row["stock_item_id"], -delta_stock, "sale" if delta_stock > 0 else "return",
                           f"{label_} → {qty}", card_id=card_id)
            if qty == 0:
                self.conn.execute("DELETE FROM items WHERE id = ?", (row["id"],))
            else:
                unit = row["unit_price"] if row["unit_price"] is not None else row["price"] / old
                self.conn.execute("UPDATE items SET qty = ?, price = ?, stock_qty = ? WHERE id = ?",
                                  (qty, round(unit * qty, 2),
                                   per_unit_stock * qty if row["stock_item_id"] is not None else row["stock_qty"],
                                   row["id"]))
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
        self._need(card_perm(card["kind"], "checkout"))
        end = end or datetime.now()
        bill = self.compute_bill(card, end)
        checked = self._check_payments(payments, bill.total, "bill")
        self._save_payments("card", card_id, checked, now_str(end))
        self.conn.execute(
            "UPDATE cards SET status = 'closed', end_time = ?, minutes = ?, billed_hours = ?, "
            "time_cost = ?, items_total = ?, total = ?, closed_by = ? WHERE id = ?",
            (now_str(end), bill.minutes, bill.billed_hours, bill.time_cost, bill.items_total, bill.total,
             self.user_id(), card_id))
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
        self._need("money.add_expense")
        description, amount = self._check_expense(description, amount)
        checked = self._check_payments(payments, amount, "expense")
        ts = now_str(when)
        cur = self.conn.execute("INSERT INTO expenses (description, amount, created_at, created_by) "
                                "VALUES (?, ?, ?, ?)", (description, amount, ts, self.user_id()))
        self._save_payments("expense", cur.lastrowid, checked, ts)
        self.conn.commit()
        return cur.lastrowid

    def update_expense(self, expense_id: int, description: str, amount, payments=None) -> None:
        self._need("money.edit_expense")
        description, amount = self._check_expense(description, amount)
        checked = self._check_payments(payments, amount, "expense")
        row = self.conn.execute("SELECT created_at FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        self.conn.execute("UPDATE expenses SET description = ?, amount = ? WHERE id = ?",
                          (description, amount, expense_id))
        self._save_payments("expense", expense_id, checked, row["created_at"] if row else now_str())
        self.conn.commit()

    def delete_expense(self, expense_id: int) -> None:
        self._need("money.edit_expense")
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

    # -------------------------------------------------------------- customers
    def find_customer(self, phone):
        try:
            phone = normalize_phone(phone)
        except BookingError:
            return None
        return self.conn.execute("SELECT * FROM customers WHERE phone = ?", (phone,)).fetchone()

    def get_customer(self, customer_id: int):
        return self.conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()

    def _check_customer_input(self, name, phone, exclude_id=None):
        name = self._check_name(name)
        phone = normalize_phone(phone)
        other = self.find_customer(phone)
        if other is not None and other["id"] != exclude_id:
            raise BookingError(f"This phone ({phone}) is already registered with the name \"{other['name']}\".")
        return name, phone

    def add_customer(self, name, phone, notes: str = "") -> int:
        self._need("customers.edit")
        name, phone = self._check_customer_input(name, phone)
        cur = self.conn.execute("INSERT INTO customers (phone, name, notes, created_at) VALUES (?, ?, ?, ?)",
                                (phone, name, (notes or "").strip() or None, now_str()))
        self.conn.commit()
        return cur.lastrowid

    def update_customer(self, customer_id: int, name, phone, notes: str = "") -> None:
        """Change a customer's name/phone (only from the Customers tab). Their open cards show the new name."""
        self._need("customers.edit")
        name, phone = self._check_customer_input(name, phone, exclude_id=customer_id)
        self.conn.execute("UPDATE customers SET name = ?, phone = ?, notes = ? WHERE id = ?",
                          (name, phone, (notes or "").strip() or None, customer_id))
        self.conn.execute("UPDATE cards SET customer_name = ?, phone = ? WHERE customer_id = ? AND status = 'open'",
                          (name, phone, customer_id))
        self.conn.commit()

    def remove_customer(self, customer_id: int) -> None:
        """Delete the customer record. Their past bills stay exactly as they were (each bill keeps its own copy
        of the name, phone and amounts), so money totals and statistics don't change. Not allowed while the
        customer has an open card."""
        self._need("customers.delete")
        n = self.conn.execute("SELECT COUNT(*) FROM cards WHERE customer_id = ? AND status = 'open'",
                              (customer_id,)).fetchone()[0]
        if n:
            raise BookingError("This customer has an open card right now. Check it out or cancel it first.")
        self.conn.execute("UPDATE cards SET customer_id = NULL, customer_removed = 1 WHERE customer_id = ?",
                          (customer_id,))
        self.conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
        self.conn.commit()

    def search_customers(self, text: str, limit: int = 8):
        """Suggestions while typing on a new card: customers whose name contains the text, or whose phone
        contains its digits. Needs 2 or more letters / digits."""
        text = (text or "").strip()
        digits = re.sub(r"\D", "", text)
        if len(text) < 2:
            return []
        if len(digits) >= 2 and len(digits) >= len(text.replace(" ", "")) - 1:     # typing a phone
            sql, args = "phone LIKE ?", [f"%{digits}%"]
        else:
            sql, args = "name LIKE ?", [f"%{text}%"]
        return self.conn.execute(
            f"SELECT cu.*, (SELECT MAX(end_time) FROM cards c WHERE c.customer_id = cu.id) AS last_visit "
            f"FROM customers cu WHERE {sql} ORDER BY last_visit IS NULL, last_visit DESC, name COLLATE NOCASE "
            f"LIMIT {int(limit)}", args).fetchall()

    def removed_customer_stats(self) -> dict:
        """Visits and money from customers that were deleted (still part of every total)."""
        row = self.conn.execute("SELECT COUNT(*) AS visits, COALESCE(SUM(total), 0) AS spent FROM cards "
                                "WHERE customer_removed = 1 AND status = 'closed'").fetchone()
        return {"visits": row["visits"], "spent": round(row["spent"], 2)}

    def list_customers(self, search: str = ""):
        """Every customer with their lifetime numbers (closed bills only), most recent visit first."""
        q = f"%{(search or '').strip()}%"
        digits = re.sub(r"\D", "", search or "")
        return self.conn.execute(
            "SELECT cu.*, COUNT(c.id) AS visits, COALESCE(SUM(c.total), 0) AS spent, MAX(c.end_time) AS last_visit, "
            "(SELECT COUNT(*) FROM cards o WHERE o.customer_id = cu.id AND o.status = 'open') AS open_cards "
            "FROM customers cu LEFT JOIN cards c ON c.customer_id = cu.id AND c.status = 'closed' "
            "WHERE cu.name LIKE ? OR (? != '' AND cu.phone LIKE ?) "
            "GROUP BY cu.id ORDER BY COALESCE(MAX(c.end_time), cu.created_at) DESC",
            (q, digits, f"%{digits}%")).fetchall()

    def _cust_where(self, customer_id, start, end):
        sql, args = "c.status = 'closed' AND (c.customer_id IS NOT NULL OR c.customer_removed = 1)", []
        if customer_id is not None:
            sql += " AND c.customer_id = ?"
            args.append(customer_id)
        if start is not None:
            lo, hi = self._range(start, end or date.today())
            sql += " AND c.end_time >= ? AND c.end_time < ?"
            args += [lo, hi]
        return sql, args

    def customer_stats(self, customer_id=None, start: date | None = None, end: date | None = None) -> dict:
        """Numbers for one customer (or all customers) over a period (None = the whole life of the app)."""
        where, args = self._cust_where(customer_id, start, end)
        cards = self.conn.execute(f"SELECT c.* FROM cards c WHERE {where} ORDER BY c.end_time DESC", args).fetchall()
        rooms = [c for c in cards if c["kind"] == "room"]
        halls = [c for c in cards if c["kind"] == "hall"]
        total = round(sum(c["total"] for c in cards), 2)
        items = self.conn.execute(
            "SELECT i.name, i.size, SUM(COALESCE(i.qty, 1)) AS qty, SUM(i.price) AS total FROM items i "
            f"JOIN cards c ON c.id = i.card_id WHERE {where} "
            "GROUP BY i.name, COALESCE(i.size, '') ORDER BY qty DESC, total DESC, i.name", args).fetchall()
        return {
            "cards": cards,
            "visits": len(cards),
            "room_visits": len(rooms),
            "hall_visits": len(halls),
            "minutes": sum(c["minutes"] or 0 for c in cards),
            "room_minutes": sum(c["minutes"] or 0 for c in rooms),
            "hall_minutes": sum(c["minutes"] or 0 for c in halls),
            "hall_chair_hours": sum((c["chairs"] or 0) * (c["billed_hours"] or 0) for c in halls),
            "time_spent": round(sum(c["time_cost"] or 0 for c in cards), 2),
            "items_spent": round(sum(c["items_total"] or 0 for c in cards), 2),
            "items_count": int(sum(i["qty"] for i in items)),
            "total": total,
            "average": round(total / len(cards), 2) if cards else 0.0,
            "first_visit": min((c["start_time"] for c in cards), default=None),
            "last_visit": max((c["end_time"] for c in cards), default=None),
            "items": items,
            "customers": len({c["customer_id"] for c in cards if c["customer_id"] is not None}),
        }

    def top_customers(self, start: date | None = None, end: date | None = None, limit: int | None = None):
        """Customers ranked by money spent in the period."""
        where, args = self._cust_where(None, start, end)
        sql = ("SELECT cu.id, cu.name, cu.phone, COUNT(c.id) AS visits, SUM(c.minutes) AS minutes, "
               "SUM(c.items_total) AS items_spent, SUM(c.total) AS total, MAX(c.end_time) AS last_visit, "
               "(SELECT COALESCE(SUM(COALESCE(i.qty, 1)), 0) FROM items i JOIN cards c2 ON c2.id = i.card_id "
               f" WHERE c2.customer_id = cu.id AND {where.replace('c.', 'c2.')}) AS items_count "
               f"FROM cards c JOIN customers cu ON cu.id = c.customer_id WHERE {where} "
               "GROUP BY cu.id ORDER BY total DESC, visits DESC, cu.name")
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.conn.execute(sql, args + args).fetchall()

    def removed_customers_total(self, start: date | None = None, end: date | None = None):
        """One row for every deleted customer together, ranked like top_customers (None if there are none)."""
        where, args = self._cust_where(None, start, end)
        r = self.conn.execute(
            "SELECT COUNT(c.id) AS visits, SUM(c.minutes) AS minutes, SUM(c.items_total) AS items_spent, "
            "SUM(c.total) AS total, MAX(c.end_time) AS last_visit, "
            "(SELECT COALESCE(SUM(COALESCE(i.qty, 1)), 0) FROM items i JOIN cards c2 ON c2.id = i.card_id "
            f" WHERE c2.customer_removed = 1 AND {where.replace('c.', 'c2.')}) AS items_count "
            f"FROM cards c WHERE c.customer_removed = 1 AND {where}", args + args).fetchone()
        if not r["visits"]:
            return None
        return {"id": None, "name": "Deleted customers", "phone": "", "visits": r["visits"], "minutes": r["minutes"],
                "items_spent": r["items_spent"], "total": r["total"], "last_visit": r["last_visit"],
                "items_count": r["items_count"]}

    def card_items_text(self, card_id: int) -> str:
        return ", ".join(item_label(i) for i in self.card_items(card_id))

    # -------------------------------------------------------------- inventory
    def list_stock(self, active_only: bool = True):
        sql = "SELECT * FROM stock_items"
        if active_only:
            sql += " WHERE active = 1"
        return self.conn.execute(sql + " ORDER BY name COLLATE NOCASE").fetchall()

    def get_stock_item(self, stock_item_id):
        if stock_item_id is None:
            return None
        return self.conn.execute("SELECT * FROM stock_items WHERE id = ?", (stock_item_id,)).fetchone()

    @staticmethod
    def _qty(value, label: str, allow_zero: bool = False) -> float:
        try:
            value = float(str(value if value is not None else "").replace(",", "") or "x")
        except ValueError:
            raise BookingError(f"{label} must be a number.")
        if value < 0 or (value == 0 and not allow_zero):
            raise BookingError(f"{label} must be more than 0." if not allow_zero else f"{label} can't be negative.")
        return value

    def _check_stock_input(self, name, unit, low_level, exclude_id=None):
        name = " ".join((name or "").split())
        if not name:
            raise BookingError("Item name is required.")
        unit = " ".join((unit or "").split()) or "pcs"
        low = self._qty(low_level, "Low-stock alert", allow_zero=True)
        row = self.conn.execute("SELECT id FROM stock_items WHERE active = 1 AND lower(name) = lower(?) AND id IS NOT ?",
                                (name, exclude_id)).fetchone()
        if row:
            raise BookingError(f'"{name}" is already in the inventory.')
        return name, unit, low

    def add_stock_item(self, name, unit="pcs", quantity=0, low_level=5) -> int:
        self._need("inventory.edit")
        name, unit, low = self._check_stock_input(name, unit, low_level)
        qty = self._qty(quantity, "Starting quantity", allow_zero=True)
        cur = self.conn.execute("INSERT INTO stock_items (name, unit, quantity, low_level, created_at) "
                                "VALUES (?, ?, 0, ?, ?)", (name, unit, low, now_str()))
        if qty:
            self._move(cur.lastrowid, qty, "start")
        self.conn.commit()
        return cur.lastrowid

    def update_stock_item(self, stock_item_id: int, name, unit, low_level) -> None:
        self._need("inventory.edit")
        name, unit, low = self._check_stock_input(name, unit, low_level, exclude_id=stock_item_id)
        self.conn.execute("UPDATE stock_items SET name = ?, unit = ?, low_level = ? WHERE id = ?",
                          (name, unit, low, stock_item_id))
        self.conn.commit()

    def remove_stock_item(self, stock_item_id: int) -> None:
        """Soft delete; menu items linked to it stop being tracked."""
        self._need("inventory.edit")
        self.conn.execute("UPDATE stock_items SET active = 0 WHERE id = ?", (stock_item_id,))
        self.conn.execute("UPDATE menu_items SET stock_item_id = NULL WHERE stock_item_id = ?", (stock_item_id,))
        self.conn.commit()

    def _move(self, stock_item_id: int, change: float, reason: str, note: str | None = None, card_id=None,
              expense_id=None, when: datetime | None = None) -> None:
        """Change the stock and log it (no commit)."""
        st = self.get_stock_item(stock_item_id)
        balance = round(st["quantity"] + change, 4)
        self.conn.execute("UPDATE stock_items SET quantity = ? WHERE id = ?", (balance, stock_item_id))
        self.conn.execute("INSERT INTO stock_moves (stock_item_id, change, reason, note, card_id, expense_id, balance, "
                          "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (stock_item_id, round(change, 4), reason, (note or "").strip() or None, card_id, expense_id,
                           balance, now_str(when)))

    def restock(self, stock_item_id: int, quantity, note: str = "", cost=None, payments=None) -> None:
        """Add stock. With a cost, it's also recorded as an expense (payment required, like any expense)."""
        self._need("inventory.restock")
        st = self.get_stock_item(stock_item_id)
        if st is None or not st["active"]:
            raise BookingError("That inventory item no longer exists.")
        qty = self._qty(quantity, "Quantity")
        expense_id = None
        if cost is not None and str(cost).strip() != "":
            self._need("money.add_expense")
            desc = f"Restock: {fmt_qty(qty)} {st['unit']} {st['name']}" + (f" ({note.strip()})" if note.strip() else "")
            amount = self._qty(cost, "Cost")
            checked = self._check_payments(payments, amount, "expense")
            ts = now_str()
            expense_id = self.conn.execute("INSERT INTO expenses (description, amount, created_at, created_by) "
                                           "VALUES (?, ?, ?, ?)", (desc, amount, ts, self.user_id())).lastrowid
            self._save_payments("expense", expense_id, checked, ts)
        self._move(stock_item_id, qty, "restock", note, expense_id=expense_id)
        self.conn.commit()

    def take_out(self, stock_item_id: int, quantity, note: str) -> None:
        """Remove stock that wasn't sold on a card (waste, broken, own use...). A reason is required."""
        self._need("inventory.adjust")
        st = self.get_stock_item(stock_item_id)
        qty = self._qty(quantity, "Quantity")
        if not (note or "").strip():
            raise BookingError("Write the reason (for example: expired, broken, staff).")
        if qty > st["quantity"] + 1e-9:
            raise BookingError(f"Only {fmt_qty(st['quantity'])} {st['unit']} in stock.")
        self._move(stock_item_id, -qty, "take_out", note)
        self.conn.commit()

    def set_count(self, stock_item_id: int, counted, note: str = "") -> None:
        """Stock count: set the quantity to what was counted on the shelf; the difference is logged."""
        self._need("inventory.adjust")
        st = self.get_stock_item(stock_item_id)
        counted = self._qty(counted, "Counted quantity", allow_zero=True)
        diff = round(counted - st["quantity"], 4)
        if diff:
            self._move(stock_item_id, diff, "count", note)
            self.conn.commit()

    def stock_moves(self, stock_item_id: int | None = None, limit: int = 200):
        sql = ("SELECT m.*, s.name, s.unit FROM stock_moves m JOIN stock_items s ON s.id = m.stock_item_id")
        args = []
        if stock_item_id is not None:
            sql += " WHERE m.stock_item_id = ?"
            args.append(stock_item_id)
        return self.conn.execute(sql + " ORDER BY m.id DESC LIMIT ?", args + [limit]).fetchall()

    def sell_stock_item(self, stock_item_id: int, price, category: str, name: str | None = None) -> int:
        """Put an inventory item on the menu (so it shows in "+ Items"), linked 1 unit per sale.
        A menu item with the same name is linked instead of creating a duplicate."""
        self._need("inventory.edit")
        st = self.get_stock_item(stock_item_id)
        if st is None or not st["active"]:
            raise BookingError("That inventory item no longer exists.")
        name = " ".join((name or st["name"]).split())
        existing = self.conn.execute("SELECT id FROM menu_items WHERE active = 1 AND lower(name) = lower(?)",
                                     (name,)).fetchone()
        if existing:
            item_id = existing["id"]
        else:
            item_id = self.add_menu_item(name, category, price)
        self.set_menu_stock(item_id, stock_item_id, 1)
        return item_id

    def menu_items_for_stock(self, stock_item_id: int):
        return self.conn.execute("SELECT * FROM menu_items WHERE active = 1 AND stock_item_id = ? ORDER BY position",
                                 (stock_item_id,)).fetchall()

    def stock_left_for(self, menu_item) -> float | None:
        """How many more of a menu item can be sold from stock (None = not tracked)."""
        sid, per = self._menu_stock(menu_item)
        if sid is None:
            return None
        return self.get_stock_item(sid)["quantity"] / per
