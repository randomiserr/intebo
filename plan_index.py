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
ARCHIVE_INDEX_FILENAME = "_archive_index.json"
INDEX_SCHEMA_VERSION = 1


class DataDirUnavailable(Exception):
    """Datovou slozku se nepodarilo precist (nedostupny share, chybejici VPN)."""


class PlanIndex:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.plans_dir = self.data_dir / "plans"
        self.archive_dir = self.data_dir / "archive" / "plans"
        self.index_file = self.data_dir / INDEX_FILENAME
        self.archive_index_file = self.data_dir / ARCHIVE_INDEX_FILENAME
        self._plans: Dict[str, dict] = {}
        self._archived: Optional[Dict[str, dict]] = None
        self._inventory: Optional[dict] = None
        self._dismissed: List[str] = []
        self._loaded = False
        self._lock = threading.RLock()
        # Serializes index mutations with their corresponding shared-file write.
        # Readers only take _lock, so a slow SMB write does not block page loads.
        self._write_lock = threading.Lock()

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
        with self._write_lock:
            # A manual filesystem repair may also have changed archive/plans.
            # Force the next explicit archive view to rescan it lazily.
            self._atomic_write_json(self.archive_index_file, {"schema": 0})
            with self._lock:
                self._archived = None
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

    def archived_plans(self) -> List[dict]:
        """Load archived metadata only when the archive view is requested."""
        self.ensure_loaded()
        with self._write_lock:
            with self._lock:
                if self._archived is not None:
                    return list(self._archived.values())

            payload = self._read_json_or(self.archive_index_file, None)
            if payload and payload.get("schema") == INDEX_SCHEMA_VERSION:
                archived = payload.get("plans", {})
            else:
                archived = self._scan_plans_dir(self.archive_dir)
                self._persist_archive(archived)

            with self._lock:
                self._archived = archived
                return list(archived.values())

    def get_archived(self, plan_id: str) -> Optional[dict]:
        self.archived_plans()
        with self._lock:
            return (self._archived or {}).get(plan_id)

    def has_archived_plan(self, plan_id: str) -> bool:
        """Check one archive path without loading the full archive index."""
        return (self.archive_dir / plan_id).is_dir()

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
        # The write lock covers both snapshot creation and persistence. Without
        # it, an older request could finish last and overwrite a newer index.
        with self._write_lock:
            with self._lock:
                current = self._plans.get(plan_id)
                if current and str(current.get("latest_ts", "")) > ts:
                    # Upload timestamps are lexicographically chronological.
                    # A slower, older request must not replace a newer version
                    # that has already reached the index.
                    return
                self._plans[plan_id] = {
                    "plan_key": plan_id,
                    "latest_ts": ts,
                    "uploaded_at": payload.get("uploaded_at", "Unknown"),
                    "data": payload,
                }
                snapshot = dict(self._plans)  # konzistentni kopie pod zamkem
            # Shared-file I/O stays outside _lock so page reads remain fast.
            self._persist(snapshot)

    def archive_plans(self, plan_ids: List[str]) -> List[str]:
        """Move plans out of the active tree and remove them from the index.

        The directories are renamed on the same shared volume, so their PDFs
        and JSON history stay intact without being read or copied over VPN.
        The index is written once for the whole batch. If that write fails,
        directory moves and the in-memory index are rolled back.
        """
        self.ensure_loaded()
        requested = list(dict.fromkeys(plan_ids))
        if not requested:
            return []

        with self._write_lock:
            with self._lock:
                entries = {
                    plan_id: self._plans[plan_id]
                    for plan_id in requested
                    if plan_id in self._plans
                }

            if not entries:
                return []

            # The archive index is a lazy secondary cache. Mark it invalid
            # before changing directories so normal startup never has to read
            # it, while the next explicit archive view rebuilds complete data.
            self._atomic_write_json(self.archive_index_file, {"schema": 0})
            with self._lock:
                self._archived = None

            self.archive_dir.mkdir(parents=True, exist_ok=True)
            archived: List[str] = []
            moved: List[str] = []
            displaced: List[tuple[Path, Path]] = []

            try:
                for plan_id in entries:
                    source = self.plans_dir / plan_id
                    destination = self.archive_dir / plan_id

                    if source.exists():
                        # A confirmed re-upload may reuse an archived plan ID.
                        # Preserve the previous archive under a unique sibling
                        # before promoting the newly completed version to the
                        # canonical archive path.
                        if destination.exists():
                            backup = self._unique_archive_path(plan_id)
                            os.replace(destination, backup)
                            displaced.append((destination, backup))
                        os.replace(source, destination)
                        moved.append(plan_id)
                        archived.append(plan_id)
                    elif destination.exists():
                        # Recovery from an interrupted previous archive where
                        # the directory move succeeded but index persistence did not.
                        archived.append(plan_id)

                if not archived:
                    return []

                with self._lock:
                    for plan_id in archived:
                        self._plans.pop(plan_id, None)
                    snapshot = dict(self._plans)

                try:
                    self._persist(snapshot)
                except Exception:
                    for plan_id in reversed(moved):
                        os.replace(
                            self.archive_dir / plan_id,
                            self.plans_dir / plan_id,
                        )
                    for destination, backup in reversed(displaced):
                        if backup.exists() and not destination.exists():
                            os.replace(backup, destination)
                    with self._lock:
                        self._plans.update(entries)
                    raise
            except Exception:
                # Fail-safe for a directory move error before index mutation.
                for plan_id in reversed(moved):
                    destination = self.archive_dir / plan_id
                    source = self.plans_dir / plan_id
                    if destination.exists() and not source.exists():
                        os.replace(destination, source)
                for destination, backup in reversed(displaced):
                    if backup.exists() and not destination.exists():
                        os.replace(backup, destination)
                raise

            return archived

    def restore_archived_plan(self, plan_id: str) -> bool:
        """Restore an archived plan before a confirmed same-ID overwrite."""
        source = self.archive_dir / plan_id
        # Normal active-plan overwrites stop after one path check. In
        # particular, they must not initialize/scan the complete lazy archive.
        if not source.is_dir():
            return False

        with self._write_lock:
            destination = self.plans_dir / plan_id
            if not source.exists():
                return False
            if destination.exists():
                raise FileExistsError(f"Active plan directory already exists: {plan_id}")

            # Read only this requested archive entry. The full archive index
            # can remain unloaded until the user explicitly opens its view.
            with self._lock:
                entry = (self._archived or {}).get(plan_id)
            if entry is None:
                latest = self._latest_json(source / "extracted")
                if latest is None:
                    return False
                try:
                    data = self._read_json(latest)
                except (json.JSONDecodeError, OSError):
                    return False
                entry = {
                    "plan_key": plan_id,
                    "latest_ts": latest.stem,
                    "uploaded_at": data.get("uploaded_at", "Unknown"),
                    "data": data,
                }

            # The archive directory changes, so make its lazy cache rebuild on
            # demand. Do this before the move; failure leaves data untouched.
            self._atomic_write_json(self.archive_index_file, {"schema": 0})
            with self._lock:
                self._archived = None

            self.plans_dir.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
            try:
                with self._lock:
                    previous_active = self._plans.get(plan_id)
                    self._plans[plan_id] = entry
                    snapshot = dict(self._plans)
                self._persist(snapshot)
            except Exception:
                os.replace(destination, source)
                with self._lock:
                    if previous_active is None:
                        self._plans.pop(plan_id, None)
                    else:
                        self._plans[plan_id] = previous_active
                raise
            return True

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
        return self._scan_plans_dir(self.plans_dir)

    def _unique_archive_path(self, plan_id: str) -> Path:
        """Return a non-existing sibling path for a displaced archive."""
        suffix = 1
        while True:
            candidate = self.archive_dir / f"{plan_id}__previous_{suffix}"
            if not candidate.exists():
                return candidate
            suffix += 1

    def _scan_plans_dir(self, plans_dir: Path) -> Dict[str, dict]:
        plans: Dict[str, dict] = {}
        if not plans_dir.exists():
            return plans
        # os.scandir vraci mtime/typ primo z vypisu adresare (bez extra
        # sitoveho dotazu na kazdy soubor, na rozdil od pathlib.glob+stat).
        with os.scandir(plans_dir) as it:
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

    def _persist_archive(self, plans: Dict[str, dict]) -> None:
        payload = {"schema": INDEX_SCHEMA_VERSION, "plans": plans}
        self._atomic_write_json(self.archive_index_file, payload)

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
