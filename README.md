# UpSpace · Bookings & Billing

A small Windows desktop app for the UpSpace accountant: room bookings, hall chairs, live timers,
a drinks & food menu, customers with lifetime statistics, an inventory, checkout bills, expenses and the daily money total. Python + Qt (PySide6) + SQLite.

## Run it
1. Install Python 3.10 or newer from python.org (tick **"Add python.exe to PATH"** during setup).
2. Double-click **`run_upspace.bat`**. The first run installs the two libraries it needs, then opens the app.
   (Or from a terminal: `pip install -r requirements.txt` then `python main.py`.)
3. Sign in with the UpSpace account.

Optional: double-click `build_exe.bat` to make `dist\UpSpace\UpSpace.exe`, which runs without Python installed.

## Data
Everything is saved in **`upspace.db`**, created next to `main.py` (or next to the .exe).
Open cards survive closing the app; their timers keep running from the original start time.
Back this file up (copy it somewhere safe) now and then.

## Calculation rules
| | Rule |
|---|---|
| Room time | exact minutes ÷ 60 × hourly price (after discount). No rounding, no minimum. |
| Room discount | per card: EGP off each hour, or % off the hourly price. Never applies to drinks. |
| Hall time | minimum 1 hour; after that, under 30 min extra is dropped, 30 min or more = full hour. Per chair: 15 + 15 + 10 + 10 for hours 1–4 (max 50, set in **Settings**). × number of chairs. No discount. |
| Hall chairs | 16 in total (set in **Settings**). A booking can't take more than the free chairs. A group starts together on one card; chairs can't be added later, only drinks. |
| Drinks, food & add-ons | only from the **Menu** tab. On a card, **+ Items** opens the menu: press + / − per item (and size), then **Review & confirm**. Add-ons are their own lines. |
| Total | time cost + drinks/add-ons, **rounded to the nearest pound** (158.33 → 158, 158.50 → 159). |
| Payment | required at checkout. Tick one account (Cash, or a Vodafone Cash / InstaPay account) and it takes the whole bill; tick two or more and type each amount: they must add up **exactly** to the total, otherwise a popup explains the difference. Expenses follow the same rules. |
| Money page | for the chosen day (today by default): money in from closed bills, minus expenses = total now. Open cards are shown separately and counted at checkout. |

Room prices and hall prices are copied onto a card when it starts, so changing them only affects new bookings.
Menu prices are copied onto the card when items are added, so editing or removing a menu item never changes a bill.

## Menu
Each item has a name, a category and either one price or sizes (Small / Medium / Large, at least two).
The first start fills the menu with the UpSpace drinks list; after that it's fully editable in the Menu tab.

## Customers
Every card needs the customer's **phone**, which is the customer's unique key. A new phone saves the customer
automatically; a known phone fills in the registered name. Typing a different name for a known phone shows a popup
("registered before with the name …"): names and phones are changed only in **Customers**.
The Customers page has two tabs: **Customers** (search, add, edit) and **Statistics** (one customer or all, for all time,
this month, the last 30 days, this year or custom dates): visits, time, rooms vs hall, drinks & add-ons, money spent,
visit history and top customers, with **Export to Excel**. Only closed bills count.

## Inventory
Stock items (water bottles, cans, snacks…) with a unit and a low-stock alert level. A menu item can be linked to a
stock item (1 unit per sale for every size by default); drinks made fresh stay unlinked. Adding items to a card
subtracts stock; removing an item or cancelling the card puts it back. Not enough stock → the item is blocked with
an "Out of stock" popup that can open the Inventory with Restock ready. **Restock** (optionally recorded as an
expense), **Adjust** (take out with a reason, or a stock count) and a full movement history.

## Money report (Excel)
On the Money page, **Download report** saves an .xlsx file for a date range (default: the first day with data
up to today) or for one month. Each day with activity gets its own sheet (summary, items consumed, expenses);
the last sheet, **Total**, has one row per day and the totals for the whole period. Dates before the first day
with data or after today are refused.

## Payment accounts
Settings → Payment accounts: Cash is always there; add every Vodafone Cash number and InstaPay account you use.
The Money page and the Excel report show money in, money out and the balance for each account and method.

## Settings
The **Settings** tab holds the hall (number of chairs, price per chair for hours 1–4) and the rooms
(name + price per hour). `config.py` holds the starting hall values and the login. The password is stored as a SHA-256 hash; to
change it, put the output of
`python -c "import hashlib;print(hashlib.sha256(b'NEW-PASSWORD').hexdigest())"` in `LOGIN_PASSWORD_SHA256`.

## Tests
`python -m unittest discover tests`
