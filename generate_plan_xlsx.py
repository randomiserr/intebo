import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

def _style_cell(cell, bold=False, fill_color=None, align=None):
    if bold: cell.font = Font(bold=True)
    if fill_color: cell.fill = PatternFill(start_color=fill_color, end_color=fill_color, fill_type="solid")
    if align: cell.alignment = Alignment(horizontal=align)
    else: cell.alignment = Alignment(horizontal="left") # Default to left
    
    thin = Side(border_style="thin", color="000000")
    cell.border = Border(top=thin, left=thin, right=thin, bottom=thin)

def generate_xlsx(payload: Dict[str, Any], out_path: Path, history: Optional[List[Dict[str, Any]]] = None) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Lieferplan"

    # 1. Summary Section Header
    curr_row = 1
    cell = ws.cell(row=curr_row, column=1, value="SOUHRN INFORMACÍ")
    _style_cell(cell, bold=True, fill_color="D9D9D9")
    ws.merge_cells(start_row=curr_row, start_column=1, end_row=curr_row, end_column=2)
    curr_row += 1

    labels = [
        ("Číslo Lieferplánu", payload.get("scheduling_agreement_no")),
        ("Změnovka (Release Nr.)", payload.get("release_nr")),
        ("-", "-"),
        ("Dodací Adresa", payload.get("receiving_factory")),
        ("Rampa", payload.get("warehouse_rampe")),
        ("-", "-"),
        ("Číslo výrobku", payload.get("material_no")),
        ("Balení", payload.get("pal_typ")),
        ("Množství", payload.get("volume_value", 0)),
    ]

    for label, val in labels:
        if label == "-":
            curr_row += 1
            continue
        
        c1 = ws.cell(row=curr_row, column=1, value=label)
        c2 = ws.cell(row=curr_row, column=2, value=val or "Chybí")
        _style_cell(c1, bold=True, fill_color="F2F2F2")
        _style_cell(c2, align="left")
        
        if val is None:
            c2.font = Font(color="D32F2F", italic=True)

        if label == "Množství" and val is not None:
            c2.number_format = '#,##0'
            unit = payload.get("volume_unit")
            if unit:
                c1.value = f"{label} (v {unit})"
        curr_row += 1

    curr_row += 2
    # 2. Schedule Section Header
    cell = ws.cell(row=curr_row, column=1, value="HARMONOGRAM DODÁVEK")
    _style_cell(cell, bold=True, fill_color="D9D9D9")
    ws.merge_cells(start_row=curr_row, start_column=1, end_row=curr_row, end_column=5)
    curr_row += 1

    headers = ["#", "Termín dodání", "Objednané množství", "Změna", "Dní do dodání"]
    for i, h in enumerate(headers, 1):
        cell = ws.cell(row=curr_row, column=i, value=h)
        _style_cell(cell, bold=True, fill_color="D9EAD3", align="center")
    
    curr_row += 1
    today = datetime.now().date()
    
    for idx, line in enumerate(payload.get("lines", []), 1):
        dd_raw = line.get("delivery_date") # YYYY-MM-DD
        display_date = dd_raw
        days_to = ""
        row_fill = None
        is_bold = True
        
        try:
            dd = datetime.strptime(dd_raw, "%Y-%m-%d").date()
            display_date = dd.strftime("%d.%m.%Y")
            diff = (dd - today).days
            days_to = diff
            
            modification = line.get("modification")
            
            if diff < 0:
                row_fill = "F9F9F9" # Lightest grey for past items
                is_bold = False # Unbold past items
            elif diff < 45 and modification and modification != 0:
                row_fill = "F4CCCC" # Red for urgent modifications
        except Exception: 
            pass

        row_data = [
            idx,
            display_date,
            line.get("order_quantity"),
            line.get("modification"),
            days_to
        ]
        for i, val in enumerate(row_data, 1):
            cell = ws.cell(row=curr_row, column=i, value=val)
            _style_cell(cell, bold=is_bold and i==2, align="center", fill_color=row_fill)
            
            if i == 3: # Quantity
                cell.number_format = '#,##0'
            if i == 4 and val: # Modification
                cell.number_format = '+#,##0;-#,##0;0'
        curr_row += 1

    # Safe autosize columns
    for col_idx in range(1, 6):
        max_w = 0
        column_letter = get_column_letter(col_idx)
        for row in range(1, curr_row):
            cell = ws.cell(row=row, column=col_idx)
            if cell.value:
                lines = str(cell.value).split('\n')
                max_w = max(max_w, max(len(ln) for ln in lines))
        ws.column_dimensions[column_letter].width = min(40, max_w + 5)

    # 3. History Sheet
    h_ws = wb.create_sheet("Historie verzí")
    h_headers = ["Datum nahrání", "Změnovka", "Číslo výrobku", "Složka"]
    for i, h in enumerate(h_headers, 1):
        cell = h_ws.cell(row=1, column=i, value=h)
        _style_cell(cell, bold=True, fill_color="CFE2F3", align="center")
    
    if history:
        for r_idx, entry in enumerate(history, 2):
            h_ws.cell(row=r_idx, column=1, value=entry.get("uploaded_at"))
            h_ws.cell(row=r_idx, column=2, value=entry.get("release_nr"))
            h_ws.cell(row=r_idx, column=3, value=entry.get("material_no"))
            h_ws.cell(row=r_idx, column=4, value=entry.get("plan_id"))
            for i in range(1, 5):
                _style_cell(h_ws.cell(row=r_idx, column=i), align="left")
    
    for col_idx in range(1, 5):
        max_w = 0
        column_letter = get_column_letter(col_idx)
        for row in range(1, (len(history) if history else 0) + 2):
            cell = h_ws.cell(row=row, column=col_idx)
            if cell.value:
                max_w = max(max_w, len(str(cell.value)))
        h_ws.column_dimensions[column_letter].width = max_w + 5

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Lieferplan XLSX")
    parser.add_argument("input_json", type=Path, help="Extracted JSON file")
    parser.add_argument("--out", type=Path, required=True, help="Output XLSX file")
    parser.add_argument("--changelog", type=Path, help="Optional changelog JSON file")
    args = parser.parse_args()
    with args.input_json.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    changelog_data = None
    if args.changelog and args.changelog.exists():
        with args.changelog.open("r", encoding="utf-8") as handle:
            changelog_data = json.load(handle)
    generate_xlsx(payload, args.out, history=changelog_data)

if __name__ == "__main__":
    main()
