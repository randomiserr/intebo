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
            # use x_tolerance to better handle column spacing in different generators
            txt = p.extract_text(x_tolerance=2, y_tolerance=3) or ""
            chunks.append(txt)
    return "\n".join(chunks)

def find_label_field(text: str, label_variants: List[str], multiline: int = 1, next_line: bool = False) -> Optional[str]:
    """
    Finds a label and extracts its value. 
    If multiline > 1, it captures multiple lines.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        line_clean = line.strip()
        for variant in label_variants:
            # Flexible matching: allow for extra spaces or minor character differences
            # Escape regex chars but then replace escaped spaces with flex space
            var_esc = re.escape(variant).replace(r"\ ", r"\s*")
            pattern_match = re.compile(var_esc, re.IGNORECASE)
            
            if pattern_match.search(line_clean):
                val_parts = []
                
                # Regex to find where the label ends and extract the rest of the line
                # We handle optional trailing dots/colons/spaces
                pattern_sub = re.compile(rf".*?{var_esc}[.:\s]*", re.IGNORECASE)
                same_line = pattern_sub.sub("", line_clean).strip()
                
                # If the value is BEFORE the label (sometimes happens with pdfplumber ordering)
                # Check if same_line is empty but the line has content
                if not same_line and len(line_clean) > len(variant) + 5:
                     # Attempt to find if value is on the left
                     parts = pattern_match.split(line_clean)
                     if parts and parts[0].strip():
                         same_line = parts[0].strip()

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

    Priority:
      1. Standard 'Material No' labels (German/English PDFs)
      2. Czech 'Cislo materialu/Vykresu' line - pdfplumber often merges the label
         and value without a space (e.g. "VykresA 410 689 00 25 /"), so we use a
         regex to extract the part number pattern directly from the line.
      3. Fallback to 'Buses-Nr.' (purchaser-specific, only Daimler Buses)
    """
    # 1. Standard labels (German/English PDFs)
    labels = ["Material No", "Material-No", "Pos. Material No", "Pos. Materialnummer", "Materialnummer", "Material-Nummer"]
    val = find_label_field(text, labels, multiline=1)
    if val:
        noise = ["/Z-Format/\u00c4-Index", "Z-Format/\u00c4-Index", "/Z-Format", "/\u00c4-Index", "Daimler Buses-Nr.", "Buses-Nr."]
        for n in noise:
            val = val.replace(n, "").strip()
        return val.strip()

    # 2. Czech: "Cislo materialu/Vykresu" - pdfplumber merges label+value,
    #    e.g. "Cislo materialu/VykresA 410 689 00 25 /"
    #    We scan for that line and extract the part number with a regex.
    for ln in text.splitlines():
        if re.search(r"[Cc\u010c\u010d][i\u00ed]slo\s+materi[a\u00e1]lu", ln, re.IGNORECASE):
            # Look for Mercedes-style part number: A followed by digit groups
            m = re.search(r"(A\s*[\d\s.]+\d)\s*/?\s*$", ln)
            if m:
                return m.group(1).strip()

    # 3. Last resort: Buses-Nr (purchaser-specific)
    cz_val = find_label_field(text, ["Buses-Nr"], multiline=1)
    if cz_val:
        return cz_val.strip()

    return None


def find_release_nr(text: str) -> Optional[str]:
    """
    English: Release Nr.
    German: Abruf-Nr.
    Czech: Č. odvolávky
    """
    labels = ["Release Nr", "Release number", "Abruf-Nr", "Release-Nr",
              "Č. odvolávky", "C. odvolávky"]
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
    Czech: Druh palety
    """
    labels = ["Pal. Typ", "Pal.Typ", "Ladungsträger", "Druh palety"]
    res = find_label_field(text, labels)
    if res:
        # Prevent picking up adjacent labels if value is empty
        clean_res = res.strip().lower()
        if clean_res in ["volume", "fassungsvermögen", "volumen", "objem"]:
            return None
            
        m = re.match(r"([A-Za-z0-9\-_/]+)", res)
        val = m.group(1) if m else res
        
        if val.strip().lower() in ["volume", "fassungsvermögen", "volumen", "objem"]:
            return None
            
        return val
    return None

def find_volume(text: str) -> Tuple[Optional[int], Optional[str]]:
    """
    English: Volume
    German: Fassungsvermögen
    Czech: objem
    """
    labels = ["Volume", "Fassungsvermögen", "objem", "Objem"]
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
    Starts collecting after the first header ("Delivery date" / "Liefertermin" / "Datum dodání").
    Skips page footers and repeating headers. 
    Stops if it hits a document-level footer (like "Total").
    """
    lines = text.splitlines()
    block: List[str] = []
    
    header_patterns = [
        re.compile(r"Delivery\s*date", re.IGNORECASE),
        re.compile(r"Liefertermin", re.IGNORECASE),
        re.compile(r"Delivery\s*date\s*at", re.IGNORECASE),
        re.compile(r"Datum\s*dod.n", re.IGNORECASE),  # Czech: "Datum dodání"
    ]
    
    # Page-level footers to skip
    skip_patterns = [
        re.compile(r"^Page\b", re.IGNORECASE),
        re.compile(r"^Version\b", re.IGNORECASE),
        re.compile(r"^Seite\b", re.IGNORECASE),  # German "Page"
        re.compile(r"^do\s+závodu", re.IGNORECASE),  # Czech sub-header "do závodu"
        re.compile(r"^Objednac", re.IGNORECASE),  # Czech col header "Objednací mn."
        re.compile(r"^Datum\s+dod", re.IGNORECASE),  # Czech col header repeated
    ]
    
    # Document-level end markers
    end_patterns = [
        re.compile(r"^Total\s*[:\-]?\s*$", re.IGNORECASE),  # More specific "Total" line
        re.compile(r"^Gesamt\s*[:\-]?\s*$", re.IGNORECASE),
    ]

    has_started = False
    for ln in lines:
        ln_s = ln.strip()
        if not has_started:
            if any(p.search(ln_s) for p in header_patterns):
                has_started = True
            continue
        
        # Stop at document footer if it's a standalone Total or similar
        if any(p.fullmatch(ln_s) for p in end_patterns):
            break
            
        # Skip common filler lines
        if not ln_s or any(p.search(ln_s) for p in skip_patterns):
            continue
            
        # Skip repeating headers
        if any(p.search(ln_s) for p in header_patterns):
            continue

        # Skip Czech separator lines (all underscores/dashes)
        if re.fullmatch(r"[_\-=]{5,}", ln_s):
            continue
            
        block.append(ln_s)

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
                    # Qty is usually followed by a unit or a mod.
                    # In some cases, previous columns might have leaked numbers.
                    # We assume qty is the FIRST number after the date.
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
                if re.fullmatch(r"[+-]?\d+", token_stream[k]):
                    mod = int(token_stream[k])
                    break
                k += 1

            try:
                items.append(LineItem(delivery_date=d, order_quantity=qty, modification=mod))
            except ValidationError as ve:
                warnings.append(f"Validation error for row date={tok}: {ve}")

            # advance i to at least after qty
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
    # Czech: "Přijímající závod" / "Príjímací závod"
    out.receiving_factory = find_label_field(
        text,
        ["Receiv. factory", "Empfangswerk", "Receiv factory",
         "Přijímající závod", "Prijimajici zavod", "Přijímací závod"],
        multiline=3
    )
    # Czech: "Místo složení"
    out.warehouse_rampe = find_label_field(
        text,
        ["Warehouse rampe", "Warehouse-rampe", "Abladestelle", "Místo složení", "Misto slozeni"]
    )
    
    # Scheduling agreement is usually on the line below the label.
    # Czech: "Plán dodávek/Číslo nákupčího/Datum" compound label, value "5500974715/702 /10.03.2026".
    # We extract only the digits BEFORE the first "/".
    #
    # Strategy: try standard label search first, then fall back to a CZ-specific direct scan.
    sa_labels = ["Scheduling agreement No", "Scheduling agreement", "Lieferplan-Nr", "Lieferplannummer"]
    raw_agreement = find_label_field(text, sa_labels, next_line=True, multiline=3)

    if not raw_agreement:
        # Czech fallback: scan all lines for the pattern "Plán dodávek/Číslo" and
        # find the FIRST line after it that contains 8+ digits followed by "/"
        # The value may share a line with other text (e.g. "Studene 107 5500974715/702 /10.03.2026")
        _in_cz_sa = False
        for _ln in text.splitlines():
            _ls = _ln.strip()
            if re.search(r"Pl.n\s+dod.vek", _ls, re.IGNORECASE):
                _in_cz_sa = True
                continue
            if _in_cz_sa:
                _m = re.search(r"(\d{8,}/.*)", _ls)
                if _m:
                    raw_agreement = _m.group(1)
                    break

    if raw_agreement:
        # For Czech format "5500974715/702 /10.03.2026", take only digits before first "/"
        m_slash = re.match(r"(\d{5,})/", raw_agreement.strip())
        if m_slash:
            out.scheduling_agreement_no = m_slash.group(1)
        else:
            # Standard: extract first long sequence of digits (SA numbers are usually 8-10 digits)
            m = re.search(r"(\d{5,})", raw_agreement)
            if m:
                out.scheduling_agreement_no = m.group(1)
            else:
                # fallback to search for any number
                m2 = re.search(r"(\d+)", raw_agreement)
                out.scheduling_agreement_no = m2.group(1) if m2 else raw_agreement.splitlines()[0].strip()
    
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
