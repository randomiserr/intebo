#!/usr/bin/env python3
"""
Extract key fields + schedule table from a Lieferplan-like PDF (local-only).
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber
from pydantic import BaseModel, Field, ValidationError, field_validator
from dateutil.parser import parse as date_parser


# -----------------------------
# Models
# -----------------------------

class LineItem(BaseModel):
    delivery_date: date
    order_quantity: int
    modification: Optional[int] = None  # can be None if not found

    @field_validator("order_quantity")
    @classmethod
    def qty_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("order_quantity cannot be negative")
        return v


class LieferplanExtract(BaseModel):
    scheduling_agreement_no: Optional[str] = None
    receiving_factory: Optional[str] = None
    warehouse_rampe: Optional[str] = None
    material_no: Optional[str] = None
    pal_typ: Optional[str] = None
    volume_value: Optional[int] = None
    volume_unit: Optional[str] = None
    release_nr: Optional[str] = None

    lines: List[LineItem] = Field(default_factory=list)

    missing_fields: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    debug: Dict[str, Any] = Field(default_factory=dict)


# -----------------------------
# Helpers
# -----------------------------

RE_INT_DE = re.compile(r"(?<!\d)(\d{1,3}(?:[.,]\d{3})*|\d+)(?!\d)")
RE_DATE_DDMMYYYY = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")

def normalize_int_de(s: str) -> Optional[int]:
    """
    Parse German-ish integer formatting:
      "10,000" -> 10000
      "10.000" -> 10000
      "10000"  -> 10000
    """
    s = s.strip()
    m = RE_INT_DE.search(s)
    if not m:
        return None
    raw = m.group(1)
    raw = raw.replace(".", "").replace(",", "")
    try:
        return int(raw)
    except ValueError:
        return None

def parse_date_ddmmYYYY(s: str) -> Optional[date]:
    s = s.strip()
    try:
        dt = date_parser(s, dayfirst=True).date()
        return dt
    except Exception:
        return None

def join_pages_text(pdf_path: Path) -> str:
    chunks: List[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for p in pdf.pages:
            txt = p.extract_text() or ""
            chunks.append(txt)
    return "\n".join(chunks)

def find_label_field(text: str, label_variants: List[str], multiline: int = 1, next_line: bool = False) -> Optional[str]:
    """
    Finds a label and extracts its value. 
    If multiline > 1, it captures multiple lines.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        for variant in label_variants:
            # We look for the label. We use re.sub for cleaning, but searching is simple.
            if variant.lower() in line.lower():
                val_parts = []
                
                # Regex to find where the label ends and extract the rest of the line
                # We handle optional trailing dots/colons/spaces
                pattern = re.compile(rf".*?{re.escape(variant)}[.:\s]*", re.IGNORECASE)
                same_line = pattern.sub("", line).strip()
                
                is_weak = not same_line or re.fullmatch(r"[:\s/.-]+", same_line)
                
                if next_line or is_weak:
                    start_idx = i + 1
                else:
                    val_parts.append(same_line)
                    start_idx = i + 1
                
                while len(val_parts) < multiline and start_idx < len(lines):
                    next_val = lines[start_idx].strip()
                    if next_val:
                        val_parts.append(next_val)
                    start_idx += 1
                
                if val_parts:
                    return "\n".join(val_parts).strip()
    return None

def find_material_no(text: str) -> Optional[str]:
    """
    Captures Material No. Supporting multi-line if the label and value are split.
    Keeps the / and trailing numbers.
    """
    labels = ["Material No", "Material-No", "Pos. Material No", "Pos. Materialnummer", "Materialnummer"]
    val = find_label_field(text, labels, multiline=1)
    if val:
        # Strip German noise if it leaked into the value
        noise = ["/Z-Format/Ä-Index", "Z-Format/Ä-Index", "/Z-Format", "/Ä-Index"]
        for n in noise:
            val = val.replace(n, "").strip()
        return val.strip()
    return None

def find_release_nr(text: str) -> Optional[str]:
    """
    English: Release Nr.
    German: Abruf-Nr.
    """
    labels = ["Release Nr", "Release number", "Abruf-Nr"]
    res = find_label_field(text, labels)
    if res:
        # Extract first "word" or valid part
        m = re.match(r"([A-Za-z0-9\-_/]+)", res)
        return m.group(1) if m else res
    return None

def find_pal_typ(text: str) -> Optional[str]:
    """
    English: Pal.Typ
    German: Ladungsträger
    """
    labels = ["Pal. Typ", "Pal.Typ", "Ladungsträger"]
    res = find_label_field(text, labels)
    if res:
        m = re.match(r"([A-Za-z0-9\-_/]+)", res)
        return m.group(1) if m else res
    return None

def find_volume(text: str) -> Tuple[Optional[int], Optional[str]]:
    """
    English: Volume
    German: Fassungsvermögen
    """
    labels = ["Volume", "Fassungsvermögen"]
    res = find_label_field(text, labels)
    if res:
        m = re.search(r"([0-9.,]+)\s*([A-Za-z]+)?", res)
        if m:
            val = normalize_int_de(m.group(1))
            unit = (m.group(2) or "").strip() or None
            return val, unit
    return None, None

def extract_table_block_lines(text: str) -> List[str]:
    """
    Locate all schedule table rows across all pages.
    Starts collecting after the first header ("Delivery date" / "Liefertermin").
    Skips page footers and repeating headers. 
    Stops if it hits a document-level footer (like "Total").
    """
    lines = text.splitlines()
    block: List[str] = []
    
    header_patterns = [
        re.compile(r"Delivery\s+date", re.IGNORECASE),
        re.compile(r"Liefertermin", re.IGNORECASE),
    ]
    
    # Page-level footers to skip
    skip_patterns = [
        re.compile(r"^Page\b", re.IGNORECASE),
        re.compile(r"^Version\b", re.IGNORECASE),
        re.compile(r"^Seite\b", re.IGNORECASE), # German "Page"
    ]
    
    # Document-level end markers
    end_patterns = [
        re.compile(r"^Total\b", re.IGNORECASE),
        re.compile(r"^Gesamt\b", re.IGNORECASE), # German "Total"
    ]

    has_started = False
    for ln in lines:
        if not has_started:
            if any(p.search(ln) for p in header_patterns):
                has_started = True
            continue
        
        # Stop at document footer
        if any(p.search(ln) for p in end_patterns):
            break
            
        # Skip page footers
        if any(p.search(ln) for p in skip_patterns):
            continue
            
        # Skip repeating headers
        if any(p.search(ln) for p in header_patterns):
            continue
            
        block.append(ln)

    return block

def parse_schedule_rows(block_lines: List[str]) -> Tuple[List[LineItem], List[str]]:
    """
    Improved row parsing using token stream. Handles rows like: "D 16.03.2026 30 0 ..."
    """
    warnings: List[str] = []
    if not block_lines:
        return [], ["No schedule block found (could not locate table header)."]

    token_stream: List[str] = []
    for ln in block_lines:
        ln = re.sub(r"\s+", " ", ln.strip())
        if not ln:
            continue
        token_stream.extend(ln.split(" "))

    items: List[LineItem] = []
    i = 0
    while i < len(token_stream):
        tok = token_stream[i]
        if RE_DATE_DDMMYYYY.fullmatch(tok):
            d = parse_date_ddmmYYYY(tok)
            if not d:
                warnings.append(f"Failed to parse date token: {tok}")
                i += 1
                continue

            # find next integer token for qty (could have separators)
            qty = None
            j = i + 1
            while j < len(token_stream):
                # Try parsing as number
                candidate_qty = normalize_int_de(token_stream[j])
                if candidate_qty is not None:
                    qty = candidate_qty
                    break
                # If we hit another date, we missed the quantity
                if RE_DATE_DDMMYYYY.fullmatch(token_stream[j]):
                    break
                j += 1
            
            if qty is None:
                warnings.append(f"Found date {tok} but no quantity found before next date.")
                i += 1
                continue

            # find modification: next signed int after qty
            mod = None
            k = j + 1
            while k < len(token_stream):
                if RE_DATE_DDMMYYYY.fullmatch(token_stream[k]):
                    break
                # Modification can be +X, -X, or simply X. 
                # We use a regex that handles signs.
                if re.fullmatch(r"[+-]?\d+", token_stream[k]):
                    mod = int(token_stream[k])
                    break
                k += 1

            try:
                items.append(LineItem(delivery_date=d, order_quantity=qty, modification=mod))
            except ValidationError as ve:
                warnings.append(f"Validation error for row date={tok}: {ve}")

            # advance i to after qty
            i = j + 1
            continue

        i += 1

    # De-duplicate
    unique: Dict[Tuple[date, int, Optional[int]], LineItem] = {}
    for it in items:
        unique[(it.delivery_date, it.order_quantity, it.modification)] = it

    sorted_items = sorted(unique.values(), key=lambda x: x.delivery_date)
    if not sorted_items:
        warnings.append("Schedule table parsed but produced 0 rows (format may differ).")
    return sorted_items, warnings


# -----------------------------
# Main extraction
# -----------------------------

def extract_lieferplan(pdf_path: Path) -> Dict[str, Any]:
    """
    Main entry point for extraction. Returns a serializable dict.
    """
    text = join_pages_text(pdf_path)

    out = LieferplanExtract()

    # Capture up to 3 lines for address
    out.receiving_factory = find_label_field(text, ["Receiv. factory", "Empfangswerk"], multiline=3)
    out.warehouse_rampe = find_label_field(text, ["Warehouse rampe", "Abladestelle"])
    
    # Scheduling agreement is usually on the line below the label, 
    # but sometimes there's a manufacturer name in between. We look up to 3 lines.
    raw_agreement = find_label_field(text, ["Scheduling agreement No", "Lieferplan-Nr"], next_line=True, multiline=3)
    if raw_agreement:
        # User wants ONLY the first number (before "/")
        # We extract the first sequence that looks like digits
        m = re.search(r"(\d+)", raw_agreement)
        out.scheduling_agreement_no = m.group(1) if m else raw_agreement.splitlines()[0].split()[0].split("/")[0]
    
    out.material_no = find_material_no(text)
    out.pal_typ = find_pal_typ(text)
    out.release_nr = find_release_nr(text)

    vol_val, vol_unit = find_volume(text)
    out.volume_value, out.volume_unit = vol_val, vol_unit

    block = extract_table_block_lines(text)
    lines, table_warnings = parse_schedule_rows(block)
    out.lines = lines
    out.warnings.extend(table_warnings)

    required = [
        ("scheduling_agreement_no", out.scheduling_agreement_no),
        ("receiving_factory", out.receiving_factory),
        ("warehouse_rampe", out.warehouse_rampe),
        ("material_no", out.material_no),
        ("pal_typ", out.pal_typ),
        ("volume_value", out.volume_value),
        ("release_nr", out.release_nr),
    ]
    out.missing_fields = [name for name, val in required if val in (None, "", [])]

    out.debug = {
        "pdf": str(pdf_path),
        "lines_count": len(out.lines),
        "text_length": len(text),
    }
    
    # Return as JSON-serializable dict
    return out.model_dump(mode="json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=str, help="Path to PDF file")
    ap.add_argument("--out", type=str, default="", help="Optional output JSON file path")
    args = ap.parse_args()

    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    payload = extract_lieferplan(pdf_path)

    print(json.dumps(payload, indent=2))

    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
