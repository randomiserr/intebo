import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill


def _autosize_columns(ws) -> None:
    for column_cells in ws.columns:
        max_length = 0
        column = column_cells[0].column_letter
        for cell in column_cells:
            if cell.value is not None:
                max_length = max(max_length, len(str(cell.value)))
        ws.column_dimensions[column].width = max(12, max_length + 2)


def generate_xlsx(payload: Dict[str, Any], out_path: Path, changelog: Optional[List[Dict[str, Any]]] = None) -> None:
    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"

    header_font = Font(bold=True)
    missing_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    warning_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")

    rows = [
        ("Receiving factory", payload.get("receiving_factory")),
        ("Warehouse rampe", payload.get("warehouse_rampe")),
        ("Material No.", payload.get("material_no")),
        ("Pal.Typ", payload.get("pal_typ")),
        (
            "Volume",
            f"{payload.get('volume_value')} {payload.get('volume_unit') or ''}".strip(),
        ),
        ("Release Nr.", payload.get("release_nr")),
        ("Missing fields", ", ".join(payload.get("missing_fields", [])) or "None"),
        ("Warnings", ", ".join(payload.get("warnings", [])) or "None"),
    ]

    for idx, (label, value) in enumerate(rows, start=1):
        summary.cell(row=idx, column=1, value=label).font = header_font
        cell = summary.cell(row=idx, column=2, value=value)
        if label == "Missing fields" and payload.get("missing_fields"):
            cell.fill = missing_fill
        if label == "Warnings" and payload.get("warnings"):
            cell.fill = warning_fill

    _autosize_columns(summary)

    schedule = wb.create_sheet("Schedule")
    schedule.append(["Delivery date", "Order Quantity", "Modification"])
    for cell in schedule[1]:
        cell.font = header_font

    for line in payload.get("lines", []):
        schedule.append(
            [
                line.get("delivery_date"),
                line.get("order_quantity"),
                line.get("modification"),
            ]
        )

    _autosize_columns(schedule)

    changelog_ws = wb.create_sheet("ChangeLog")
    changelog_ws.append(["Timestamp", "Note"])
    for cell in changelog_ws[1]:
        cell.font = header_font

    if changelog:
        for entry in changelog:
            changelog_ws.append([entry.get("timestamp"), entry.get("note")])
    else:
        changelog_ws.append(["", "No changelog entries."])

    _autosize_columns(changelog_ws)

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

    generate_xlsx(payload, args.out, changelog=changelog_data)


if __name__ == "__main__":
    main()
