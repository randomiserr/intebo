# Intebo Lieferplan Parser (Lokální verze)

Tento nástroj slouží k lokálnímu zpracování PDF souborů "Lieferplan", extrakci dat a generování Excel souborů. Aplikace také obsahuje webový dashboard pro přehlednou správu plánů, sledování termínů a kontrolu změn v množství ("ghost rows").

Ve stávající verzi zůstávají data uložena lokálně na vašem počítači a nejsou odesílána na žádné externí servery.

## Požadavky

- Python 3.10 nebo novější

### Instalace závislostí

Před prvním spuštěním nainstalujte potřebné knihovny:

```bash
pip install -r requirements.txt
```

## Spuštění Webové Aplikace

Pro spuštění hlavního rozhraní aplikace (Dashboardu) použijte příkaz:

```bash
uvicorn app:app --reload --port 8001
```

Aplikace bude dostupná na adrese: [http://localhost:8001](http://localhost:8001)

### Funkce Webové Aplikace
- **Nahrávání PDF**: Jednoduché nahrání nového Lieferplanu.
- **Přehled (Dashboard)**: Agregovaný pohled na všechny položky ze všech aktuálních plánů.
- **Detail Plánu**: Zobrazení konkrétního Lieferplanu s možností schválení (generuje finální Excel).
- **Notifikace**: Upozornění na blížící se termíny dodání (< 60 dní) a urgentní změny množství.
- **Sledování stavu**: Možnost označit jednotlivé řádky jako "Zpracované" (checkbox). Stav se ukládá a pamatuje si ho i při nahrání nové verze plánu.
- **Ghost Rows**: Pokud se v nové verzi plánu změní množství u již zpracované položky, systém zobrazí původní ("ghost") hodnotu pro kontrolu.

## Struktura Projektu

- **`app.py`**: Hlavní aplikace (FastAPI server). Obsahuje logiku dashboardu a API.
- **`state_manager.py`**: Správa stavu (ukládání informace o tom, které řádky jsou "hotové"). Data se ukládají do `data/row_states.json`.
- **`extract_lieferplan.py`**: Jádro pro extrakci dat z PDF (používá `pdfplumber`).
- **`generate_plan_xlsx.py`**: Generování výstupních Excel souborů.
- **`scripts/`**: Pomocné skripty (např. CLI nástroje).
    - `process_pdf.py`: Skript pro manuální zpracování PDF z příkazové řádky.
- **`tests/`**: Testovací a ověřovací skripty.
    - `verify_persistence.py`: Ověření, že se stavy řádků správně ukládají.
    - `reproduce_ghost.py`: Testování logiky pro detekci změn množství.
- **`data/`**: Složka pro ukládání nahraných plánů a stavových souborů.

## Použití CLI (Příkazové řádky)

Pokud nechcete používat webové rozhraní, můžete využít skripty v `scripts/`:

### Zpracování PDF (End-to-End)

```bash
python scripts/process_pdf.py cesta/k/souboru.pdf --data-dir ./data
```

Tento příkaz vytvoří strukturu složek v `data/plans/<označení_plánu>/` a vygeneruje Excel.

### Pouze extrakce JSON

```bash
python extract_lieferplan.py input.pdf --out extracted.json
```

### Pouze generování Excelu z JSON

```bash
python generate_plan_xlsx.py extracted.json --out Plan.xlsx
```

## Správa Dat

Všechna data jsou uložena ve složce `data/`.
- `data/plans/`: Obsahuje jednotlivé plány (raw PDF, extrahované JSONy, výstupní Excely).
- `data/row_states.json`: Ukládá stav (zaškrtnutí) jednotlivých řádků.
- `data/dismissed_notifications.json`: Ukládá seznam skrytých notifikací.

Žádná data neopouštějí váš počítač.
