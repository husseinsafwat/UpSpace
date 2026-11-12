"""App-wide settings for the UpSpace booking & billing app."""

# --- Login (single fixed account, no sign-up) --------------------------------
LOGIN_EMAIL = "upspace@gmail.com"
# SHA-256 of the password, so the password itself is not stored in plain text.
LOGIN_PASSWORD_SHA256 = "1e1812310856b6e79b5fe1aaeec2d0c89eb4d291f828c906ac2b683c7ac03c33"

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
