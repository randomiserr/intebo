"""
In-memory + on-disk index of Lieferplans.

Proc to existuje
----------------
Datova slozka (config.ini -> data_dir) obvykle lezi na firemnim sitovem
disku (SMB share). Puvodni kod prochazel cely strom plans/ a otviral JSON
kazdeho planu pri KAZDEM nacteni stranky -- to je desitky az stovky malych
sitovych operaci na jednu stranku. Na LAN je to neviditelne, pres VPN pomale
a pres hotspot+VPN se to zasekne uplne.

Tento index to resi:
  * Studeny start = jedno cteni souboru `_index.json` (misto skenovani stromu).
  * Teple nacteni = zadne diskove I/O (vse v pameti).
  * Zapis se deje jen kdyz aplikace sama ulozi novy plan -> tehdy se index
    aktualizuje v pameti i na disku.

Predpoklady (plati pro tuto aplikaci):
  * Do datove slozky zapisuje jen tato aplikace.
  * Nastroj pouziva vzdy jen jeden clovek naraz.
Za techto podminek je persistovany index autoritativni a nemusi se pri
beznem nacteni proti disku overovat. Pro rucni sesynchronizovani po zasahu
zvenci slouzi rebuild() (vystaveny endpointem /api/rebuild-index).
"""

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Dict, List, Optional

INDEX_FILENAME = "_index.json"
INDEX_SCHEMA_VERSION = 1


class DataDirUnavailable(Exception):
    """Datovou slozku se nepodarilo precist (nedostupny share, chybejici VPN)."""


class PlanIndex:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.plans_dir = self.data_dir / "plans"
        self.index_file = self.data_dir / INDEX_FILENAME
        self._plans: Dict[str, dict] = {}
        self._inventory: Optional[dict] = None
        self._dismissed: List[str] = []
        self._loaded = False
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    # Zivotni cyklus
    # ------------------------------------------------------------------ #
    def ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def load(self) -> None:
        """Naplni pamet. Jedno male cteni pokud `_index.json` existuje,
        jinak jednorazovy plny sken (ktery `_index.json` rovnou zapise).

        Blokujici I/O (cteni sharu, sken, zapis) delame ZAMERNE mimo `_lock`.
        Zaseknute SMB volani tak nedrzi zamek a nezablokuje ostatni pristup
        (napr. zapis planu po obnove VPN)."""
        payload = self._read_json_or(self.index_file, None, raise_on_io=True)
        if payload and payload.get("schema") == INDEX_SCHEMA_VERSION:
            plans = payload.get("plans", {})
            persist_needed = False
        else:
            # chybejici / stary / poskozeny index -> plny sken
            plans = self._scan_plans()
            persist_needed = True

        # male vedlejsi soubory drzime taky v pameti (obnova pri zapisu)
        inventory = self._read_json_or(self.data_dir / "inventory.json", None)
        dismissed = self._read_json_or(
            self.data_dir / "dismissed_notifications.json", []
        ) or []

        with self._lock:
            self._plans = plans
            self._inventory = inventory
            self._dismissed = dismissed
            self._loaded = True

        if persist_needed:
            self._persist(plans)

    def rebuild(self) -> None:
        """Plny sken stromu plans/ -> index v pameti + na disku. Rezerva pro
        pripad, ze se do slozky zapsalo mimo aplikaci."""
        plans = self._scan_plans()
        with self._lock:
            self._plans = plans
            self._loaded = True
        self._persist(plans)

    # ------------------------------------------------------------------ #
    # Cteni (po nacteni bez diskoveho I/O)
    # ------------------------------------------------------------------ #
    def plans(self) -> List[dict]:
        self.ensure_loaded()
        with self._lock:
            return list(self._plans.values())

    def get(self, plan_id: str) -> Optional[dict]:
        self.ensure_loaded()
        with self._lock:
            return self._plans.get(plan_id)

    def inventory(self) -> Optional[dict]:
        self.ensure_loaded()
        return self._inventory

    def dismissed(self) -> List[str]:
        self.ensure_loaded()
        with self._lock:
            return list(self._dismissed)

    # ------------------------------------------------------------------ #
    # Zapis (aktualizace pameti + persistence)
    # ------------------------------------------------------------------ #
    def upsert_plan(self, plan_id: str, payload: dict, ts: str) -> None:
        """Zavolat po ulozeni noveho/prepsaneho planu. `payload` uz mame
        v pameti z extrakce, takze se nic znovu necte z disku."""
        self.ensure_loaded()
        with self._lock:
            self._plans[plan_id] = {
                "plan_key": plan_id,
                "latest_ts": ts,
                "uploaded_at": payload.get("uploaded_at", "Unknown"),
                "data": payload,
            }
            snapshot = dict(self._plans)  # konzistentni kopie pod zamkem
        # Zapis na disk uz mimo zamek (viz load()).
        self._persist(snapshot)

    def set_inventory(self, inv: dict) -> None:
        self.ensure_loaded()
        self._inventory = inv

    def set_dismissed(self, dismissed: List[str]) -> None:
        self.ensure_loaded()
        with self._lock:
            self._dismissed = list(dismissed)

    # ------------------------------------------------------------------ #
    # Interni
    # ------------------------------------------------------------------ #
    def _scan_plans(self) -> Dict[str, dict]:
        plans: Dict[str, dict] = {}
        if not self.plans_dir.exists():
            return plans
        # os.scandir vraci mtime/typ primo z vypisu adresare (bez extra
        # sitoveho dotazu na kazdy soubor, na rozdil od pathlib.glob+stat).
        with os.scandir(self.plans_dir) as it:
            for entry in it:
                if not entry.is_dir():
                    continue
                ext_dir = Path(entry.path) / "extracted"
                latest = self._latest_json(ext_dir)
                if latest is None:
                    continue
                try:
                    data = self._read_json(latest)
                except (json.JSONDecodeError, OSError):
                    continue
                plans[entry.name] = {
                    "plan_key": entry.name,
                    "latest_ts": latest.stem,
                    "uploaded_at": data.get("uploaded_at", "Unknown"),
                    "data": data,
                }
        return plans

    @staticmethod
    def _latest_json(ext_dir: Path) -> Optional[Path]:
        if not ext_dir.exists():
            return None
        # Nazvy jsou casova razitka (YYYYMMDDThhmmss.json) -> lexikograficke
        # poradi = chronologicke. Staci najit maximum bez trideni.
        newest_name = ""
        newest_path: Optional[Path] = None
        with os.scandir(ext_dir) as it:
            for e in it:
                if e.is_file() and e.name.endswith(".json") and e.name > newest_name:
                    newest_name = e.name
                    newest_path = Path(e.path)
        return newest_path

    def _persist(self, plans: Dict[str, dict]) -> None:
        """Zapis indexu na disk. Vola se mimo `_lock` (dostane snapshot plans)."""
        payload = {"schema": INDEX_SCHEMA_VERSION, "plans": plans}
        self._atomic_write_json(self.index_file, payload)

    # ------------------------------------------------------------------ #
    # I/O helpery
    # ------------------------------------------------------------------ #
    @staticmethod
    def _read_json(path: Path) -> dict:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    @classmethod
    def _read_json_or(cls, path: Path, default, raise_on_io: bool = False):
        try:
            return cls._read_json(path)
        except FileNotFoundError:
            return default
        except json.JSONDecodeError:
            return default
        except OSError as e:
            # Nedostupny share: pri nacitani indexu chceme chybu vyhodit
            # (aby se zobrazila hlaska), u vedlejsich souboru staci default.
            if raise_on_io:
                raise DataDirUnavailable(str(e)) from e
            return default

    @staticmethod
    def _atomic_write_json(path: Path, payload) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)  # atomicke prejmenovani na stejnem svazku
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
