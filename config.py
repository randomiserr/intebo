from pathlib import Path
import os

# --- BASE SETTINGS ---
# IT can change this path by setting the INTEBO_DATA_DIR environment variable
DATA_DIR = Path(os.getenv("INTEBO_DATA_DIR", "data"))

# --- WEB SETTINGS ---
PORT = 8001
HOST = "0.0.0.0"  # Allows access from other computers on the network
