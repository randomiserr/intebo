import json
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

class StateManager:
    def __init__(self, data_dir: Path):
        self.state_file = data_dir / "row_states.json"
        self._ensure_file()
        raw = self._load_state()
        migrated, changed = self._migrate_keys(raw)
        self.cache = migrated
        if changed:
            self._save_disk(migrated)

    def _ensure_file(self):
        if not self.state_file.exists():
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self._save_disk({})

    def _load_state(self) -> Dict[str, Any]:
        try:
            with self.state_file.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _save_disk(self, state: Dict[str, Any]):
        """Internal method to write state to disk."""
        with self.state_file.open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    @staticmethod
    def _normalize_mat(val: str) -> str:
        """Normalise material/SA numbers before using them as state dict keys.

        Makes keys immune to minor extraction differences across tool versions, e.g.:
          "A 628 755 24 11 /"         ->  "A6287552411"
          "A123456789 /Z-Format/..."  ->  "A123456789"
          "A.628.755.24.11"           ->  "A6287552411"
          "None"                      ->  "None"  (unchanged)
        """
        v = str(val).strip()
        # Remove everything from the first " /" onwards (PDF noise like "/Z-Format/Ä-Index")
        v = re.sub(r'\s*/.*$', '', v).strip()
        # Strip dots (separator in Buses-Nr format) and spaces (separator in Material No format)
        # so both "A 628 755 24 11" and "A.628.755.24.11" collapse to "A6287552411"
        v = re.sub(r'[\s.]+', '', v)
        return v

    def _migrate_keys(self, state: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
        """Normalize all legacy SA/material keys in an existing state dict.

        Runs once at startup after an upgrade. Any entries stored under raw
        keys (e.g. "A123 /Z-Format") are re-keyed to their normalized form.
        If two raw keys collapse to the same normalized key, their row entries
        are merged and processed=True always wins over False.

        Returns (migrated_state, was_changed).
        """
        changed = False
        new_state: Dict[str, Any] = {}

        for raw_sa, mat_dict in state.items():
            norm_sa = self._normalize_mat(raw_sa)
            if norm_sa not in new_state:
                new_state[norm_sa] = {}
            if norm_sa != raw_sa:
                changed = True

            if not isinstance(mat_dict, dict):
                continue

            for raw_mat, row_dict in mat_dict.items():
                norm_mat = self._normalize_mat(raw_mat)
                if norm_mat not in new_state[norm_sa]:
                    new_state[norm_sa][norm_mat] = {}
                if norm_mat != raw_mat:
                    changed = True

                if not isinstance(row_dict, dict):
                    continue

                for row_key, row_data in row_dict.items():
                    existing = new_state[norm_sa][norm_mat].get(row_key)
                    if existing is None:
                        new_state[norm_sa][norm_mat][row_key] = row_data
                    else:
                        # Collision: processed=True always wins
                        if row_data.get("processed") and not existing.get("processed"):
                            new_state[norm_sa][norm_mat][row_key] = row_data

        return new_state, changed

    def _get_key(self, date: str, quantity: float) -> str:
        """Create a unique key for a row based on its content."""
        # Using string representation of quantity to avoid float precision issues in keys
        return f"{date}_{float(quantity)}"

    def _check_file_integrity(self):
        """Check if file still exists. If not, clear cache."""
        if not self.state_file.exists() and self.cache:
            self.cache = {}

    def get_processed_versions(self, sa_no: str, material: str, date: str) -> list[float]:
        """Return a list of quantities that are marked as processed for this date."""
        self._check_file_integrity()
        # Use cache instead of loading from disk
        state = self.cache
        sa_key = self._normalize_mat(sa_no)
        mat_key = self._normalize_mat(material)
        sa_data = state.get(sa_key, {})
        mat_data = sa_data.get(mat_key, {})
        
        processed_qtys = []
        prefix = f"{date}_"
        
        for key, value in mat_data.items():
            if key.startswith(prefix) and value.get("processed"):
                try:
                    # key format is "YYYY-MM-DD_100.0"
                    # Extract qty part
                    qty_str = key[len(prefix):]
                    processed_qtys.append(float(qty_str))
                except ValueError:
                    continue
                    
        return sorted(processed_qtys)

    def get_state(self, sa_no: str, material: str, date: str, quantity: float) -> bool:
        self._check_file_integrity()
        # Use cache instead of loading from disk
        state = self.cache
        sa_key = self._normalize_mat(sa_no)
        mat_key = self._normalize_mat(material)
        sa_data = state.get(sa_key, {})
        mat_data = sa_data.get(mat_key, {})
        row_key = self._get_key(date, quantity)
        
        row_data = mat_data.get(row_key, {})
        return row_data.get("processed", False)

    def set_state(self, sa_no: str, material: str, date: str, quantity: float, is_processed: bool):
        self._check_file_integrity()
        # Update cache first
        state = self.cache
        sa_key = self._normalize_mat(sa_no)
        mat_key = self._normalize_mat(material)
        # Ensure hierarchy exists
        if sa_key not in state:
            state[sa_key] = {}
        if mat_key not in state[sa_key]:
            state[sa_key][mat_key] = {}
            
        row_key = self._get_key(date, quantity)
        
        # Update state
        state[sa_key][mat_key][row_key] = {
            "processed": is_processed,
            "updated_at": datetime.now().isoformat()
        }
        
        # Persist to disk
        self._save_disk(state)

