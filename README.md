# Intebo Lieferplan Parser

Nástroj pro zpracování PDF souborů "Lieferplan", extrakci dat, sledování dodávek a generování Excel souborů. Webový dashboard umožňuje přehlednou správu plánů, sledování termínů, kontrolu změn množství a nahrání skladových inventur (Nadvýroba).

## Rychlý start (Windows)

1. Nainstalovat **Python 3.10–3.13** z [python.org/downloads](https://www.python.org/downloads/) — při instalaci **zaškrtnout „Add python.exe to PATH"**.
2. Stáhnout projekt: `git clone https://github.com/randomiserr/intebo.git` (nebo ZIP z GitHubu).
3. Dvojklik na **`start.bat`**.
   - Při prvním spuštění se vytvoří `config.ini` ze šablony a otevře se v Notepadu — vyplnit `data_dir`, uložit, zavřít.
   - Skript pak doinstaluje knihovny a spustí server.
4. V prohlížeči otevřít `http://localhost:8000`.

### Tichý start bez terminálu (`Intebo LP.vbs`)

Po prvním nastavení můžete místo `start.bat` používat **`Intebo LP.vbs`** — spustí server na pozadí bez terminálového okna a automaticky otevře prohlížeč.

Pro pohodlí: pravým tlačítkem na `Intebo LP.vbs` → **Odeslat → Plocha (vytvořit zástupce)**. Zástupci lze přiřadit ikonu (Vlastnosti → Změnit ikonu).

Server běží na pozadí, dokud nezavřete `python.exe` v Task Manageru nebo nerestartujete PC.

> ⚠️ **Doporučujeme Python 3.12.** Python 3.14 je čerstvý a některé knihovny s ním ještě nefungují spolehlivě.

## Konfigurace (`config.ini`)

Veškerá konfigurace na jednom místě — bez úprav kódu. Soubor `config.ini` je per-PC (v `.gitignore`), šablona je `config.ini.example`.

```ini
[paths]
; Lokální:        data_dir = C:\intebo-data
; Sdílená:        data_dir = \\fileserver\intebo-data
; Google Drive:   data_dir = G:\My Drive\intebo-data
; Prázdné:        použije se ./data vedle app.py
data_dir =

[server]
host = localhost    ; nebo 0.0.0.0 pro přístup z LAN
port = 8000
```

Hodnoty z `config.ini` lze přebít proměnnými prostředí `INTEBO_DATA_DIR`, `INTEBO_HOST`, `INTEBO_PORT` (užitečné pro nasazení jako služba).

## Funkce

### Lieferplany (PDF)
- **Nahrávání PDF** — nahrání nového Lieferplanu přes webové rozhraní
- **Detail plánu** — zobrazení konkrétního Lieferplanu s možností schválení (generuje finální Excel)
- **Přehled (Dashboard)** — agregovaný pohled na všechny položky ze všech aktuálních plánů
- **Notifikace** — upozornění na blížící se termíny dodání a urgentní změny množství
- **Sledování stavu** — možnost označit řádky jako „Zpracované" (checkbox), stav se ukládá trvale
- **Ghost Rows** — pokud se v nové verzi plánu změní množství u zpracované položky, systém zobrazí původní hodnotu

### Inventura / Nadvýroba (Excel)
- **Nahrávání inventury** — nahrání Excel souboru (POHODA export) se skladovými zásobami
- **Zobrazení nadvýroby** — na detailu každého plánu se zobrazí odpovídající skladové množství (matched dle čísla materiálu)
- **Matching** — automatický matching materiálů i při odlišných formátech (různé mezery, varianty kódů)

## Struktura projektu

| Soubor | Popis |
|---|---|
| `app.py` | Hlavní FastAPI server — API, routing, šablony |
| `config.py` | Načítá `config.ini` + proměnné prostředí (nesahat — mění se `config.ini`) |
| `config.ini.example` | Šablona konfigurace (kopíruje se na `config.ini` při prvním startu) |
| `start.bat` | Spouštěcí skript pro Windows (instalace závislostí + spuštění serveru) |
| `extract_lieferplan.py` | Extrakce dat z PDF (pdfplumber + Pydantic) |
| `generate_plan_xlsx.py` | Generování výstupních Excel souborů |
| `inventory_parser.py` | Parser inventurních Excel souborů (POHODA) |
| `state_manager.py` | Správa stavu řádků (zaškrtnutí/zpracování) |
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

Single-process Python (FastAPI) web app. Filesystem-based storage, žádná databáze.

### Možnosti nasazení

| Scénář | Postup |
|---|---|
| **Jeden uživatel, jedno PC** | `start.bat`, `data_dir` na lokální cestu. |
| **Více uživatelů, sdílená data, každý běží lokálně** | `start.bat` na každém PC, `data_dir` na sdílené úložiště (UNC / Google Drive). Pozor: žádné zámky — souběžný zápis může způsobit ztrátu dat. |
| **Centrální server (doporučeno pro tým)** | Aplikace běží jako služba na serveru (Windows Service / systemd), uživatelé jen otevřou prohlížeč. Bez konfliktů, čistá záloha. |

### Požadavky na server

- Python 3.10–3.13, ~1 GB RAM, ~500 MB disk
- Síťová dostupnost portu (8000 nebo dle `config.ini`) z klientských PC

### Data Directory

Default: `./data` vedle `app.py`. Lze přebít:

```bash
# Linux
export INTEBO_DATA_DIR=/mnt/storage/intebo-data
# PowerShell
$env:INTEBO_DATA_DIR = "D:\intebo-data"
```

Nebo přes `config.ini` (viz výše). Podporuje libovolný připojený souborový systém — lokální disk, NFS, SMB, Azure Files, AWS EFS atd. Složka musí existovat a aplikační uživatel musí mít read/write.

**Struktura:**
```
$INTEBO_DATA_DIR/
├── plans/                          # Nahrané plány (PDF + extracted JSON + output XLSX)
├── inventory.json                  # Poslední upload inventury (přepisuje se)
├── row_states.json                 # Stavy checkboxů (jediný nereprodukovatelný stav)
└── dismissed_notifications.json
```

> `row_states.json` je jediný soubor se stavem, který nelze obnovit z uploadů. Vše ostatní lze přegenerovat.

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
ExecStart=/usr/bin/python3 -m uvicorn app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now intebo
```

### Důležité

- **Single worker.** Nepoužívejte `--workers > 1` — app používá in-memory caching s disk persistence.
- **Nemá auth.** Omezit přístup firewallem. Kdokoliv s URL může měnit data.
- **Souběžnost.** Reads neomezeně. Zápisy v pohodě při běžném použití (desítky uživatelů). Pro sdílenou složku bez serveru: pozor na souběžný zápis.
- **Zálohování.** `row_states.json` a obsah `plans/raw/` (originální PDF) — to ostatní lze přegenerovat.
