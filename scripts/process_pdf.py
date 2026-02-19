import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import sys
sys.path.append(str(Path(__file__).resolve().parent.parent))

from extract_lieferplan import extract_lieferplan
from generate_plan_xlsx import generate_xlsx


SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9_-]+")
UNDERSCORE_RE = re.compile(r"_+")


def sanitize_key(value: str) -> str:
    value = SAFE_CHARS_RE.sub("_", value.strip())
    value = UNDERSCORE_RE.sub("_", value)
    return value.strip("_") or "plan"


def compute_plan_key(payload: Dict[str, str]) -> str:
    parts = [
        payload.get("receiving_factory"),
        payload.get("warehouse_rampe"),
        payload.get("material_no"),
    ]
    if payload.get("release_nr"):
        parts.append(payload.get("release_nr"))
    joined = "_".join(part for part in parts if part)
    return sanitize_key(joined)


def next_version(approved_dir: Path) -> str:
    existing = sorted(approved_dir.glob("v*.json"))
    max_version = 0
    for path in existing:
        match = re.match(r"v(\d+)\.json", path.name)
        if match:
            max_version = max(max_version, int(match.group(1)))
    return f"v{max_version + 1:04d}.json"


def process_pdf(input_pdf: Path, data_dir: Path) -> Dict[str, str]:
    payload = extract_lieferplan(input_pdf)
    plan_key = compute_plan_key(payload)

    plan_dir = data_dir / "plans" / plan_key
    raw_dir = plan_dir / "raw"
    extracted_dir = plan_dir / "extracted"
    approved_dir = plan_dir / "approved"
    output_dir = plan_dir / "output"

    for directory in [raw_dir, extracted_dir, approved_dir, output_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")

    raw_path = raw_dir / f"{timestamp}.pdf"
    shutil.copy2(input_pdf, raw_path)

    extracted_path = extracted_dir / f"{timestamp}.json"
    with extracted_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    payload_with_meta = dict(payload)
    payload_with_meta["approved_at"] = timestamp

    current_path = approved_dir / "current.json"
    with current_path.open("w", encoding="utf-8") as handle:
        json.dump(payload_with_meta, handle, ensure_ascii=False, indent=2)

    version_path = approved_dir / next_version(approved_dir)
    with version_path.open("w", encoding="utf-8") as handle:
        json.dump(payload_with_meta, handle, ensure_ascii=False, indent=2)

    output_path = output_dir / "Plan.xlsx"
    generate_xlsx(payload_with_meta, output_path)

    return {"plan_key": plan_key, "output_path": str(output_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Process Lieferplan PDF")
    parser.add_argument("input_pdf", type=Path, help="Path to the PDF file")
    parser.add_argument("--data-dir", type=Path, default=Path("./data"))
    args = parser.parse_args()

    if not args.input_pdf.exists():
        raise FileNotFoundError(f"PDF not found: {args.input_pdf}")

    result = process_pdf(args.input_pdf, args.data_dir)
    print(f"Plan key: {result['plan_key']}")
    print(f"Output XLSX: {result['output_path']}")


if __name__ == "__main__":
    main()
