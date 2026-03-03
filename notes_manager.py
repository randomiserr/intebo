import json
from pathlib import Path
from datetime import datetime
import uuid
from typing import Dict, Any

class NotesManager:
    def __init__(self, data_dir: Path):
        self.notes_file = data_dir / "notes.json"
        self._ensure_file()
        self.cache = self._load_notes()

    def _ensure_file(self):
        if not self.notes_file.exists():
            self.notes_file.parent.mkdir(parents=True, exist_ok=True)
            self._save_disk({})

    def _load_notes(self) -> Dict[str, Any]:
        try:
            with self.notes_file.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _save_disk(self, state: Dict[str, Any]):
        with self.notes_file.open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def _check_file_integrity(self):
        if not self.notes_file.exists() and self.cache:
            self.cache = {}

    def get_notes(self, sa_no: str):
        self._check_file_integrity()
        return self.cache.get(str(sa_no), [])

    def add_note(self, sa_no: str, user: str, text: str):
        self._check_file_integrity()
        notes = self.cache.get(str(sa_no), [])
        note_id = str(uuid.uuid4())
        new_note = {
            "id": note_id,
            "user": user,
            "text": text,
            "created_at": datetime.now().strftime("%d.%m.%Y %H:%M")
        }
        notes.append(new_note)
        self.cache[str(sa_no)] = notes
        self._save_disk(self.cache)
        return new_note

    def update_note(self, sa_no: str, note_id: str, text: str):
        self._check_file_integrity()
        notes = self.cache.get(str(sa_no), [])
        for note in notes:
            if note["id"] == note_id:
                note["text"] = text
                note["updated_at"] = datetime.now().strftime("%d.%m.%Y %H:%M")
                self._save_disk(self.cache)
                return note
        return None

    def delete_note(self, sa_no: str, note_id: str):
        self._check_file_integrity()
        notes = self.cache.get(str(sa_no), [])
        original_len = len(notes)
        notes = [n for n in notes if n["id"] != note_id]
        if len(notes) < original_len:
            self.cache[str(sa_no)] = notes
            self._save_disk(self.cache)
            return True
        return False
