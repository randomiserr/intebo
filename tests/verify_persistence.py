import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from state_manager import StateManager
from pathlib import Path
import shutil

# Setup
DATA_DIR = Path("data")
state_manager = StateManager(DATA_DIR)

# Test Data
sa_no = "TEST_SA"
material = "TEST_MAT"
date = "2023-01-01"
quantity = 100.0

print(f"Testing persistence for {sa_no} / {material} / {date} / {quantity}")

# 1. Initial State should be False
initial_state = state_manager.get_state(sa_no, material, date, quantity)
print(f"Initial state: {initial_state}")
assert initial_state is False

# 2. Set State to True
print("Setting state to True...")
state_manager.set_state(sa_no, material, date, quantity, True)

# 3. Verify State is True
new_state = state_manager.get_state(sa_no, material, date, quantity)
print(f"New state: {new_state}")
assert new_state is True

# 4. simulate new release (same data, should return True)
print("Simulating new release (checking same key)...")
persistent_state = state_manager.get_state(sa_no, material, date, quantity)
print(f"Persistent state: {persistent_state}")
assert persistent_state is True

# 5. Check different key (should be False)
other_state = state_manager.get_state(sa_no, material, date, 200.0)
print(f"Other row state: {other_state}")
assert other_state is False

print("\nSUCCESS: Backend persistence logic verified.")
