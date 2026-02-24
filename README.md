# Intebo Lieferplan Parser

Nástroj pro zpracování PDF souborů "Lieferplan", extrakci dat, sledování dodávek a generování Excel souborů. Webový dashboard umožňuje přehlednou správu plánů, sledování termínů, kontrolu změn množství a nahrání skladových inventur (Nadvýroba).

## Požadavky

- Python 3.10+

### Requirements

```bash
pip install -r requirements.txt
```

## Spuštění

```bash
uvicorn app:app --host 0.0.0.0 --port 80
```

Aplikace bude dostupná na `http://<IP-adresa>/`. Pro lokální vývoj: `http://localhost:8001` s `--port 8001`.

## Funkce

### Lieferplany (PDF)
- **Nahrávání PDF** — nahrání nového Lieferplanu přes webové rozhraní
- **Detail plánu** — zobrazení konkrétního Lieferplanu s možností schválení (generuje finální Excel)
- **Přehled (Dashboard)** — agregovaný pohled na všechny položky ze všech aktuálních plánů
- **Notifikace** — upozornění na blížící se termíny dodání a urgentní změny množství
- **Sledování stavu** — možnost označit řádky jako "Zpracované" (checkbox), stav se ukládá trvale
- **Ghost Rows** — pokud se v nové verzi plánu změní množství u zpracované položky, systém zobrazí původní hodnotu

### Inventura / Nadvýroba (Excel)
- **Nahrávání inventury** — nahrání Excel souboru (POHODA export) se skladovými zásobami
- **Zobrazení nadvýroby** — na detailu každého plánu se zobrazí odpovídající skladové množství (matched dle čísla materiálu)
- **Matching** — automatický matching materiálů i při odlišných formátech (různé mezery, varianty kódů)

## Struktura projektu

| Soubor | Popis |
|---|---|
| `app.py` | Hlavní FastAPI server — API, routing, šablony |
| `extract_lieferplan.py` | Extrakce dat z PDF (pdfplumber + Pydantic) |
| `generate_plan_xlsx.py` | Generování výstupních Excel souborů |
| `inventory_parser.py` | Parser inventurních Excel souborů (POHODA) |
| `state_manager.py` | Správa stavu řádků (zaškrtnutí/zpracování) |
| `config.py` | Konfigurace (port, cesta k datům) |
| `templates/` | HTML šablony (index, detail plánu, dashboard) |
| `scripts/process_pdf.py` | CLI skript pro zpracování PDF |

## CLI (Příkazová řádka)

```bash
# Zpracování PDF (end-to-end)
python scripts/process_pdf.py cesta/k/souboru.pdf --data-dir ./data

# Pouze extrakce JSON
python extract_lieferplan.py input.pdf --out extracted.json
```

---

## Nasazení (Deployment)

Single-process Python (FastAPI) web app. Filesystem-based storage, no database.

### Požadavky na server

- Python 3.10+, ~1 GB RAM, ~500 MB disk
- Network access from client machines (port 80 or custom)

### Data Directory

Default: `./data` relative to app root.

Override via environment variable:
```bash
export INTEBO_DATA_DIR=/mnt/storage/intebo-data    # Linux
$env:INTEBO_DATA_DIR = "D:\intebo-data"            # PowerShell
```

Supports any mounted filesystem — local disk, NFS, SMB, Azure Files, AWS EFS, etc.  
The directory must exist and the app user needs read/write permissions.

**Struktura:**
```
$INTEBO_DATA_DIR/
├── plans/                     # Uploaded plans (PDF + extracted JSON + output XLSX)
├── inventory.json             # Latest inventory upload (overwritten daily)
├── row_states.json            # Checkbox states (only persistent app state)
└── dismissed_notifications.json
```

> `row_states.json` is the only file with state that can't be recreated from uploads. Everything else is regenerated on upload.

### Windows Service (NSSM)

```cmd
nssm install InteboParser "C:\Python310\python.exe" "-m uvicorn app:app --host 0.0.0.0 --port 80"
nssm set InteboParser AppDirectory "C:\Apps\intebo"
nssm set InteboParser AppEnvironmentExtra "INTEBO_DATA_DIR=D:\intebo-data"
nssm start InteboParser
```

### Linux (systemd)

`/etc/systemd/system/intebo.service`:
```ini
[Unit]
Description=Intebo Lieferplan Parser
After=network.target

[Service]
Type=simple
User=intebo
WorkingDirectory=/opt/intebo
Environment=INTEBO_DATA_DIR=/mnt/storage/intebo-data
ExecStart=/usr/bin/python3 -m uvicorn app:app --host 0.0.0.0 --port 80
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now intebo
```

### Důležité

- **Single worker only.** Do not use `--workers` — app uses in-memory caching with disk persistence.
- **No authentication.** Restrict access via firewall or reverse proxy with auth.
- **Concurrency.** Reads unlimited. Writes work fine for typical usage (single-digit concurrent users).
- **Backups.** Only `row_states.json` has non-recreatable state. Include it in existing backup rotation if needed.
