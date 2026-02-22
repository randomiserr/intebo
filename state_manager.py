import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

class StateManager:
    def __init__(self, data_dir: Path):
        self.state_file = data_dir / "row_states.json"
        self._ensure_file()
        self.cache = self._load_state()

    def _ensure_file(self):
        if not self.state_file.exists():
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
        sa_data = state.get(str(sa_no), {})
        mat_data = sa_data.get(str(material), {})
        
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
        
        sa_data = state.get(str(sa_no), {})
        mat_data = sa_data.get(str(material), {})
        row_key = self._get_key(date, quantity)
        
        row_data = mat_data.get(row_key, {})
        return row_data.get("processed", False)

    def set_state(self, sa_no: str, material: str, date: str, quantity: float, is_processed: bool):
        self._check_file_integrity()
        # Update cache first
        state = self.cache
        
        # Ensure hierarchy exists
        if str(sa_no) not in state:
            state[str(sa_no)] = {}
        if str(material) not in state[str(sa_no)]:
            state[str(sa_no)][str(material)] = {}
            
        row_key = self._get_key(date, quantity)
        
        # Update state
        state[str(sa_no)][str(material)][row_key] = {
            "processed": is_processed,
            "updated_at": datetime.now().isoformat()
        }
        
        # Persist to disk
        self._save_disk(state)

