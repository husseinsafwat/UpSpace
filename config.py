"""App-wide settings for the UpSpace booking & billing app."""

# Accounts live in the database since v2.2 (see auth.py). First start creates the admin:
# admin@gmail.com / admin. The admin adds users in Settings > Users.

# --- Hall (starting values; change them in the app's Settings tab) -------------
HALL_TOTAL_CHAIRS = 16
# Price per chair for hour 1, 2, 3, 4. Hours after the 4th are free (cap = sum).
HALL_HOUR_TIERS = [15, 15, 10, 10]

# --- Currency / database ------------------------------------------------------
CURRENCY = "EGP"
DB_FILENAME = "upspace.db"

# --- Brand --------------------------------------------------------------------
CHARCOAL = "#171715"
IVORY = "#F4F1EA"
STONE = "#92918C"
WHITE = "#FFFFFF"
SOFT = "#E6E2D8"
DANGER = "#9B2C2C"
HEADING_FONT = "Montserrat"
BODY_FONT = "Inter"
