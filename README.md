# Lieferplan Intebo Parser (Local)

Local-only pilot for parsing “Lieferplan” PDFs and generating per-plan Excel files. Data stays on the machine; no external services are used.

## Requirements

- Python 3.10+

Install dependencies:

```bash
pip install -r requirements.txt
```

## CLI Usage

### Extract JSON

```bash
python extract_lieferplan.py input.pdf --out extracted.json
```

### Generate XLSX from extracted JSON

```bash
python generate_plan_xlsx.py extracted.json --out Plan.xlsx
```

### Process PDF end-to-end (CLI)

```bash
python process_pdf.py input.pdf --data-dir ./data
```

This creates a plan folder structure in `data/plans/<plan_key>/` and regenerates `output/Plan.xlsx` from scratch each run.

## Web UI (Local)

Run the local UI with FastAPI:

```bash
uvicorn app:app --host 0.0.0.0 --port 8001
```

Open `http://localhost:8001` in your browser to upload PDFs, review extracted data, approve versions, and download the generated XLSX.

## Data Storage

All files remain on the local filesystem:

```
data/
  plans/
    <plan_key>/
      raw/
      extracted/
      approved/
      output/
```

No data leaves the machine.
