from app import get_aggregated_items
from pathlib import Path
import sys

# Ensure we can import app
sys.path.append(str(Path(__file__).resolve().parent.parent))

print("Testing Aggregation Logic...")
items = get_aggregated_items()

print(f"Found {len(items)} aggregated items.")

if len(items) > 0:
    print("First item sample:")
    print(items[0])
    
    # Check for keys
    required_keys = ["plan_id", "delivery_date", "days_to_delivery", "urgency", "is_processed"]
    for k in required_keys:
        if k not in items[0]:
            print(f"FAILED: Missing key {k}")
            exit(1)
            
    print("SUCCESS: Items structure looks correct.")
else:
    print("WARNING: No items found (maybe no plans uploaded?).")
    
print("\nDone.")
