"""
Centralni konfigurace aplikace Intebo.

Hodnoty se nacitaji v tomto poradi (pozdejsi prepise drivejsi):
  1. Vychozi hodnoty zde v kodu
  2. Soubor config.ini vedle teto slozky (pokud existuje)
  3. Promenne prostredi (INTEBO_DATA_DIR, INTEBO_HOST, INTEBO_PORT)

IT obvykle staci upravit config.ini -- bez sahani do kodu.
"""

from pathlib import Path
import configparser
import os

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.ini"

# --- Vychozi hodnoty ---
_defaults = {
    "data_dir": str(BASE_DIR / "data"),
    "host": "0.0.0.0",
    "port": "8000",
}

# --- Nacteni z config.ini ---
_parser = configparser.ConfigParser()
if CONFIG_FILE.exists():
    _parser.read(CONFIG_FILE, encoding="utf-8")

def _get(section: str, key: str, default: str) -> str:
    if _parser.has_option(section, key):
        value = _parser.get(section, key).strip()
        if value:
            return value
    return default

_data_dir = _get("paths", "data_dir", _defaults["data_dir"])
_host = _get("server", "host", _defaults["host"])
_port = _get("server", "port", _defaults["port"])

# --- Promenne prostredi maji prednost (kvuli serverovemu nasazeni jako sluzba) ---
_data_dir = os.getenv("INTEBO_DATA_DIR", _data_dir)
_host = os.getenv("INTEBO_HOST", _host)
_port = os.getenv("INTEBO_PORT", _port)

# --- Verejne hodnoty ---
DATA_DIR: Path = Path(_data_dir)
HOST: str = _host
PORT: int = int(_port)
