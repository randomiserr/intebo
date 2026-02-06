import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber
from dateutil import parser as date_parser

DATE_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")
SIGNED_INT_RE = re.compile(r"^[+-]?\d+$")


def _normalize_number(value: str) -> Optional[int]:
    cleaned = value.replace(".", "").replace(",", "").replace(" ", "")
    if cleaned.isdigit():
        return int(cleaned)
    return None


def _extract_label(text: str, label: str) -> Optional[str]:
    pattern = re.compile(rf"{re.escape(label)}\s*:?\s*(.+)", re.IGNORECASE)
    for line in text.splitlines():
        match = pattern.search(line)
        if match:
            return match.group(1).strip()
    return None


def _extract_regex(text: str, pattern: str) -> Optional[str]:
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _parse_volume(text: str) -> Tuple[Optional[int], Optional[str]]:
    match = re.search(r"Volume\s*:?\s*([\d\.,]+)\s*([A-Za-z]+)?", text, re.IGNORECASE)
    if not match:
        return None, None
    raw_value = match.group(1)
    unit = match.group(2)
    value = _normalize_number(raw_value)
    return value, unit


def _parse_schedule(tokens: List[str]) -> List[Dict[str, Any]]:
    header_index = None
    for idx in range(len(tokens) - 3):
        if (
            tokens[idx].lower() == "delivery"
            and tokens[idx + 1].lower().startswith("date")
            and tokens[idx + 2].lower() == "order"
            and tokens[idx + 3].lower().startswith("quantity")
        ):
            header_index = idx + 4
            break
    if header_index is None:
        return []

    end_markers = {"total", "totals", "subtotal", "grand"}
    lines: List[Dict[str, Any]] = []
    i = header_index
    while i < len(tokens):
        token = tokens[i]
        if token.lower() in end_markers:
            break
        if DATE_RE.match(token):
            delivery_date_raw = token
            try:
                delivery_date = date_parser.parse(delivery_date_raw, dayfirst=True).date()
            except (ValueError, TypeError):
                i += 1
                continue
            quantity = None
            modification = None
            if i + 1 < len(tokens) and SIGNED_INT_RE.match(tokens[i + 1]):
                quantity = int(tokens[i + 1])
            if i + 2 < len(tokens) and SIGNED_INT_RE.match(tokens[i + 2]):
                modification = int(tokens[i + 2])
                i += 1
            if quantity is not None:
                lines.append(
                    {
                        "delivery_date": delivery_date.isoformat(),
                        "order_quantity": quantity,
                        "modification": modification,
                    }
                )
                i += 2
            else:
                i += 1
        else:
            i += 1
    return lines


def extract_lieferplan(pdf_path: Path) -> Dict[str, Any]:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    text_parts: List[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)

    text = "\n".join(text_parts)
    tokens = re.split(r"\s+", text.strip()) if text.strip() else []

    receiving_factory = _extract_label(text, "Receiv. factory")
    warehouse_rampe = _extract_label(text, "Warehouse rampe")
    material_no = _extract_regex(text, r"Material\s*No\.?\s*:?\s*([\w\-\/]+)")
    pal_typ = _extract_regex(text, r"Pal\.?\s*Typ\s*:?\s*([\w\-\/]+)")
    volume_value, volume_unit = _parse_volume(text)
    release_nr = _extract_regex(text, r"Release\s*Nr\.?\s*:?\s*([\w\-\/]+)")

    lines = _parse_schedule(tokens)

    missing_fields = []
    if not receiving_factory:
        missing_fields.append("receiving_factory")
    if not warehouse_rampe:
        missing_fields.append("warehouse_rampe")
    if not material_no:
        missing_fields.append("material_no")
    if not pal_typ:
        missing_fields.append("pal_typ")
    if volume_value is None:
        missing_fields.append("volume_value")
    if not release_nr:
        missing_fields.append("release_nr")

    warnings: List[str] = []
    if not lines:
        warnings.append("Schedule parsing returned 0 rows.")

    return {
        "receiving_factory": receiving_factory,
        "warehouse_rampe": warehouse_rampe,
        "material_no": material_no,
        "pal_typ": pal_typ,
        "volume_value": volume_value,
        "volume_unit": volume_unit,
        "release_nr": release_nr,
        "lines": lines,
        "missing_fields": missing_fields,
        "warnings": warnings,
        "debug": {
            "pdf": str(pdf_path),
            "lines_count": len(text_parts),
            "text_length": len(text),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract Lieferplan data from PDF")
    parser.add_argument("input_pdf", type=Path, help="Path to the PDF file")
    parser.add_argument("--out", type=Path, required=True, help="Output JSON file")
    args = parser.parse_args()

    payload = extract_lieferplan(args.input_pdf)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
