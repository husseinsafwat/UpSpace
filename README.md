# UpSpace · Bookings & Billing

A small Windows desktop app for the UpSpace accountant: room bookings, hall chairs, live timers,
drinks/add-ons, checkout bills, expenses and the daily money total. Python + CustomTkinter + SQLite.

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
| Hall time | minimum 1 hour; after that, under 30 min extra is dropped, 30 min or more = full hour. Per chair: 15 + 15 + 10 + 10 for hours 1–4 (max 50). × number of chairs. No discount. |
| Hall chairs | 16 in total. A booking can't take more than the free chairs. A group starts together on one card; chairs can't be added later, only drinks. |
| Total | time cost + drinks/add-ons. |
| Money page | for the chosen day (today by default): money in from closed bills, minus expenses = total now. Open cards are shown separately and counted at checkout. |

Room prices are copied onto a card when it starts, so changing a room's price only affects new bookings.

## Settings
`config.py` holds the chair count, hall prices and the login. The password is stored as a SHA-256 hash; to
change it, put the output of
`python -c "import hashlib;print(hashlib.sha256(b'NEW-PASSWORD').hexdigest())"` in `LOGIN_PASSWORD_SHA256`.

## Tests
`python -m unittest discover tests`
