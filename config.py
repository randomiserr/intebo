from pathlib import Path
import os

# --- BASE SETTINGS ---
# IT can change this path if they want to store data on a specific disk/volume
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))

# --- WEB SETTINGS ---
PORT = 8004
HOST = "0.0.0.0"  # Allows access from other computers on the network

# --- EMAIL AUTOMATION SETTINGS ---
# (To be filled by IT for the background worker)
EMAIL_CONFIG = {
    "imap_server": "imap.company.com",
    "email_user": "lieferplan-parser@company.com",
    "email_pass": "your-password-here",
    "subject_filter": "Lieferplan Import",
    "check_interval_seconds": 300  # Check every 5 minutes
}
