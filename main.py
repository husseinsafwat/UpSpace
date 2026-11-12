"""UpSpace booking & billing desktop app (CustomTkinter + SQLite).

Run:  python main.py
"""
from __future__ import annotations

import hashlib
import os
import sys
import tkinter as tk
import calendar
import tkinter.font as tkfont
from tkinter import filedialog
from datetime import date, datetime, timedelta

import customtkinter as ctk

import pricing
from config import (CHARCOAL, CURRENCY, DANGER, DB_FILENAME, IVORY, LOGIN_EMAIL, LOGIN_PASSWORD_SHA256, SOFT,
                    STONE, WHITE)
from db import SIZES, BookingError, Database, item_label
from pricing import fmt_duration, fmt_money

try:
    from PIL import Image
except ImportError:  # logo is optional
    Image = None


# ----------------------------------------------------------------------------- paths
def resource_dir() -> str:
    """Folder with assets (works for `python main.py` and a PyInstaller .exe)."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def data_dir() -> str:
    """Folder where the database lives.

    Installed app (.exe): %LOCALAPPDATA%\\UpSpace\\data  (survives updates and uninstall).
    Running from source: next to main.py.
    """
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "UpSpace", "data")
        os.makedirs(path, exist_ok=True)
        return path
    return os.path.dirname(os.path.abspath(__file__))


ASSETS = os.path.join(resource_dir(), "assets")


# ----------------------------------------------------------------------------- style
HOVER = "#33332F"
FONT = {}


def load_fonts(root: tk.Misc) -> None:
    fonts_dir = os.path.join(ASSETS, "fonts")
    if os.path.isdir(fonts_dir):
        for f in os.listdir(fonts_dir):
            if f.lower().endswith((".otf", ".ttf")):
                try:
                    ctk.FontManager.load_font(os.path.join(fonts_dir, f))
                except Exception:
                    pass
    families = set(tkfont.families(root))
    body = "Inter" if "Inter" in families else "Segoe UI"
    heading = next((f for f in ("Montserrat", "Inter ExtraBold", "Inter") if f in families), body)
    FONT.update(
        body=ctk.CTkFont(body, 14),
        small=ctk.CTkFont(body, 12),
        bold=ctk.CTkFont(body, 14, "bold"),
        label=ctk.CTkFont(body, 12, "bold"),
        h1=ctk.CTkFont(heading, 26, "bold"),
        h2=ctk.CTkFont(heading, 18, "bold"),
        h3=ctk.CTkFont(heading, 15, "bold"),
        timer=ctk.CTkFont(body, 26, "bold"),
        kpi=ctk.CTkFont(heading, 24, "bold"),
    )


def money(value) -> str:
    return f"{fmt_money(value)} {CURRENCY}"


def primary_button(master, text, command, **kw):
    kw.setdefault("height", 38)
    return ctk.CTkButton(master, text=text, command=command, fg_color=CHARCOAL, hover_color=HOVER,
                         text_color=IVORY, corner_radius=8, font=FONT["bold"], **kw)


def ghost_button(master, text, command, danger=False, **kw):
    kw.setdefault("height", 32)
    color = DANGER if danger else CHARCOAL
    return ctk.CTkButton(master, text=text, command=command, fg_color="transparent", hover_color=SOFT,
                         text_color=color, border_color=color, border_width=1, corner_radius=8,
                         font=FONT["small"], **kw)


def entry(master, placeholder="", **kw):
    return ctk.CTkEntry(master, placeholder_text=placeholder, height=38, corner_radius=8, border_color=STONE,
                        fg_color=WHITE, text_color=CHARCOAL, font=FONT["body"], **kw)


def field_label(master, text):
    return ctk.CTkLabel(master, text=text.upper(), font=FONT["label"], text_color=STONE, anchor="w")


def panel(master, **kw):
    return ctk.CTkFrame(master, fg_color=WHITE, corner_radius=12, border_width=1, border_color=SOFT, **kw)


def style_segmented(seg) -> None:
    """Selected segment: ivory text on charcoal. Others: charcoal text."""
    current = seg.get()
    for name, btn in seg._buttons_dict.items():
        btn.configure(text_color=IVORY if name == current else CHARCOAL)


def parse_number(text: str):
    text = (text or "").strip().replace(",", "")
    return float(text) if text else None


# ----------------------------------------------------------------------------- dialogs
class Dialog(ctk.CTkToplevel):
    """Small modal window centred on the main window."""

    def __init__(self, master, title: str, width=420, height=300):
        super().__init__(master)
        self.title(title)
        self.configure(fg_color=IVORY)
        self.resizable(False, False)
        self.transient(master.winfo_toplevel())
        top = master.winfo_toplevel()
        x = top.winfo_rootx() + (top.winfo_width() - width) // 2
        y = top.winfo_rooty() + (top.winfo_height() - height) // 3
        self.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=24, pady=20)
        self.error = ctk.CTkLabel(self, text="", text_color=DANGER, font=FONT["small"], wraplength=width - 48)
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(120, self._grab)

    def _grab(self):
        try:
            self.lift()
            self.focus_force()
            self.grab_set()
        except tk.TclError:
            self.after(120, self._grab)

    def show_error(self, msg: str):
        self.error.configure(text=msg)
        self.error.pack(before=self.body, side="bottom", pady=(0, 8))

    def buttons(self, ok_text, ok_command, cancel_text="Cancel", danger=False):
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(side="bottom", fill="x", pady=(14, 0))
        ok = primary_button(row, ok_text, ok_command, width=150)
        if danger:
            ok.configure(fg_color=DANGER, hover_color="#7a2222")
        ok.pack(side="right")
        ghost_button(row, cancel_text, self.destroy, width=100, height=38).pack(side="right", padx=(0, 10))
        self.bind("<Return>", lambda e: ok_command())
        return ok


def price_text(prices: dict) -> str:
    """'15' or 'S 30 · M 40'."""
    if None in prices:
        return fmt_money(prices[None])
    return "  ·  ".join(f"{size} {fmt_money(p)}" for size, p in prices.items())


class MenuPickerDialog(Dialog):
    """Add drinks / food / add-ons to a card from the menu.

    + puts one in the cart, − takes one out. "Review" shows the cart in a small window,
    and Confirm there adds everything to the card.
    """

    def __init__(self, master, db: Database, card, on_done):
        super().__init__(master, "Add from menu", 980, 660)
        self.db, self.card, self.on_done = db, card, on_done
        self.cart: dict[tuple[int, str | None], int] = {}
        self.menu = {r["id"]: r for r in db.list_menu()}
        self.counts: dict[tuple[int, str | None], ctk.CTkLabel] = {}

        top = ctk.CTkFrame(self.body, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text="Add from menu", font=FONT["h2"], text_color=CHARCOAL).pack(side="left")
        place = card["room_name"] if card["kind"] == "room" else f"Hall · {card['chairs']} chair(s)"
        ctk.CTkLabel(top, text=f"   {card['customer_name']}  ·  {place}", font=FONT["body"],
                     text_color=STONE).pack(side="left")
        self.search = entry(top, "Search…", width=220)
        self.search.pack(side="right")
        self.search.bind("<KeyRelease>", lambda e: self.build_list())

        cats = ["All"] + db.menu_categories()
        self.cat = ctk.CTkSegmentedButton(self.body, values=cats, command=lambda v: self.on_cat(),
                                          font=FONT["small"], height=32, fg_color=SOFT, selected_color=CHARCOAL,
                                          selected_hover_color=HOVER, unselected_color=SOFT,
                                          unselected_hover_color="#d8d3c7", text_color=CHARCOAL)
        self.cat.set("All")
        self.cat.pack(fill="x", pady=(10, 8))
        style_segmented(self.cat)

        main = ctk.CTkFrame(self.body, fg_color="transparent")
        main.pack(fill="both", expand=True)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=1)
        self.list = ctk.CTkScrollableFrame(main, fg_color=WHITE, corner_radius=12, border_width=1,
                                           border_color=SOFT)
        self.list.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.list.grid_columnconfigure(0, weight=1)

        side = panel(main, width=300)
        side.grid(row=0, column=1, sticky="ns")
        side.grid_propagate(False)
        side.grid_rowconfigure(1, weight=1)
        side.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(side, text="Cart", font=FONT["h3"], text_color=CHARCOAL).grid(row=0, column=0, sticky="w",
                                                                              padx=16, pady=(14, 4))
        self.cart_box = ctk.CTkScrollableFrame(side, fg_color="transparent")
        self.cart_box.grid(row=1, column=0, sticky="nsew", padx=6)
        self.cart_box.grid_columnconfigure(0, weight=1)
        self.cart_total = ctk.CTkLabel(side, text="", font=FONT["h2"], text_color=CHARCOAL)
        self.cart_total.grid(row=2, column=0, sticky="w", padx=16, pady=(6, 4))
        self.review_btn = primary_button(side, "Review & confirm", self.review)
        self.review_btn.grid(row=3, column=0, sticky="ew", padx=12)
        ghost_button(side, "Close", self.destroy, height=34).grid(row=4, column=0, sticky="ew", padx=12,
                                                                  pady=(6, 12))
        self.build_list()
        self.update_cart()

    def on_cat(self):
        style_segmented(self.cat)
        self.build_list()

    def build_list(self):
        for w in self.list.winfo_children():
            w.destroy()
        self.counts = {}
        cat = self.cat.get()
        q = self.search.get().strip().lower()
        rows = [m for m in self.menu.values()
                if (cat == "All" or m["category"] == cat) and (not q or q in m["name"].lower())]
        if not rows:
            ctk.CTkLabel(self.list, text="No items here. Add items in the Menu tab.", font=FONT["body"],
                         text_color=STONE).grid(row=0, column=0, sticky="w", padx=12, pady=12)
            return
        r, last_cat = 0, None
        for m in rows:
            if m["category"] != last_cat:
                last_cat = m["category"]
                field_label(self.list, last_cat).grid(row=r, column=0, columnspan=2, sticky="w", padx=12,
                                                      pady=(12 if r else 6, 2))
                r += 1
            ctk.CTkLabel(self.list, text=m["name"], font=FONT["bold"], text_color=CHARCOAL, anchor="w").grid(
                row=r, column=0, sticky="w", padx=12, pady=4)
            opts = ctk.CTkFrame(self.list, fg_color="transparent")
            opts.grid(row=r, column=1, sticky="e", padx=8)
            for size, price in Database.item_prices(m).items():
                self._counter(opts, m["id"], size, price).pack(side="left", padx=(10, 0))
            r += 1

    def _counter(self, master, item_id, size, price):
        box = ctk.CTkFrame(master, fg_color=IVORY, corner_radius=8)
        label = (f"{size}  " if size else "") + fmt_money(price)
        ctk.CTkLabel(box, text=label, font=FONT["small"], text_color=CHARCOAL, width=58).pack(side="left",
                                                                                          padx=(8, 2))
        ghost_button(box, "−", lambda: self.change(item_id, size, -1), width=28, height=28).pack(side="left")
        n = ctk.CTkLabel(box, text="", font=FONT["bold"], text_color=CHARCOAL, width=26)
        n.pack(side="left")
        primary_button(box, "+", lambda: self.change(item_id, size, 1), width=28, height=28).pack(
            side="left", padx=(0, 6), pady=4)
        self.counts[(item_id, size)] = n
        self._show_count(item_id, size)
        return box

    def _show_count(self, item_id, size):
        label = self.counts.get((item_id, size))
        if label is not None:
            qty = self.cart.get((item_id, size), 0)
            label.configure(text=str(qty) if qty else "·")

    def change(self, item_id, size, delta):
        key = (item_id, size)
        qty = max(0, self.cart.get(key, 0) + delta)
        if qty:
            self.cart[key] = qty
        else:
            self.cart.pop(key, None)
        self._show_count(item_id, size)
        self.update_cart()

    def lines(self):
        """[(label, qty, line_total, (item_id, size))] in the order they were added."""
        out = []
        for (item_id, size), qty in self.cart.items():
            m = self.menu[item_id]
            unit = Database.item_prices(m)[size]
            name = m["name"] + (f" ({size})" if size else "")
            out.append((f"{qty} × {name}", qty, unit * qty, (item_id, size)))
        return out

    def update_cart(self):
        for w in self.cart_box.winfo_children():
            w.destroy()
        lines = self.lines()
        if not lines:
            ctk.CTkLabel(self.cart_box, text="Empty. Press + next to an item.", font=FONT["small"],
                         text_color=STONE).grid(row=0, column=0, sticky="w", padx=8)
        for i, (label, _, total, key) in enumerate(lines):
            ctk.CTkLabel(self.cart_box, text=label, font=FONT["small"], text_color=CHARCOAL, anchor="w",
                         wraplength=170, justify="left").grid(row=i, column=0, sticky="w", padx=(8, 4), pady=1)
            ctk.CTkLabel(self.cart_box, text=fmt_money(total), font=FONT["small"], text_color=CHARCOAL).grid(
                row=i, column=1, sticky="e")
            ctk.CTkButton(self.cart_box, text="×", width=22, height=22, fg_color="transparent", hover_color=SOFT,
                          text_color=STONE, font=FONT["bold"],
                          command=lambda k=key: self.change(k[0], k[1], -self.cart.get(k, 0))).grid(
                row=i, column=2, padx=4)
        count = sum(self.cart.values())
        self.cart_total.configure(text=f"{money(sum(l[2] for l in lines))}" + (f"  ·  {count} item(s)" if count
                                                                                  else ""))
        self.review_btn.configure(state="normal" if lines else "disabled")

    def review(self):
        if self.cart:
            CartReviewDialog(self, self.lines(), self.confirm)

    def confirm(self) -> str | None:
        """Called by the review window. Returns an error message, or None when added."""
        try:
            self.db.add_items(self.card["id"], [(i, s, q) for (i, s), q in self.cart.items()])
        except BookingError as e:
            return str(e)
        self.destroy()
        self.on_done()
        return None


class CartReviewDialog(Dialog):
    """Small window: every line in the cart and the total. Confirm adds them to the card."""

    def __init__(self, master, lines, on_confirm):
        super().__init__(master, "Review items", 420, min(560, 250 + 26 * len(lines)))
        self.on_confirm = on_confirm
        ctk.CTkLabel(self.body, text="Add these to the card?", font=FONT["h3"], text_color=CHARCOAL).pack(anchor="w")
        box = panel(self.body)
        box.pack(fill="both", expand=True, pady=(10, 0))
        box.grid_columnconfigure(0, weight=1)
        for i, (label, _, total, _) in enumerate(lines):
            ctk.CTkLabel(box, text=label, font=FONT["body"], text_color=CHARCOAL, anchor="w").grid(
                row=i, column=0, sticky="w", padx=14, pady=(10 if i == 0 else 1, 1))
            ctk.CTkLabel(box, text=money(total), font=FONT["body"], text_color=CHARCOAL).grid(row=i, column=1,
                                                                                              sticky="e", padx=14)
        bar = ctk.CTkFrame(box, fg_color=CHARCOAL, corner_radius=8)
        bar.grid(row=len(lines), column=0, columnspan=2, sticky="ew", padx=10, pady=10)
        ctk.CTkLabel(bar, text=f"TOTAL · {sum(l[1] for l in lines)} item(s)", font=FONT["bold"],
                     text_color=IVORY).pack(side="left", padx=12, pady=8)
        ctk.CTkLabel(bar, text=money(sum(l[2] for l in lines)), font=FONT["h2"], text_color=IVORY).pack(
            side="right", padx=12)
        self.buttons("Confirm", self.confirm, cancel_text="Back")

    def confirm(self):
        error = self.on_confirm()
        if error:
            self.show_error(error)
        else:
            self.destroy()


class DiscountDialog(Dialog):
    """Room discount for this card: EGP off each hour, or % off the hourly price."""

    LABELS = {pricing.DISCOUNT_NONE: "No discount", pricing.DISCOUNT_EGP: f"{CURRENCY} off / hour",
              pricing.DISCOUNT_PCT: "% off"}

    def __init__(self, master, db: Database, card, on_done):
        super().__init__(master, "Discount", 440, 330)
        self.db, self.card, self.on_done = db, card, on_done
        ctk.CTkLabel(self.body, text="Discount on the hourly price", font=FONT["h3"],
                     text_color=CHARCOAL).pack(anchor="w")
        ctk.CTkLabel(self.body, text=f"Room price: {money(card['rate'])} per hour. Drinks are never discounted.",
                     font=FONT["small"], text_color=STONE).pack(anchor="w", pady=(2, 10))
        self.kind = ctk.CTkSegmentedButton(
            self.body, values=list(self.LABELS.values()), command=lambda _: (style_segmented(self.kind), self.update_preview()),
            selected_color=CHARCOAL, selected_hover_color=HOVER, unselected_color=SOFT,
            unselected_hover_color="#d8d3c7", text_color=IVORY, font=FONT["small"], height=34)
        self.kind.set(self.LABELS[card["discount_type"]])
        style_segmented(self.kind)
        self.kind.pack(fill="x")
        field_label(self.body, "Value").pack(anchor="w", pady=(12, 2))
        self.value = entry(self.body, "e.g. 20")
        if card["discount_type"] != pricing.DISCOUNT_NONE:
            self.value.insert(0, fmt_money(card["discount_value"]))
        self.value.pack(fill="x")
        self.value.bind("<KeyRelease>", lambda e: self.update_preview())
        self.preview = ctk.CTkLabel(self.body, text="", font=FONT["bold"], text_color=CHARCOAL)
        self.preview.pack(anchor="w", pady=(10, 0))
        self.buttons("Save", self.save)
        self.update_preview()

    def selected(self) -> str:
        label = self.kind.get()
        return next(k for k, v in self.LABELS.items() if v == label)

    def update_preview(self):
        kind = self.selected()
        self.value.configure(state="disabled" if kind == pricing.DISCOUNT_NONE else "normal")
        try:
            value = parse_number(self.value.get()) or 0
        except ValueError:
            self.preview.configure(text="")
            return
        rate = pricing.discounted_rate(self.card["rate"], kind, value)
        self.preview.configure(text=f"New price: {money(rate)} per hour")

    def save(self):
        kind = self.selected()
        try:
            value = 0 if kind == pricing.DISCOUNT_NONE else parse_number(self.value.get())
            if value is None:
                raise BookingError("Enter the discount value.")
            self.db.set_discount(self.card["id"], kind, value)
        except ValueError:
            self.show_error("Discount must be a number.")
            return
        except BookingError as e:
            self.show_error(str(e))
            return
        self.destroy()
        self.on_done()


class ConfirmDialog(Dialog):
    def __init__(self, master, title, message, ok_text, on_ok, danger=True):
        super().__init__(master, title, 420, 210)
        ctk.CTkLabel(self.body, text=title, font=FONT["h3"], text_color=CHARCOAL).pack(anchor="w")
        ctk.CTkLabel(self.body, text=message, font=FONT["body"], text_color=CHARCOAL, wraplength=370,
                     justify="left").pack(anchor="w", pady=(8, 0))

        def ok():
            self.destroy()
            on_ok()
        self.buttons(ok_text, ok, danger=danger)


class CheckoutDialog(Dialog):
    """Shows the bill (time frozen when opened). Confirm closes the card."""

    def __init__(self, master, db: Database, card, on_done):
        items = db.card_items(card["id"])
        super().__init__(master, "Checkout", 460, min(640, 430 + 26 * len(items)))
        self.db, self.card, self.on_done = db, card, on_done
        self.end = datetime.now()
        bill = db.compute_bill(card, self.end)

        place = card["room_name"] if card["kind"] == "room" else f"Hall · {card['chairs']} chair(s)"
        ctk.CTkLabel(self.body, text=card["customer_name"], font=FONT["h2"], text_color=CHARCOAL).pack(anchor="w")
        ctk.CTkLabel(self.body, text=place, font=FONT["body"], text_color=STONE).pack(anchor="w")

        box = panel(self.body)
        box.pack(fill="x", pady=(12, 0))
        box.grid_columnconfigure(0, weight=1)
        rows = [("Started", card["start_time"][11:16]), ("Ended", self.end.strftime("%H:%M")),
                ("Time spent", fmt_duration(bill.minutes))]
        if card["kind"] == "room":
            rate_txt = money(bill.rate) + " / hour"
            if card["discount_type"] != pricing.DISCOUNT_NONE:
                d = (f"{fmt_money(card['discount_value'])}%" if card["discount_type"] == pricing.DISCOUNT_PCT
                     else money(card["discount_value"]))
                rate_txt = f"{money(bill.effective_rate)} / hour  (was {fmt_money(bill.rate)}, −{d})"
            rows += [("Hourly price", rate_txt),
                     ("Room time", f"{bill.minutes} min ÷ 60 × {fmt_money(bill.effective_rate)} = {money(bill.time_cost)}")]
        else:
            rows += [("Billed hours", f"{int(bill.billed_hours)} h"),
                     ("Hall time", f"{fmt_money(bill.rate)} × {card['chairs']} chair(s) = {money(bill.time_cost)}")]
        for it in items:
            rows.append((f"  {item_label(it)}", money(it["price"])))
        rows.append(("Drinks & add-ons", money(bill.items_total)))
        for i, (k, v) in enumerate(rows):
            ctk.CTkLabel(box, text=k, font=FONT["small"], text_color=STONE, anchor="w").grid(
                row=i, column=0, sticky="w", padx=14, pady=(8 if i == 0 else 1, 1))
            ctk.CTkLabel(box, text=v, font=FONT["small"], text_color=CHARCOAL, anchor="e").grid(
                row=i, column=1, sticky="e", padx=14)
        total = ctk.CTkFrame(box, fg_color=CHARCOAL, corner_radius=8)
        total.grid(row=len(rows), column=0, columnspan=2, sticky="ew", padx=10, pady=10)
        ctk.CTkLabel(total, text="TOTAL", font=FONT["bold"], text_color=IVORY).pack(side="left", padx=12, pady=8)
        ctk.CTkLabel(total, text=money(bill.total), font=FONT["h2"], text_color=IVORY).pack(side="right", padx=12)

        self.buttons("Confirm & close", self.confirm, cancel_text="Back")

    def confirm(self):
        try:
            self.db.close_card(self.card["id"], self.end)
        except BookingError as e:
            self.show_error(str(e))
            return
        self.destroy()
        self.on_done()


class ExpenseDialog(Dialog):
    def __init__(self, master, db: Database, on_done, expense=None):
        super().__init__(master, "Expense", 400, 290)
        self.db, self.on_done, self.expense = db, on_done, expense
        ctk.CTkLabel(self.body, text="Edit expense" if expense else "Add expense", font=FONT["h3"],
                     text_color=CHARCOAL).pack(anchor="w")
        field_label(self.body, "What was it for").pack(anchor="w", pady=(12, 2))
        self.desc = entry(self.body, "e.g. Milk")
        self.desc.pack(fill="x")
        field_label(self.body, f"Amount ({CURRENCY})").pack(anchor="w", pady=(10, 2))
        self.amount = entry(self.body, "e.g. 120")
        self.amount.pack(fill="x")
        if expense:
            self.desc.insert(0, expense["description"])
            self.amount.insert(0, fmt_money(expense["amount"]).replace(",", ""))
        self.buttons("Save", self.save)
        self.after(150, self.desc.focus_set)

    def save(self):
        try:
            amount = parse_number(self.amount.get())
            if self.expense:
                self.db.update_expense(self.expense["id"], self.desc.get(), amount)
            else:
                self.db.add_expense(self.desc.get(), amount)
        except ValueError:
            self.show_error("Amount must be a number.")
            return
        except BookingError as e:
            self.show_error(str(e))
            return
        self.destroy()
        self.on_done()


class RoomDialog(Dialog):
    def __init__(self, master, db: Database, on_done, room=None):
        super().__init__(master, "Room", 400, 290)
        self.db, self.on_done, self.room = db, on_done, room
        ctk.CTkLabel(self.body, text="Edit room" if room else "New room", font=FONT["h3"],
                     text_color=CHARCOAL).pack(anchor="w")
        field_label(self.body, "Room name").pack(anchor="w", pady=(12, 2))
        self.name = entry(self.body, "e.g. Meeting Room A")
        self.name.pack(fill="x")
        field_label(self.body, f"Price per hour ({CURRENCY})").pack(anchor="w", pady=(10, 2))
        self.price = entry(self.body, "e.g. 120")
        self.price.pack(fill="x")
        if room:
            self.name.insert(0, room["name"])
            self.price.insert(0, fmt_money(room["price_per_hour"]).replace(",", ""))
        self.buttons("Save", self.save)
        self.after(150, self.name.focus_set)

    def save(self):
        try:
            price = parse_number(self.price.get())
            if self.room:
                self.db.update_room(self.room["id"], self.name.get(), price)
            else:
                self.db.add_room(self.name.get(), price)
        except ValueError:
            self.show_error("Price must be a number.")
            return
        except BookingError as e:
            self.show_error(str(e))
            return
        self.destroy()
        self.on_done()


class MenuItemDialog(Dialog):
    """New / edit menu item: name, category, and one price or a price per size."""

    def __init__(self, master, db: Database, on_done, item=None, category=None):
        super().__init__(master, "Menu item", 440, 470)
        self.db, self.on_done, self.item = db, on_done, item
        ctk.CTkLabel(self.body, text="Edit item" if item else "New menu item", font=FONT["h3"],
                     text_color=CHARCOAL).pack(anchor="w")
        field_label(self.body, "Name").pack(anchor="w", pady=(12, 2))
        self.name = entry(self.body, "e.g. Latte")
        self.name.pack(fill="x")
        field_label(self.body, "Category (pick or type a new one)").pack(anchor="w", pady=(10, 2))
        cats = db.menu_categories() or ["Hot Drinks"]
        self.cat = ctk.CTkComboBox(self.body, values=cats, height=38, corner_radius=8, border_color=STONE,
                                   fg_color=WHITE, text_color=CHARCOAL, button_color=SOFT,
                                   button_hover_color="#d8d3c7", dropdown_fg_color=WHITE,
                                   dropdown_text_color=CHARCOAL, dropdown_hover_color=SOFT, font=FONT["body"],
                                   dropdown_font=FONT["body"])
        self.cat.pack(fill="x")
        self.cat.set(item["category"] if item else (category or cats[0]))

        self.sized = ctk.BooleanVar(value=bool(item) and None not in Database.item_prices(item))
        ctk.CTkSwitch(self.body, text="Sold in sizes (Small / Medium / Large)", variable=self.sized,
                      command=self.toggle, font=FONT["body"], text_color=CHARCOAL, progress_color=CHARCOAL,
                      button_color=STONE, button_hover_color=CHARCOAL).pack(anchor="w", pady=(14, 6))
        self.single = ctk.CTkFrame(self.body, fg_color="transparent")
        field_label(self.single, f"Price ({CURRENCY})").pack(anchor="w", pady=(0, 2))
        self.price = entry(self.single, "e.g. 40")
        self.price.pack(fill="x")
        self.sizes_box = ctk.CTkFrame(self.body, fg_color="transparent")
        self.size_entries = {}
        for i, (code, label, _) in enumerate(SIZES):
            self.sizes_box.grid_columnconfigure(i, weight=1, uniform="s")
            field_label(self.sizes_box, f"{label} ({CURRENCY})").grid(row=0, column=i, sticky="w",
                                                                    padx=(0 if i == 0 else 6, 0))
            e = entry(self.sizes_box, "—")
            e.grid(row=1, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0))
            self.size_entries[code] = e
        ctk.CTkLabel(self.sizes_box, text="Fill the sizes this item comes in (at least two). Leave the others "
                                          "empty.", font=FONT["small"], text_color=STONE, wraplength=380,
                     justify="left").grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        if item:
            self.name.insert(0, item["name"])
            prices = Database.item_prices(item)
            if None in prices:
                self.price.insert(0, fmt_money(prices[None]).replace(",", ""))
            else:
                for code, p in prices.items():
                    self.size_entries[code].insert(0, fmt_money(p).replace(",", ""))
        self.toggle()
        self.buttons("Save", self.save)
        self.after(150, self.name.focus_set)

    def toggle(self):
        if self.sized.get():
            self.single.pack_forget()
            self.sizes_box.pack(fill="x")
        else:
            self.sizes_box.pack_forget()
            self.single.pack(fill="x")

    def save(self):
        if self.sized.get():
            price, sizes = None, {c: e.get() for c, e in self.size_entries.items()}
        else:
            price, sizes = self.price.get(), None
        try:
            if self.item:
                self.db.update_menu_item(self.item["id"], self.name.get(), self.cat.get(), price, sizes)
            else:
                self.db.add_menu_item(self.name.get(), self.cat.get(), price, sizes)
        except BookingError as e:
            self.show_error(str(e))
            return
        self.destroy()
        self.on_done()


def parse_day(text: str) -> date:
    return datetime.strptime(text.strip(), "%Y-%m-%d").date()


class ReportDialog(Dialog):
    """Download an Excel report for a date range or a whole month."""

    def __init__(self, master, db: Database):
        super().__init__(master, "Download report", 460, 400)
        self.db = db
        self.first = db.first_data_date()
        today = date.today()
        ctk.CTkLabel(self.body, text="Download money report", font=FONT["h3"], text_color=CHARCOAL).pack(anchor="w")
        ctk.CTkLabel(self.body, text="Excel file: one sheet per day, and a Total sheet for the whole period.",
                     font=FONT["small"], text_color=STONE, wraplength=400, justify="left").pack(anchor="w")
        self.mode = ctk.CTkSegmentedButton(self.body, values=["Date range", "Month"], command=lambda v: self.switch(),
                                           font=FONT["small"], height=32, fg_color=SOFT, selected_color=CHARCOAL,
                                           selected_hover_color=HOVER, unselected_color=SOFT,
                                           unselected_hover_color="#d8d3c7", text_color=CHARCOAL)
        self.mode.set("Date range")
        self.mode.pack(fill="x", pady=(12, 10))

        self.range_box = ctk.CTkFrame(self.body, fg_color="transparent")
        self.range_box.grid_columnconfigure((0, 1), weight=1, uniform="r")
        field_label(self.range_box, "From (YYYY-MM-DD)").grid(row=0, column=0, sticky="w")
        field_label(self.range_box, "To (YYYY-MM-DD)").grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.start = entry(self.range_box)
        self.start.grid(row=1, column=0, sticky="ew")
        self.end = entry(self.range_box)
        self.end.grid(row=1, column=1, sticky="ew", padx=(10, 0))
        self.start.insert(0, f"{self.first or today:%Y-%m-%d}")
        self.end.insert(0, f"{today:%Y-%m-%d}")
        hint = (f"Data starts on {self.first:%d %b %Y}. Dates must be between that day and today."
                if self.first else "There's no data yet.")
        ctk.CTkLabel(self.range_box, text=hint, font=FONT["small"], text_color=STONE, wraplength=400,
                     justify="left").grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.month_box = ctk.CTkFrame(self.body, fg_color="transparent")
        self.month_box.grid_columnconfigure((0, 1), weight=1, uniform="m")
        self.months = []                       # [(label, year, month)] from the first data month to now
        if self.first:
            y, m = self.first.year, self.first.month
            while (y, m) <= (today.year, today.month):
                self.months.append((f"{calendar.month_name[m]} {y}", y, m))
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        years = sorted({y for _, y, _ in self.months}, reverse=True) or [today.year]
        field_label(self.month_box, "Month").grid(row=0, column=0, sticky="w")
        field_label(self.month_box, "Year").grid(row=0, column=1, sticky="w", padx=(10, 0))
        opt = dict(height=38, fg_color=WHITE, button_color=SOFT, button_hover_color="#d8d3c7", text_color=CHARCOAL,
                   dropdown_fg_color=WHITE, dropdown_text_color=CHARCOAL, dropdown_hover_color=SOFT,
                   font=FONT["body"], dropdown_font=FONT["body"], corner_radius=8)
        self.month_var = ctk.StringVar(value=calendar.month_name[today.month])
        self.year_var = ctk.StringVar(value=str(years[0]))
        ctk.CTkOptionMenu(self.month_box, variable=self.month_var, values=list(calendar.month_name)[1:],
                          **opt).grid(row=1, column=0, sticky="ew")
        ctk.CTkOptionMenu(self.month_box, variable=self.year_var, values=[str(y) for y in years],
                          **opt).grid(row=1, column=1, sticky="ew", padx=(10, 0))
        ctk.CTkLabel(self.month_box, text="The current month runs up to today.", font=FONT["small"],
                     text_color=STONE).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.switch()
        self.ok = self.buttons("Download", self.download)

    def switch(self):
        style_segmented(self.mode)
        self.error.pack_forget()
        if self.mode.get() == "Month":
            self.range_box.pack_forget()
            self.month_box.pack(fill="x")
        else:
            self.month_box.pack_forget()
            self.range_box.pack(fill="x")

    def period(self) -> tuple[date, date]:
        """The chosen period, or raises ValueError with a message for the user."""
        today = date.today()
        if self.mode.get() == "Month":
            y = int(self.year_var.get())
            m = list(calendar.month_name).index(self.month_var.get())
            if self.first is None or (y, m) < (self.first.year, self.first.month) or (y, m) > (today.year, today.month):
                raise ValueError(f"There's no data for {self.month_var.get()} {y}.")
            start = max(date(y, m, 1), self.first)
            end = min(date(y, m, calendar.monthrange(y, m)[1]), today)
        else:
            try:
                start, end = parse_day(self.start.get()), parse_day(self.end.get())
            except ValueError:
                raise ValueError("Write dates like 2026-11-12 (year-month-day).")
        error = self.db.check_period(start, end, today)
        if error:
            raise ValueError(error)
        return start, end

    def download(self):
        try:
            start, end = self.period()
        except ValueError as e:
            self.show_error(str(e))
            return
        import report   # needs openpyxl; imported here so the rest of the app works without it
        self.grab_release()
        path = filedialog.asksaveasfilename(parent=self, title="Save report", defaultextension=".xlsx",
                                            initialfile=report.default_filename(start, end),
                                            filetypes=[("Excel workbook", "*.xlsx")])
        self._grab()
        if not path:
            return
        try:
            report.build_report(self.db, start, end, path)
        except PermissionError:
            self.show_error("Couldn't save. Is that file open in Excel? Close it and try again.")
            return
        except (OSError, ValueError) as e:
            self.show_error(str(e))
            return
        master = self.master
        self.destroy()
        ConfirmDialog(master, "Report saved", f"Saved {os.path.basename(path)}\n"
                                              f"({start:%d %b %Y} to {end:%d %b %Y}).", "OK", lambda: None,
                      danger=False)


# ----------------------------------------------------------------------------- card widget
class CardWidget(ctk.CTkFrame):
    """One open booking: live timer, running cost, items and actions."""

    def __init__(self, master, app: "MainView", card):
        super().__init__(master, fg_color=WHITE, corner_radius=12, border_width=1, border_color=SOFT)
        self.app, self.db, self.card = app, app.db, card
        self.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(self, fg_color=CHARCOAL, corner_radius=10)
        head.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 0))
        place = card["room_name"] if card["kind"] == "room" else f"HALL · {card['chairs']} CHAIR(S)"
        ctk.CTkLabel(head, text=place.upper(), font=FONT["label"], text_color=STONE).pack(anchor="w", padx=12,
                                                                                        pady=(8, 0))
        ctk.CTkLabel(head, text=card["customer_name"], font=FONT["h3"], text_color=IVORY).pack(anchor="w", padx=12)
        sub = f"Started {card['start_time'][11:16]}"
        if card["phone"]:
            sub += f"  ·  {card['phone']}"
        ctk.CTkLabel(head, text=sub, font=FONT["small"], text_color=STONE).pack(anchor="w", padx=12, pady=(0, 8))

        self.timer = ctk.CTkLabel(self, text="00:00:00", font=FONT["timer"], text_color=CHARCOAL)
        self.timer.grid(row=1, column=0, sticky="w", padx=16, pady=(8, 0))
        self.rate = ctk.CTkLabel(self, text="", font=FONT["small"], text_color=STONE, anchor="w", justify="left")
        self.rate.grid(row=2, column=0, sticky="w", padx=16)

        items = self.db.card_items(card["id"])
        if items:
            box = ctk.CTkFrame(self, fg_color=IVORY, corner_radius=8)
            box.grid(row=3, column=0, sticky="ew", padx=12, pady=(8, 0))
            box.grid_columnconfigure(0, weight=1)
            for i, it in enumerate(items):
                ctk.CTkLabel(box, text=item_label(it), font=FONT["small"], text_color=CHARCOAL, anchor="w").grid(
                    row=i, column=0, sticky="w", padx=(10, 4))
                ctk.CTkLabel(box, text=fmt_money(it["price"]), font=FONT["small"], text_color=CHARCOAL).grid(
                    row=i, column=1, sticky="e")
                ctk.CTkButton(box, text="×", width=22, height=22, fg_color="transparent", hover_color=SOFT,
                              text_color=STONE, font=FONT["bold"],
                              command=lambda iid=it["id"]: self.remove_item(iid)).grid(row=i, column=2, padx=4)

        self.lines = ctk.CTkLabel(self, text="", font=FONT["small"], text_color=CHARCOAL, anchor="w",
                                  justify="left")
        self.lines.grid(row=4, column=0, sticky="w", padx=16, pady=(8, 0))
        self.total = ctk.CTkLabel(self, text="", font=FONT["h2"], text_color=CHARCOAL)
        self.total.grid(row=5, column=0, sticky="w", padx=16, pady=(0, 6))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=6, column=0, sticky="ew", padx=12, pady=(0, 6))
        small = [("+ Items", lambda: MenuPickerDialog(self, self.db, self.card, app.refresh_all), False)]
        if card["kind"] == "room":
            small.append(("Discount", lambda: DiscountDialog(self, self.db, self.card, app.refresh_all), False))
        small.append(("Cancel", self.cancel, True))
        for i, (text, cmd, danger) in enumerate(small):
            actions.grid_columnconfigure(i, weight=1, uniform="a")
            ghost_button(actions, text, cmd, danger=danger, width=10).grid(
                row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0))
        primary_button(self, "Checkout", lambda: CheckoutDialog(self, self.db, self.card, app.refresh_all),
                       height=36).grid(row=7, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.tick()

    def tick(self):
        now = datetime.now()
        secs = max(0, int((now - datetime.strptime(self.card["start_time"], "%Y-%m-%d %H:%M:%S")).total_seconds()))
        self.timer.configure(text=f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}")
        bill = self.db.compute_bill(self.card, now)
        c = self.card
        if c["kind"] == "room":
            rate = f"{fmt_money(bill.effective_rate)} {CURRENCY}/hour"
            if c["discount_type"] == pricing.DISCOUNT_EGP:
                rate += f"  (−{fmt_money(c['discount_value'])} {CURRENCY} discount)"
            elif c["discount_type"] == pricing.DISCOUNT_PCT:
                rate += f"  (−{fmt_money(c['discount_value'])}% discount)"
            self.rate.configure(text=rate)
        else:
            self.rate.configure(text=f"Billed so far: {int(bill.billed_hours)} h × {c['chairs']} chair(s)")
        self.lines.configure(text=f"Time  {money(bill.time_cost)}\nItems  {money(bill.items_total)}")
        self.total.configure(text=money(bill.total))

    def remove_item(self, item_id):
        self.db.delete_item(item_id)
        self.app.refresh_all()

    def cancel(self):
        ConfirmDialog(self, "Cancel booking?",
                      f"{self.card['customer_name']}'s card will be removed without a bill. "
                      "Use this only for a booking made by mistake.",
                      "Cancel booking", lambda: (self.db.cancel_card(self.card["id"]), self.app.refresh_all()))


# ----------------------------------------------------------------------------- tabs
class BookingTab(ctk.CTkFrame):
    """Shared layout for the Rooms and Hall tabs: form on the left, open cards on the right."""

    kind = "room"
    title = ""

    def __init__(self, master, app: "MainView"):
        super().__init__(master, fg_color="transparent")
        self.app, self.db = app, app.db
        self.cards: list[CardWidget] = []
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.form = panel(self, width=300)
        self.form.grid(row=0, column=0, sticky="nsw", padx=(0, 16))
        self.form.grid_propagate(False)
        inner = ctk.CTkFrame(self.form, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=20, pady=20)
        ctk.CTkLabel(inner, text=self.title, font=FONT["h2"], text_color=CHARCOAL).pack(anchor="w")
        field_label(inner, "Customer name").pack(anchor="w", pady=(16, 2))
        self.name = entry(inner, "Required")
        self.name.pack(fill="x")
        field_label(inner, "Phone (optional)").pack(anchor="w", pady=(10, 2))
        self.phone = entry(inner, "Optional")
        self.phone.pack(fill="x")
        self.build_extra(inner)
        self.error = ctk.CTkLabel(inner, text="", text_color=DANGER, font=FONT["small"], wraplength=250,
                                  justify="left")
        self.error.pack(anchor="w", pady=(8, 0))
        self.start_btn = primary_button(inner, "Start & create card", self.start)
        self.start_btn.pack(fill="x", pady=(4, 0))
        self.info = ctk.CTkLabel(inner, text="", font=FONT["small"], text_color=STONE, wraplength=250,
                                 justify="left")
        self.info.pack(anchor="w", pady=(14, 0))

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)
        self.list_title = ctk.CTkLabel(right, text="", font=FONT["h3"], text_color=CHARCOAL, anchor="w")
        self.list_title.grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.list = ctk.CTkScrollableFrame(right, fg_color="transparent")
        self.list.grid(row=1, column=0, sticky="nsew")
        for c in range(3):
            self.list.grid_columnconfigure(c, weight=1, uniform="cards")

    def build_extra(self, inner):
        pass

    def start(self):
        raise NotImplementedError

    def reset_form(self):
        self.name.delete(0, "end")
        self.phone.delete(0, "end")
        self.error.configure(text="")
        self.app.focus_set()

    def refresh(self):
        for w in self.cards:
            w.destroy()
        self.cards = []
        open_cards = self.db.open_cards(self.kind)
        for i, card in enumerate(open_cards):
            w = CardWidget(self.list, self.app, card)
            w.grid(row=i // 3, column=i % 3, sticky="nsew", padx=6, pady=6)
            self.cards.append(w)
        self.list_title.configure(text=f"Open cards ({len(open_cards)})" if open_cards
                                  else "No open cards yet. Start one from the form.")

    def tick(self):
        for w in self.cards:
            w.tick()


class RoomsTab(BookingTab):
    kind = "room"
    title = "Reserve a room"

    def build_extra(self, inner):
        field_label(inner, "Room").pack(anchor="w", pady=(10, 2))
        self.room_var = ctk.StringVar(value="")
        self.room_menu = ctk.CTkOptionMenu(inner, variable=self.room_var, values=[""], height=38,
                                           fg_color=WHITE, button_color=SOFT, button_hover_color="#d8d3c7",
                                           text_color=CHARCOAL, dropdown_fg_color=WHITE,
                                           dropdown_text_color=CHARCOAL, dropdown_hover_color=SOFT,
                                           font=FONT["body"], dropdown_font=FONT["body"], corner_radius=8)
        self.room_menu.pack(fill="x")
        self.room_map: dict[str, int] = {}

    def refresh(self):
        super().refresh()
        free = self.db.free_rooms()
        all_rooms = self.db.list_rooms()
        self.room_map = {f"{r['name']}  ·  {fmt_money(r['price_per_hour'])} {CURRENCY}/h": r["id"] for r in free}
        labels = list(self.room_map) or ["No free rooms"]
        self.room_menu.configure(values=labels)
        if self.room_var.get() not in self.room_map:
            self.room_var.set(labels[0])
        enabled = bool(self.room_map)
        self.room_menu.configure(state="normal" if enabled else "disabled")
        self.start_btn.configure(state="normal" if enabled else "disabled")
        busy = len(all_rooms) - len(free)
        if not all_rooms:
            self.info.configure(text="No rooms yet. Add rooms in the Settings tab.")
        else:
            self.info.configure(text=f"{len(free)} free · {busy} busy out of {len(all_rooms)} room(s).\n"
                                     "Busy rooms are hidden from the list until checkout.")

    def start(self):
        try:
            room_id = self.room_map.get(self.room_var.get())
            if room_id is None:
                raise BookingError("Please choose a free room.")
            self.db.open_room_card(self.name.get(), self.phone.get(), room_id)
        except BookingError as e:
            self.error.configure(text=str(e))
            return
        self.reset_form()
        self.app.refresh_all()


class HallTab(BookingTab):
    kind = "hall"
    title = "Book hall chairs"

    def build_extra(self, inner):
        field_label(inner, "Number of chairs").pack(anchor="w", pady=(10, 2))
        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x")
        ghost_button(row, "−", lambda: self.step(-1), width=38, height=38).pack(side="left")
        self.chairs = entry(row, justify="center")
        self.chairs.insert(0, "1")
        self.chairs.pack(side="left", fill="x", expand=True, padx=6)
        ghost_button(row, "+", lambda: self.step(1), width=38, height=38).pack(side="left")
        self.free_label = ctk.CTkLabel(inner, text="", font=FONT["bold"], text_color=CHARCOAL)
        self.free_label.pack(anchor="w", pady=(10, 0))

    def step(self, delta):
        try:
            n = int(self.chairs.get())
        except ValueError:
            n = 1
        n = max(1, min(self.db.hall_chairs_total(), n + delta))
        self.chairs.delete(0, "end")
        self.chairs.insert(0, str(n))

    def refresh(self):
        super().refresh()
        free = self.db.free_chairs()
        self.free_label.configure(text=f"Free chairs: {free} / {self.db.hall_chairs_total()}")
        self.start_btn.configure(state="normal" if free > 0 else "disabled")
        tiers = self.db.hall_tiers()
        self.info.configure(text=f"Per chair: {' + '.join(fmt_money(t) for t in tiers)} for hours 1–{len(tiers)} "
                                 f"(max {fmt_money(sum(tiers))}).\n"
                                 "Minimum 1 hour. Under 30 min extra is dropped, 30 min or more counts "
                                 "as a full hour.\nThe whole group starts together; chairs can't be added "
                                 "later.")

    def reset_form(self):
        super().reset_form()
        self.chairs.delete(0, "end")
        self.chairs.insert(0, "1")

    def start(self):
        try:
            self.db.open_hall_card(self.name.get(), self.phone.get(), self.chairs.get())
        except BookingError as e:
            self.error.configure(text=str(e))
            return
        self.reset_form()
        self.app.refresh_all()


class MoneyTab(ctk.CTkFrame):
    def __init__(self, master, app: "MainView"):
        super().__init__(master, fg_color="transparent")
        self.app, self.db = app, app.db
        self.day = date.today()

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x")
        ghost_button(bar, "◀", lambda: self.shift(-1), width=38, height=38).pack(side="left")
        self.day_label = ctk.CTkLabel(bar, text="", font=FONT["h2"], text_color=CHARCOAL, width=260)
        self.day_label.pack(side="left", padx=8)
        ghost_button(bar, "▶", lambda: self.shift(1), width=38, height=38).pack(side="left")
        ghost_button(bar, "Today", self.go_today, width=70, height=38).pack(side="left", padx=(8, 0))
        primary_button(bar, "+ Add expense", lambda: ExpenseDialog(self, self.db, self.refresh),
                       width=150).pack(side="right")
        ghost_button(bar, "Download report", lambda: ReportDialog(self, self.db), width=150,
                     height=38).pack(side="right", padx=(0, 10))

        kpis = ctk.CTkFrame(self, fg_color="transparent")
        kpis.pack(fill="x", pady=(14, 0))
        self.kpi = {}
        for i, (key, label, dark) in enumerate([("income", "Money in", False), ("expenses", "Expenses", False),
                                                ("net", "Total now (in − expenses)", True)]):
            kpis.grid_columnconfigure(i, weight=1, uniform="k")
            box = ctk.CTkFrame(kpis, fg_color=CHARCOAL if dark else WHITE, corner_radius=12, border_width=1,
                               border_color=CHARCOAL if dark else SOFT)
            box.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 8, 0))
            ctk.CTkLabel(box, text=label.upper(), font=FONT["label"], text_color=STONE).pack(anchor="w", padx=16,
                                                                                         pady=(12, 0))
            v = ctk.CTkLabel(box, text="", font=FONT["kpi"], text_color=IVORY if dark else CHARCOAL)
            v.pack(anchor="w", padx=16, pady=(0, 12))
            self.kpi[key] = v
        self.running = ctk.CTkLabel(self, text="", font=FONT["small"], text_color=STONE, anchor="w")
        self.running.pack(fill="x", pady=(8, 0))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, pady=(8, 0))
        body.grid_columnconfigure((0, 1), weight=1, uniform="b")
        body.grid_rowconfigure(0, weight=1)

        left = panel(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ctk.CTkLabel(left, text="Breakdown", font=FONT["h3"], text_color=CHARCOAL).pack(anchor="w", padx=16,
                                                                                   pady=(14, 4))
        self.breakdown = ctk.CTkFrame(left, fg_color="transparent")
        self.breakdown.pack(fill="x", padx=16)
        ctk.CTkLabel(left, text="Expenses", font=FONT["h3"], text_color=CHARCOAL).pack(anchor="w", padx=16,
                                                                                  pady=(16, 4))
        self.exp_list = ctk.CTkScrollableFrame(left, fg_color="transparent", height=160)
        self.exp_list.pack(fill="both", expand=True, padx=8, pady=(0, 10))

        right = panel(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        ctk.CTkLabel(right, text="Closed bills", font=FONT["h3"], text_color=CHARCOAL).pack(anchor="w", padx=16,
                                                                                       pady=(14, 4))
        self.bills = ctk.CTkScrollableFrame(right, fg_color="transparent")
        self.bills.pack(fill="both", expand=True, padx=8, pady=(0, 10))

    def shift(self, days):
        self.day += timedelta(days=days)
        self.refresh()

    def go_today(self):
        self.day = date.today()
        self.refresh()

    @staticmethod
    def _clear(frame):
        for w in frame.winfo_children():
            w.destroy()

    def refresh(self):
        d = self.day
        self.day_label.configure(text=("Today · " if d == date.today() else "") + d.strftime("%a %d %b %Y"))
        s = self.db.summary(d, d)
        self.kpi["income"].configure(text=money(s["income_total"]))
        self.kpi["expenses"].configure(text=money(s["expenses_total"]))
        self.kpi["net"].configure(text=money(s["net"]))
        n, est = self.db.running_estimate()
        self.running.configure(text=(f"Still running: {n} open card(s), about {money(est)} so far. "
                                     "Counted when they check out.") if n else "No open cards right now.")

        self._clear(self.breakdown)
        self.breakdown.grid_columnconfigure(0, weight=1)
        rows = [
            ("Room bookings", f"{s['room_bookings']}"),
            ("Room hours", fmt_duration(s["room_minutes"])),
            ("Room time", money(s["room_time_income"])),
            ("Hall bookings", f"{s['hall_bookings']}  ({s['hall_chairs']} chairs)"),
            ("Hall chair-hours", f"{fmt_money(s['hall_chair_hours'])} h"),
            ("Hall time", money(s["hall_time_income"])),
            ("Drinks & add-ons", money(s["items_income"])),
            ("Money in", money(s["income_total"])),
            ("Expenses", "− " + money(s["expenses_total"])),
            ("Total now", money(s["net"])),
        ]
        for i, (k, v) in enumerate(rows):
            strong = k in ("Money in", "Total now")
            f = FONT["bold"] if strong else FONT["body"]
            ctk.CTkLabel(self.breakdown, text=k, font=f, text_color=CHARCOAL if strong else STONE,
                         anchor="w").grid(row=i, column=0, sticky="w", pady=1)
            ctk.CTkLabel(self.breakdown, text=v, font=f, text_color=CHARCOAL).grid(row=i, column=1, sticky="e")

        self._clear(self.exp_list)
        self.exp_list.grid_columnconfigure(1, weight=1)
        if not s["expenses"]:
            ctk.CTkLabel(self.exp_list, text="No expenses for this day.", font=FONT["small"],
                         text_color=STONE).grid(row=0, column=0, columnspan=4, sticky="w", padx=8)
        for i, e in enumerate(s["expenses"]):
            ctk.CTkLabel(self.exp_list, text=e["created_at"][11:16], font=FONT["small"], text_color=STONE).grid(
                row=i, column=0, padx=(8, 8), sticky="w")
            ctk.CTkLabel(self.exp_list, text=e["description"], font=FONT["body"], text_color=CHARCOAL,
                         anchor="w").grid(row=i, column=1, sticky="w")
            ctk.CTkLabel(self.exp_list, text=money(e["amount"]), font=FONT["bold"], text_color=CHARCOAL).grid(
                row=i, column=2, padx=8)
            acts = ctk.CTkFrame(self.exp_list, fg_color="transparent")
            acts.grid(row=i, column=3, pady=2)
            ghost_button(acts, "Edit", lambda e=e: ExpenseDialog(self, self.db, self.refresh, e), width=44,
                         height=26).pack(side="left")
            ghost_button(acts, "Delete", lambda e=e: self.delete_expense(e), danger=True, width=54,
                         height=26).pack(side="left", padx=(4, 0))

        self._clear(self.bills)
        self.bills.grid_columnconfigure(1, weight=1)
        if not s["cards"]:
            ctk.CTkLabel(self.bills, text="No closed bills for this day.", font=FONT["small"],
                         text_color=STONE).grid(row=0, column=0, columnspan=4, sticky="w", padx=8)
        for i, c in enumerate(reversed(s["cards"])):
            place = c["room_name"] if c["kind"] == "room" else f"Hall × {c['chairs']}"
            ctk.CTkLabel(self.bills, text=c["end_time"][11:16], font=FONT["small"], text_color=STONE).grid(
                row=i, column=0, padx=(8, 8), sticky="w")
            ctk.CTkLabel(self.bills, text=f"{c['customer_name']}  ·  {place}", font=FONT["body"],
                         text_color=CHARCOAL, anchor="w").grid(row=i, column=1, sticky="w")
            ctk.CTkLabel(self.bills, text=fmt_duration(c["minutes"]), font=FONT["small"], text_color=STONE).grid(
                row=i, column=2, padx=8)
            ctk.CTkLabel(self.bills, text=money(c["total"]), font=FONT["bold"], text_color=CHARCOAL).grid(
                row=i, column=3, padx=8, sticky="e")

    def delete_expense(self, e):
        ConfirmDialog(self, "Delete expense?", f"\"{e['description']}\" ({money(e['amount'])}) will be removed.",
                      "Delete", lambda: (self.db.delete_expense(e["id"]), self.refresh()))


class MenuTab(ctk.CTkFrame):
    """The menu used by "+ Items" on every card. Add, edit or remove drinks, food and add-ons."""

    def __init__(self, master, app: "MainView"):
        super().__init__(master, fg_color="transparent")
        self.app, self.db = app, app.db
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x")
        ctk.CTkLabel(bar, text="Menu", font=FONT["h2"], text_color=CHARCOAL).pack(side="left")
        primary_button(bar, "+ New item", lambda: MenuItemDialog(self, self.db, app.refresh_all),
                       width=140).pack(side="right")
        ctk.CTkLabel(self, text="Cards can only add items from this menu. A price change applies to items added "
                                "from now on; items already on a card or bill keep their price.",
                     font=FONT["small"], text_color=STONE, anchor="w").pack(fill="x", pady=(4, 10))
        self.list = ctk.CTkScrollableFrame(self, fg_color=WHITE, corner_radius=12, border_width=1,
                                           border_color=SOFT)
        self.list.pack(fill="both", expand=True)
        self.list.grid_columnconfigure(0, weight=1)
        self._shown = None

    def refresh(self):
        items = self.db.list_menu()
        key = [tuple(r) for r in items]
        if key == self._shown:      # skip rebuilding a long list when nothing changed
            return
        self._shown = key
        for w in self.list.winfo_children():
            w.destroy()
        if not items:
            ctk.CTkLabel(self.list, text="The menu is empty. Click “+ New item” to add one.", font=FONT["body"],
                         text_color=STONE).grid(row=0, column=0, padx=16, pady=16, sticky="w")
            return
        r, last = 0, None
        for it in items:
            if it["category"] != last:
                last = it["category"]
                head = ctk.CTkFrame(self.list, fg_color="transparent")
                head.grid(row=r, column=0, columnspan=4, sticky="ew", padx=8, pady=(14 if r else 6, 2))
                field_label(head, last).pack(side="left")
                ghost_button(head, f"+ Add to {last}",
                             lambda c=last: MenuItemDialog(self, self.db, self.app.refresh_all, category=c),
                             width=10, height=24).pack(side="left", padx=10)
                r += 1
            ctk.CTkLabel(self.list, text=it["name"], font=FONT["bold"], text_color=CHARCOAL, anchor="w").grid(
                row=r, column=0, sticky="w", padx=16, pady=2)
            ctk.CTkLabel(self.list, text=price_text(Database.item_prices(it)) + f"  {CURRENCY}", font=FONT["body"],
                         text_color=CHARCOAL).grid(row=r, column=1, padx=16, sticky="e")
            ghost_button(self.list, "Edit", lambda i=it: MenuItemDialog(self, self.db, self.app.refresh_all, i),
                         width=60, height=28).grid(row=r, column=2, padx=4)
            ghost_button(self.list, "Remove", lambda i=it: self.remove(i), danger=True, width=70,
                         height=28).grid(row=r, column=3, padx=(4, 12))
            r += 1

    def remove(self, item):
        ConfirmDialog(self, "Remove item?", f"\"{item['name']}\" will be taken off the menu. Bills that already "
                                            "have it keep its name and price.", "Remove",
                      lambda: (self.db.remove_menu_item(item["id"]), self.app.refresh_all()))


class SettingsTab(ctk.CTkScrollableFrame):
    """Hall (chairs and price per chair per hour) and rooms (name and price per hour)."""

    def __init__(self, master, app: "MainView"):
        super().__init__(master, fg_color="transparent")
        self.app, self.db = app, app.db

        ctk.CTkLabel(self, text="Hall", font=FONT["h2"], text_color=CHARCOAL, anchor="w").pack(fill="x")
        ctk.CTkLabel(self, text="New values apply to new hall bookings; cards already open keep the prices they "
                                "started with. A chair never pays more than the sum of the hours below.",
                     font=FONT["small"], text_color=STONE, anchor="w").pack(fill="x", pady=(4, 10))
        hall = panel(self)
        hall.pack(fill="x")
        inner = ctk.CTkFrame(hall, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=14)
        field_label(inner, "Chairs in the hall").grid(row=0, column=0, sticky="w")
        self.chairs = entry(inner, "16", width=110, justify="center")
        self.chairs.grid(row=1, column=0, sticky="w", padx=(0, 28))
        self.tiers = []
        for i in range(4):
            field_label(inner, f"Hour {i + 1} ({CURRENCY}/chair)").grid(row=0, column=i + 1, sticky="w",
                                                                       padx=(0, 10))
            e = entry(inner, "", width=120, justify="center")
            e.grid(row=1, column=i + 1, sticky="w", padx=(0, 10))
            self.tiers.append(e)
        primary_button(inner, "Save hall", self.save_hall, width=120).grid(row=1, column=5, padx=(18, 0))
        self.hall_msg = ctk.CTkLabel(inner, text="", font=FONT["small"], text_color=STONE, anchor="w")
        self.hall_msg.grid(row=2, column=0, columnspan=6, sticky="w", pady=(8, 0))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", pady=(26, 0))
        ctk.CTkLabel(bar, text="Rooms", font=FONT["h2"], text_color=CHARCOAL).pack(side="left")
        primary_button(bar, "+ New room", lambda: RoomDialog(self, self.db, app.refresh_all),
                       width=140).pack(side="right")
        ctk.CTkLabel(self, text="Each room needs a name and a price per hour. A new price applies to new bookings; "
                                "cards already open keep the price they started with.",
                     font=FONT["small"], text_color=STONE, anchor="w").pack(fill="x", pady=(4, 10))
        self.list = panel(self)
        self.list.pack(fill="x")
        self.fill_hall()

    def fill_hall(self):
        self.chairs.delete(0, "end")
        self.chairs.insert(0, str(self.db.hall_chairs_total()))
        for e, t in zip(self.tiers, self.db.hall_tiers() + [0] * 4):
            e.delete(0, "end")
            e.insert(0, fmt_money(t).replace(",", ""))
        tiers = self.db.hall_tiers()
        self.hall_msg.configure(text=f"Now: {self.db.hall_chairs_total()} chairs · per chair "
                                     f"{' + '.join(fmt_money(t) for t in tiers)} = max {money(sum(tiers))}.",
                                text_color=STONE)

    def save_hall(self):
        try:
            self.db.set_hall_settings(self.chairs.get(), [parse_number(e.get()) or 0 for e in self.tiers])
        except ValueError:
            self.hall_msg.configure(text="Prices must be numbers.", text_color=DANGER)
            return
        except BookingError as e:
            self.hall_msg.configure(text=str(e), text_color=DANGER)
            return
        self.app.refresh_all()
        self.fill_hall()
        self.hall_msg.configure(text=self.hall_msg.cget("text") + "  Saved.", text_color=CHARCOAL)

    def refresh(self):
        for w in self.list.winfo_children():
            w.destroy()
        rooms = self.db.list_rooms()
        busy = self.db.busy_room_ids()
        self.list.grid_columnconfigure(0, weight=1)
        if not rooms:
            ctk.CTkLabel(self.list, text="No rooms yet. Click “+ New room” to add one.", font=FONT["body"],
                         text_color=STONE).grid(row=0, column=0, padx=16, pady=16, sticky="w")
        for i, r in enumerate(rooms):
            pad = (14 if i == 0 else 4, 14 if i == len(rooms) - 1 else 4)
            ctk.CTkLabel(self.list, text=r["name"], font=FONT["bold"], text_color=CHARCOAL, anchor="w").grid(
                row=i, column=0, sticky="w", padx=16, pady=pad)
            ctk.CTkLabel(self.list, text=f"{money(r['price_per_hour'])} / hour", font=FONT["body"],
                         text_color=CHARCOAL).grid(row=i, column=1, padx=16)
            status = "In use" if r["id"] in busy else "Free"
            ctk.CTkLabel(self.list, text=status, font=FONT["small"],
                         text_color=CHARCOAL if r["id"] in busy else STONE, width=60).grid(row=i, column=2)
            ghost_button(self.list, "Edit", lambda r=r: RoomDialog(self, self.db, self.app.refresh_all, r),
                         width=60).grid(row=i, column=3, padx=4)
            ghost_button(self.list, "Remove", lambda r=r: self.remove(r), danger=True, width=70).grid(
                row=i, column=4, padx=(4, 16))

    def remove(self, room):
        def do():
            try:
                self.db.remove_room(room["id"])
            except BookingError as e:
                ConfirmDialog(self, "Can't remove room", str(e), "OK", lambda: None, danger=False)
                return
            self.app.refresh_all()
        ConfirmDialog(self, "Remove room?", f"\"{room['name']}\" will no longer be bookable. "
                                            "Old bills keep its name.", "Remove", do)


# ----------------------------------------------------------------------------- screens
class LoginView(ctk.CTkFrame):
    def __init__(self, master, on_success):
        super().__init__(master, fg_color=IVORY)
        self.on_success = on_success
        card = panel(self, width=400)
        card.place(relx=0.5, rely=0.5, anchor="center")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(padx=36, pady=32)
        logo = load_image("UPSPACE_Full_Logo_Black_Transparent.png", 150)
        if logo:
            ctk.CTkLabel(inner, image=logo, text="").pack(pady=(0, 6))
        else:
            ctk.CTkLabel(inner, text="UPSPACE", font=FONT["h1"], text_color=CHARCOAL).pack()
        ctk.CTkLabel(inner, text="Bookings & billing", font=FONT["body"], text_color=STONE).pack(pady=(0, 16))
        field_label(inner, "Email").pack(anchor="w", pady=(0, 2))
        self.email = entry(inner, "", width=320)
        self.email.pack()
        field_label(inner, "Password").pack(anchor="w", pady=(10, 2))
        self.password = entry(inner, "", width=320, show="•")
        self.password.pack()
        self.error = ctk.CTkLabel(inner, text="", text_color=DANGER, font=FONT["small"])
        self.error.pack(pady=(6, 0))
        primary_button(inner, "Sign in", self.submit, width=320).pack(pady=(4, 0))
        self.email.bind("<Return>", lambda e: self.password.focus_set())
        self.password.bind("<Return>", lambda e: self.submit())
        self.after(200, self.email.focus_set)

    def submit(self):
        email = self.email.get().strip().lower()
        digest = hashlib.sha256(self.password.get().encode("utf-8")).hexdigest()
        if email == LOGIN_EMAIL.lower() and digest == LOGIN_PASSWORD_SHA256:
            self.on_success()
        else:
            self.error.configure(text="Wrong email or password.")
            self.password.delete(0, "end")


class MainView(ctk.CTkFrame):
    def __init__(self, master, db: Database, on_logout):
        super().__init__(master, fg_color=IVORY)
        self.db = db
        header = ctk.CTkFrame(self, fg_color=CHARCOAL, corner_radius=0, height=60)
        header.pack(fill="x")
        ctk.CTkLabel(header, text="UPSPACE", font=FONT["h2"], text_color=IVORY).pack(side="left", padx=(24, 10),
                                                                                  pady=14)
        ctk.CTkLabel(header, text="WORK • MEET • GROW", font=FONT["label"], text_color=STONE).pack(side="left")
        ctk.CTkButton(header, text="Log out", command=on_logout, fg_color="transparent", hover_color=HOVER,
                      text_color=IVORY, border_color=STONE, border_width=1, width=80, height=30,
                      font=FONT["small"]).pack(side="right", padx=24)
        self.clock = ctk.CTkLabel(header, text="", font=FONT["bold"], text_color=IVORY)
        self.clock.pack(side="right", padx=8)

        self.tabs = ctk.CTkTabview(self, fg_color=IVORY, segmented_button_fg_color=SOFT,
                                   segmented_button_selected_color=CHARCOAL,
                                   segmented_button_selected_hover_color=HOVER,
                                   segmented_button_unselected_color=SOFT,
                                   segmented_button_unselected_hover_color="#d8d3c7", text_color=CHARCOAL,
                                   command=self.on_tab)
        self.tabs.pack(fill="both", expand=True, padx=20, pady=(8, 16))
        self.tabs._segmented_button.configure(font=FONT["bold"], height=36,
                                              text_color=CHARCOAL, )
        names = ["Rooms", "Hall", "Menu", "Money", "Settings"]
        for n in names:
            self.tabs.add(n)
        self.rooms = RoomsTab(self.tabs.tab("Rooms"), self)
        self.hall = HallTab(self.tabs.tab("Hall"), self)
        self.menu = MenuTab(self.tabs.tab("Menu"), self)
        self.money = MoneyTab(self.tabs.tab("Money"), self)
        self.settings = SettingsTab(self.tabs.tab("Settings"), self)
        for t in (self.rooms, self.hall, self.menu, self.money, self.settings):
            t.pack(fill="both", expand=True, pady=(6, 0))
        self._style_tabs()
        self.refresh_all()
        self._money_counter = 0
        self._alive = True
        self.tick()

    def _style_tabs(self):
        # Selected tab text in ivory, others in charcoal.
        style_segmented(self.tabs._segmented_button)

    def on_tab(self):
        self._style_tabs()
        self.refresh_all()

    def refresh_all(self):
        self.rooms.refresh()
        self.hall.refresh()
        self.menu.refresh()
        self.money.refresh()
        self.settings.refresh()

    def tick(self):
        if not self._alive:
            return
        self.clock.configure(text=datetime.now().strftime("%a %d %b  ·  %H:%M:%S"))
        current = self.tabs.get()
        if current == "Rooms":
            self.rooms.tick()
        elif current == "Hall":
            self.hall.tick()
        elif current == "Money":
            self._money_counter += 1
            if self._money_counter % 30 == 0:   # refresh "still running" every 30 s
                self.money.refresh()
        self.after(1000, self.tick)

    def destroy(self):
        self._alive = False
        super().destroy()


def load_image(name: str, width: int):
    path = os.path.join(ASSETS, name)
    if Image is None or not os.path.exists(path):
        return None
    try:
        img = Image.open(path)
        h = int(img.height * width / img.width)
        return ctk.CTkImage(light_image=img, dark_image=img, size=(width, h))
    except Exception:  # Pillow missing ImageTk etc. -> fall back to text logo
        return None


class App(ctk.CTk):
    def __init__(self, db_path: str | None = None):
        super().__init__()
        ctk.set_appearance_mode("light")
        load_fonts(self)
        self.title("UpSpace · Bookings & Billing")
        self.geometry("1320x820")
        self.minsize(1100, 700)
        self.configure(fg_color=IVORY)
        icon = os.path.join(ASSETS, "UPSPACE_Monogram_Black_Transparent.png")
        if os.path.exists(icon):
            try:
                self.iconphoto(True, tk.PhotoImage(file=icon))
            except tk.TclError:
                pass
        self.db = Database(db_path or os.path.join(data_dir(), DB_FILENAME))
        self.view = None
        self.show_login()

    def _swap(self, view):
        if self.view is not None:
            self.view.destroy()
        self.view = view
        view.pack(fill="both", expand=True)

    def show_login(self):
        self._swap(LoginView(self, self.show_main))

    def show_main(self):
        self._swap(MainView(self, self.db, self.show_login))


if __name__ == "__main__":
    App().mainloop()
