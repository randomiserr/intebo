import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app import get_aggregated_items, state_manager
from datetime import datetime

# 1. Setup - Pick an item from the current dashboard state (or mock one)
# We need to successfully mock the state so that get_aggregated_items finds it.

print("--- Starting Ghost Row Reproduction ---")

# Let's see what items we have first
items = get_aggregated_items()
if not items:
    print("No items found in dashboard. Cannot reproduce.")
    exit()

target = items[0]
print(f"Target Item: SA={target['sa_no']} Mat={target['material_no']} Date={target['delivery_date']} Qty={target['quantity']}")

# 2. Key identifiers
sa = target['sa_no']
mat = target['material_no']
date = target['delivery_date']
current_qty = target['quantity']

# 3. Simulate a "Processed" state for a DIFFERENT quantity (Conflict)
old_qty = current_qty + 100 # Different quantity
print(f"Simulating processed state for Qty={old_qty} (Current is {current_qty})")

# Mark the OLD quantity as processed
state_manager.set_state(sa, mat, date, old_qty, True)

# 4. Re-run aggregation
print("Re-running aggregation...")
new_items = get_aggregated_items()

# 5. Check for ghost row
ghost_found = False
for i in new_items:
    # Check if it matches our target date/sa/mat
    if i['sa_no'] == sa and i['material_no'] == mat and i['delivery_date'] == date:
        if i.get('is_ghost'):
            print(f"FAILED? Found GHOST row! Qty={i['quantity']} (Should be {old_qty})")
            if i['quantity'] == old_qty:
                print("SUCCESS: Ghost row found with correct quantity.")
                ghost_found = True
        elif i.get('has_ghost'):
            print(f"Found REAL row with has_ghost=True. Ghost Qty ref={i.get('ghost_qty')}")

if not ghost_found:
    print("FAILURE: No ghost row found.")
else:
    print("Logic seems correct.")

# Cleanup (optional, but good to un-set state if we want to be clean)
# state_manager.set_state(sa, mat, date, old_qty, False)
