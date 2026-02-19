from app import get_aggregated_items, DATA_DIR, _load_json
from pathlib import Path
import sys

# Ensure we can import app
sys.path.append(str(Path(__file__).resolve().parent.parent))

print(f"Data Dir: {DATA_DIR}")
plans_dir = DATA_DIR / "plans"
if not plans_dir.exists():
    print("No plans dir.")
    exit()

print("Scanning plans...")
for p_dir in plans_dir.iterdir():
    if not p_dir.is_dir(): continue
    print(f"\nChecking Plan: {p_dir.name}")
    ext_dir = p_dir / "extracted"
    files = sorted(ext_dir.glob("*.json"))
    print(f"  Found {len(files)} extracted files: {[f.name for f in files]}")
    
    if len(files) > 1:
        latest = files[-1]
        prev = files[-2]
        print(f"  Comparing {latest.name} vs {prev.name}")
        
        l_data = _load_json(latest)
        p_data = _load_json(prev)
        
        print(f"  Latest items: {len(l_data.get('lines', []))}")
        print(f"  Prev items: {len(p_data.get('lines', []))}")
        
        # Test the mapping logic
        prev_lines_map = {}
        for pl in p_data.get("lines", []):
            pd = pl.get("delivery_date")
            pq = pl.get("order_quantity", 0)
            if pd:
                prev_lines_map[pd] = prev_lines_map.get(pd, 0) + pq
                
        print(f"  Prev Map keys: {list(prev_lines_map.keys())[:3]}...")
        
        for line in l_data.get("lines", [])[:3]:
            d_date = line.get("delivery_date")
            qty = line.get("order_quantity", 0)
            print(f"    Line {d_date}: Qty {qty}")
            if d_date in prev_lines_map:
                old_qty = prev_lines_map[d_date]
                diff = qty - old_qty
                print(f"      -> Match! Old: {old_qty}, Diff: {diff}")
            else:
                print(f"      -> No match in prev.")

items = get_aggregated_items()
print(f"\nTotal Aggregated Items: {len(items)}")
sample_mods = [i for i in items if i.get("modification") is not None]
print(f"Items with modification: {len(sample_mods)}")
if sample_mods:
    print(f"Sample mod: {sample_mods[0]}")
