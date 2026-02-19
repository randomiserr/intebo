import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from state_manager import StateManager
from pathlib import Path
import shutil
from datetime import datetime

# Setup
DATA_DIR = Path("data")
state_manager = StateManager(DATA_DIR)

# Test Data
sa_no = "TEST_GHOST_SA"
material = "TEST_GHOST_MAT"
date = "2025-12-31" 
qty_v1 = 100.0
qty_v2 = 200.0

print(f"Testing Ghost Row Logic for {sa_no}")

# 1. Process V1 (Qty 100)
print(f"1. Marking {date} / {qty_v1} as processed.")
state_manager.set_state(sa_no, material, date, qty_v1, True)

# 2. Verify get_processed_versions
versions = state_manager.get_processed_versions(sa_no, material, date)
print(f"2. Processed versions for {date}: {versions}")
assert qty_v1 in versions

# 3. Simulate Logic from app.py
# Current plan has ONLY qty_v2 (modified)
current_plan_pairs = {(date, qty_v2)}
final_lines = []

# Logic from app.py simulation
processed_ghost_dates = set()
d_date = date

# Check ghost rows
if d_date not in processed_ghost_dates:
    processed_ghost_dates.add(d_date)
    hist_qtys = state_manager.get_processed_versions(sa_no, material, d_date)
    for hq in hist_qtys:
        if (d_date, hq) not in current_plan_pairs:
            print(f"   -> Found ghost row: {hq}")
            final_lines.append({"qty": hq, "is_ghost": True})

# Add current line
final_lines.append({"qty": qty_v2, "is_ghost": False})

print(f"3. Final Display Lines: {final_lines}")

assert len(final_lines) == 2
assert final_lines[0]["qty"] == qty_v1
assert final_lines[0]["is_ghost"] is True
assert final_lines[1]["qty"] == qty_v2
assert final_lines[1]["is_ghost"] is False

print("\nSUCCESS: Ghost Row Logic verified.")
