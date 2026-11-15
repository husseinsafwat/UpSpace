"""Passwords and the list of permissions an admin can give to a user."""
from __future__ import annotations

import hashlib
import hmac
import os

ADMIN_EMAIL = "admin@gmail.com"
ADMIN_PASSWORD = "admin"           # starting password of the admin account; stored only as a hash

ITERATIONS = 120_000


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    """Salted PBKDF2-SHA256: 'pbkdf2_sha256$<iterations>$<salt hex>$<hash hex>'."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str | None) -> bool:
    try:
        algo, iterations, salt, digest = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        test = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), bytes.fromhex(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(test.hex(), digest)


# Every permission, grouped by page, in the order the admin sees them. "view" shows the page in the sidebar.
PERMISSION_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Rooms", [("rooms.view", "View the page"), ("rooms.open", "Open cards"),
               ("rooms.items", "Add / edit items on cards"), ("rooms.discount", "Give discounts"),
               ("rooms.checkout", "Checkout"), ("rooms.cancel", "Cancel cards")]),
    ("Hall", [("hall.view", "View the page"), ("hall.open", "Open cards"),
              ("hall.items", "Add / edit items on cards"), ("hall.checkout", "Checkout"),
              ("hall.cancel", "Cancel cards")]),
    ("Customers", [("customers.view", "View the customer list"), ("customers.edit", "Add / edit customers"),
                   ("customers.delete", "Delete customers"), ("customers.stats", "View statistics"),
                   ("customers.export", "Export statistics to Excel")]),
    ("Menu", [("menu.view", "View the page"), ("menu.edit", "Add / edit / remove items")]),
    ("Inventory", [("inventory.view", "View the page"), ("inventory.restock", "Restock"),
                   ("inventory.adjust", "Take out / count stock"), ("inventory.edit", "Add / edit / remove stock items")]),
    ("Money", [("money.view_in", "View money in (bills, accounts)"), ("money.view_expenses", "View expenses"),
               ("money.add_expense", "Add expenses"), ("money.edit_expense", "Edit / delete expenses"),
               ("money.cash", "Set opening cash / add cash"), ("money.past_dates", "View previous dates"),
               ("money.report", "Download the report")]),
    ("Settings", [("settings.view", "View the page"), ("settings.hall", "Edit hall chairs and prices"),
                  ("settings.rooms", "Add / edit / remove rooms"), ("settings.accounts", "Edit payment accounts")]),
]

ALL_PERMISSIONS = [key for _, perms in PERMISSION_GROUPS for key, _ in perms]

NORMAL_USER = "Normal user"
NORMAL_USER_PERMS = (["rooms.view", "rooms.open", "rooms.items", "rooms.discount", "rooms.checkout", "rooms.cancel",
                      "hall.view", "hall.open", "hall.items", "hall.checkout", "hall.cancel",
                      "customers.view",
                      "money.view_in", "money.view_expenses", "money.add_expense", "money.cash"])

# Which permissions make each page appear in the sidebar.
PAGE_ACCESS = {
    "Rooms": ("rooms.view",),
    "Hall": ("hall.view",),
    "Customers": ("customers.view", "customers.stats"),
    "Menu": ("menu.view",),
    "Inventory": ("inventory.view",),
    "Money": ("money.view_in", "money.view_expenses", "money.add_expense", "money.cash"),
    "Settings": ("settings.view",),
}
