import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from extract_lieferplan import extract_lieferplan
from state_manager import StateManager
from inventory_parser import parse_inventory_xlsx
from notes_manager import NotesManager
from plan_index import PlanIndex
from pydantic import BaseModel

import copy
import os
import threading
import config

app = FastAPI()
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = config.DATA_DIR
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "templates")), name="static")

# --- Datova vrstva ---------------------------------------------------------
# Cely pristup k (obvykle sitove) datove slozce jde pres tyto tri objekty.
# Inicializace bezi v jednom vlakne na pozadi (single-flight), aby se server
# pri nedostupnem/zaseknutem sharu nezasekl pri startu (jinak by se prohlizec
# nikdy neotevrel) a aby zaseknuty pokus nespoustel dalsi vlakna.
plan_index = PlanIndex(DATA_DIR)
state_manager = None       # type: ignore[assignment]  # != None => datova vrstva pripravena
notes_manager = None       # type: ignore[assignment]
DATA_ERROR = None          # posledni chyba pri pristupu k datove slozce (str)

INIT_TIMEOUT_SECONDS = 15   # jak dlouho ceka PRVNI pozadavek na nacteni

_init_lock = threading.Lock()
_init_thread = None        # type: ignore[assignment]  # bezici pokus o inicializaci


def _init_data():
    """Priprava datove vrstvy (blokujici I/O na share). Bezi v _init_worker."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    sm = StateManager(DATA_DIR)
    nm = NotesManager(DATA_DIR)
    plan_index.load()
    return sm, nm


def _init_worker():
    """Jeden pokus o inicializaci. Pri uspechu zverejni managery (posledni
    se nastavuje state_manager -- to je priznak, ze je vse pripravene)."""
    global state_manager, notes_manager, DATA_ERROR
    try:
        sm, nm = _init_data()
        notes_manager = nm
        DATA_ERROR = None
        state_manager = sm          # signal 'ready' az uplne nakonec
    except Exception as exc:  # noqa: BLE001
        DATA_ERROR = str(exc) or "Datova slozka neni dostupna."


def _ensure_data_ready():
    """Vraci None pokud je datova vrstva pripravena, jinak chybovou hlasku.

    Single-flight: naráz bezi max. jeden pokus o inicializaci. Zaseknute SMB
    volani nelze v Pythonu prerusit, takze dalsi pozadavky se nepokousi
    spustit nove vlakno -- vrati rovnou chybovou stranku, dokud bezici pokus
    neskonci. Po pripojeni VPN se pokus dokonci a dalsi obnoveni uz projde."""
    global _init_thread
    if state_manager is not None:
        return None

    started_here = False
    with _init_lock:
        if _init_thread is None or not _init_thread.is_alive():
            _init_thread = threading.Thread(target=_init_worker, daemon=True)
            _init_thread.start()
            started_here = True
        t = _init_thread

    # Jen ten, kdo pokus prave spustil, na nej chvili pocka (aby prvni nacteni
    # stranky rovnou ukazalo data). Ostatni pozadavky se vraci hned.
    if started_here:
        t.join(INIT_TIMEOUT_SECONDS)

    if state_manager is not None:
        return None
    return DATA_ERROR or "Datova slozka se nacita, zkuste to za chvili."


def _data_error_page(request: Request):
    return TEMPLATES.TemplateResponse(
        request, "data_error.html", {"detail": DATA_ERROR}, status_code=503
    )


def _api_guard():
    err = _ensure_data_ready()
    if err:
        raise HTTPException(status_code=503, detail=err)


# Pokus o inicializaci uz pri startu. Neuspech neni fatalni -- server nabehne
# a stranky zobrazi hlasku "pripojte VPN".
_ensure_data_ready()

# Simple regex to make material/filename safe for Windows paths
SAFE_PATH_RE = re.compile(r"[^A-Za-z0-9_-]+")

def get_aggregated_items() -> List[Dict]:
    """
    Scans all plans, finds the latest version for each SA/Material pair, 
    and aggregates all line items from those latest versions only.
    Items with ghosts are returned as a single object containing 'ghosts' list.
    """
    # 1. Collect and filter plans: (sa, mat) -> Best Plan.
    # Cteme z indexu v pameti (zadne skenovani sitove slozky).
    best_plans = {}

    for pentry in plan_index.plans():
        data = pentry["data"]
        ts = pentry["latest_ts"]

        sa = str(data.get("scheduling_agreement_no", "Unknown")).strip()
        mat = str(data.get("material_no", "Unknown")).strip()
        rel = data.get("release_nr", "0")

        # Key used for grouping
        key = (sa, mat)

        # Parse release number safely
        try:
            rel_val = int(str(rel).strip())
        except (ValueError, TypeError):
            rel_val = -1

        current_best = best_plans.get(key)
        is_better = False

        if not current_best:
            is_better = True
        elif rel_val > current_best['rel_val']:
            is_better = True
        elif rel_val == current_best['rel_val']:
            # Tie-breaker: timestamp (nazev ts je serazitelny chronologicky)
            if ts > current_best['ts']:
                is_better = True

        if is_better:
            best_plans[key] = {
                'plan_key': pentry["plan_key"],
                'ts': ts,
                'data': data,
                'rel_val': rel_val,
                'sa': sa,
                'mat': mat,
                'rel': rel  # original string
            }

    items = []
    today = datetime.now().date()

    # 2. Process only the best plans
    for entry in best_plans.values():
        data = entry['data']

        sa_no = entry['sa']
        mat_no = entry['mat']
        rel_nr = entry['rel']
        plan_id = entry['plan_key']
        ts = entry['ts']
        
        # --- GHOST ROWS LOGIC START ---
        # 1. Identify all (date, qty) present in the *current* plan
        current_plan_pairs = set()
        for line in data.get("lines", []):
                try:
                    qty_f = float(line.get("order_quantity", 0))
                except (ValueError, TypeError):
                    qty_f = 0.0
                current_plan_pairs.add((line.get("delivery_date"), qty_f))
        
        # Helper to create a ghost line
        def create_ghost_line(date_str, qty, plan_id_val, ts_val, sa_val, mat_val, rel_val):
            days_g = None
            urgency_g = "normal"
            try:
                dd_g = datetime.strptime(date_str, "%Y-%m-%d").date()
                days_g = (dd_g - today).days
            except (ValueError, TypeError): pass
            
            return {
                "plan_id": plan_id_val,
                "ts": ts_val,
                "sa_no": sa_val,
                "material_no": mat_val,
                "release_nr": rel_val,
                "delivery_date": date_str,
                "formatted_date": f"{date_str[8:10]}.{date_str[5:7]}.{date_str[0:4]}",
                "quantity": qty,
                "modification": None,
                "days_to_delivery": days_g,
                "urgency": urgency_g,
                "is_processed": True,
                "is_ghost": True # Flag for UI
            }

        processed_ghost_dates = set()
        # --- GHOST ROWS LOGIC END ---

        for line in data.get("lines", []):
            try:
                # logic for modification
                mod = line.get("modification")
                        
                # Calculate days/urgency
                days = None
                urgency = "normal"
                try:
                    dd = datetime.strptime(line["delivery_date"], "%Y-%m-%d").date()
                    days = (dd - today).days
                    
                    # Logic:
                    # 1. Critical: <= 2 days
                    # 2. Warning: <= 7 days
                    # 3. Urgent Mod: < 45 days AND modification != 0
                    
                    is_mod = (mod is not None and mod != 0)

                    if 0 <= days <= 2:
                        urgency = "critical"
                    elif 3 <= days <= 7:
                        urgency = "warning"
                    elif (0 <= days < 45) and is_mod:
                        urgency = "urgent_mod" # New state for <45 + mod
                        
                except (ValueError, TypeError): pass
                
                # Check processed status (for the REAL line)
                processed = state_manager.get_state(
                    sa_no, mat_no, 
                    line.get("delivery_date"), 
                    line.get("order_quantity")
                )

                # --- GHOST INJECTION ---
                d_date = line.get("delivery_date")
                ghosts = []
                has_ghost = False
                ghost_qty = None
                
                if d_date not in processed_ghost_dates:
                    processed_ghost_dates.add(d_date)
                    hist_qtys = state_manager.get_processed_versions(sa_no, mat_no, d_date)
                    
                    for hq in hist_qtys:
                            if (d_date, hq) not in current_plan_pairs:
                                # Create ghost item
                                g_item = create_ghost_line(d_date, hq, plan_id, ts, sa_no, mat_no, rel_nr)
                                ghosts.append(g_item)
                    
                    if ghosts:
                        has_ghost = True
                        ghost_qty = ghosts[-1]["quantity"]

                # Create the main item object
                item_obj = {
                    "plan_id": plan_id,
                    "ts": ts,
                    "sa_no": sa_no,
                    "material_no": mat_no,
                    "release_nr": rel_nr,
                    "delivery_date": line["delivery_date"],
                    "formatted_date": f"{line['delivery_date'][8:10]}.{line['delivery_date'][5:7]}.{line['delivery_date'][0:4]}",
                    "quantity": line["order_quantity"],
                    "modification": mod,
                    "days_to_delivery": days,
                    "urgency": urgency,
                    "is_processed": processed,
                    "is_ghost": False,
                    "has_ghost": has_ghost,
                    "ghost_qty": ghost_qty,
                    "ghosts": ghosts # Attach ghosts list here
                }
                items.append(item_obj)
            except Exception as e:
                print(f"Error processing line in plan {p_dir.name}: {e}")
                continue
            
    # Sort by days to delivery (ascending, None last)
    items.sort(key=lambda x: (x["days_to_delivery"] is None, x["days_to_delivery"]))
    return items

def get_safe_id(val: str) -> str:
    if not val:
        return "Unknown"
    return SAFE_PATH_RE.sub("_", val).strip("_") or "unknown"

def get_plan_dirs(plan_id: str):
    base = DATA_DIR / "plans" / plan_id
    dirs = {
        "base": base,
        "raw": base / "raw",
        "extracted": base / "extracted",
        "approved": base / "approved",
        "output": base / "output"
    }
    return dirs

def get_history(sa_no: str) -> List[Dict]:
    """Find all unique versions (different Release Nr) that share the same Scheduling Agreement.

    Kazdy plan_id ma jedno release_nr (je soucasti nazvu), takze staci vzit
    nejnovejsi verzi kazdeho odpovidajiciho planu z indexu -- bez skenovani."""
    history_map = {}
    safe_sa = get_safe_id(sa_no)
    prefix_old = f"SA_{safe_sa}_"
    prefix_new = f"{safe_sa}_AN_"
    for pentry in plan_index.plans():
        name = pentry["plan_key"]
        if not (name.startswith(prefix_old) or name.startswith(prefix_new)):
            continue
        data = pentry["data"]
        rn = data.get("release_nr", "Unknown")
        ts = pentry["latest_ts"]
        # Keep newest per release nr
        if rn not in history_map or ts > history_map[rn]["ts"]:
            history_map[rn] = {
                "plan_id": name,
                "ts": ts,
                "release_nr": rn,
                "uploaded_at": data.get("uploaded_at", "Unknown"),
                "material_no": data.get("material_no")
            }
    return sorted(history_map.values(), key=lambda x: x["ts"], reverse=True)

def format_cz_num(val: Any) -> str:
    if val is None: return ""
    try:
        fval = float(val)
        return f"{fval:,.0f}".replace(",", " ").replace(".", ",")
    except (ValueError, TypeError):
        return str(val)

def _load_json(path: Path) -> Dict:
    with path.open("r", encoding="utf-8") as h:
        return json.load(h)

# --- Notification System ---

DISMISSED_FILE = DATA_DIR / "dismissed_notifications.json"

class DismissRequest(BaseModel):
    notif_id: str

def load_dismissed():
    # Cteno z indexu v pameti (obnoveno pri startu / po zapisu).
    return plan_index.dismissed()

def save_dismissed(dismissed_list):
    plan_index.set_dismissed(dismissed_list)
    with open(DISMISSED_FILE, "w") as f:
        json.dump(dismissed_list, f)

def get_notifications(items=None):
    if items is None:
        items = get_aggregated_items()
    dismissed = set(load_dismissed())
    notifs = []
    
    # Trigger days: 59, 52, 48, 45
    TRIGGER_DAYS = {59, 52, 48, 45}
    
    for item in items:
        if not item.get("is_processed") and item.get("days_to_delivery") is not None:
            days = item["days_to_delivery"]
            modification = item.get("modification")
            
            # Condition 1: Approaching deadline (specific days)
            is_approach = days in TRIGGER_DAYS
            
            # Condition 2: Urgent Modification (< 45 days + has modification)
            # Check modification is not None and not 0
            is_urgent_mod = (days < 45) and (modification is not None) and (modification != 0)
            
            if is_approach or is_urgent_mod:
                # Unique ID: sa_no + date + days + type
                # Add differentiation for type so they don't clash?
                # Actually, if both happen, it's fine to show one notification or the same ID.
                # But if today is 45 days AND it has modification, usually the modification is the bigger deal?
                # Let's keep ID simple: sa_no + date + days. 
                # If modification changes, it might trigger new notif? 
                # If days change, it triggers new notif.
                # If mod exists, it should probably flag every day < 45? 
                # User said: "items are <45 days + ... aren't checked -> Flag"
                # If it flags every day, the user gets spammed with 45 notifs?
                # "When I uploaded a new file, the items weren't flagged"
                # Maybe just flag it once per file version (TS)? 
                # Let's start with flagging it. If ID is sa_no_date_days, it will trigger e.g. at 44, 43, 42... 
                # That might be annoying. But user said "flagged".
                # For now, stick to the requested logic.
                
                notif_id = f"{item['sa_no']}_{item['delivery_date']}_{days}"
                
                if notif_id not in dismissed:
                    msg = f"U lieferplánu {item['sa_no']} zbývá {days} dní do dodání"
                    if is_urgent_mod:
                         msg += " a došlo ke změně množství!"
                         
                    notifs.append({
                        "id": notif_id,
                        "sa_no": item["sa_no"],
                        "days": days,
                        "plan_key": item.get("plan_id"), # Used for link construction
                        "ts": item.get("ts"),
                        "text": msg,
                        "link": f"/plan/{item.get('plan_id')}/{item.get('ts')}"
                    })
    return notifs

@app.post("/api/dismiss-notification")
def dismiss_notification_endpoint(req: DismissRequest):
    _api_guard()
    dismissed = load_dismissed()
    if req.notif_id not in dismissed:
        dismissed.append(req.notif_id)
        save_dismissed(dismissed)
    return {"status": "ok"}

@app.get("/")
def index(request: Request):
    if _ensure_data_ready():
        return _data_error_page(request)

    plans = [
        {"plan_key": p["plan_key"], "uploaded_at": p.get("uploaded_at", "Unknown")}
        for p in plan_index.plans()
    ]
    # Sort plans by uploaded_at descending
    plans = sorted(plans, key=lambda x: x["uploaded_at"], reverse=True)

    notifications = get_notifications()

    # Get inventory uploaded_at (z indexu v pameti)
    inv_data = plan_index.inventory()
    inventory_uploaded_at = inv_data.get("uploaded_at") if inv_data else None

    return TEMPLATES.TemplateResponse(request, "index.html", {
        "plans": plans,
        "notifications": notifications,
        "inventory_uploaded_at": inventory_uploaded_at
    })

@app.get("/overview")
def overview(request: Request):
    if _ensure_data_ready():
        return _data_error_page(request)

    items = get_aggregated_items()
    # get_aggregated_items uz radky serazuje podle days_to_delivery vzestupne.
    notifications = get_notifications(items)  # znovu nepocitat agregaci

    return TEMPLATES.TemplateResponse(request, "overview.html", {
        "items": items,
        "format_num": format_cz_num,
        "notifications": notifications
    })

@app.post("/upload")
def upload_pdf(request: Request, file: UploadFile = File(...)):
    _api_guard()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    temp_pdf = BASE_DIR / f"tmp_{ts}.pdf"

    with temp_pdf.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        payload = extract_lieferplan(temp_pdf)
        sa = payload.get("scheduling_agreement_no") or "Unknown"
        rn = payload.get("release_nr") or "Unknown"

        plan_id = f"{get_safe_id(sa)}_AN_{get_safe_id(rn)}"

        # --- Duplicate detection (z indexu v pameti, bez skenovani disku) ---
        dirs = get_plan_dirs(plan_id)
        plan_already_exists = plan_index.get(plan_id) is not None
        if plan_already_exists:
            # Save temp PDF under a staging name so confirm-overwrite can reuse it
            staging_pdf = BASE_DIR / f"staging_{ts}.pdf"
            shutil.move(temp_pdf, staging_pdf)
            from fastapi.responses import JSONResponse
            return JSONResponse({
                "exists": True,
                "plan_id": plan_id,
                "staging": str(staging_pdf),
                "redirect": f"/plan/{plan_id}/latest"
            })
        # --- End duplicate detection ---
        
        # Add timestamp metadata
        now_dt = datetime.now(timezone.utc)
        payload["uploaded_at"] = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        payload["ts_key"] = ts
        
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)

        shutil.move(temp_pdf, dirs["raw"] / f"{ts}.pdf")
        extracted_file = dirs["extracted"] / f"{ts}.json"

        with extracted_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        plan_index.upsert_plan(plan_id, payload, ts)

        from fastapi.responses import JSONResponse
        return JSONResponse({"exists": False, "redirect": f"/plan/{plan_id}/{ts}"})
    except Exception as e:
        if temp_pdf.exists(): temp_pdf.unlink()
        raise HTTPException(status_code=500, detail=str(e))


class OverwriteRequest(BaseModel):
    staging: str
    plan_id: str

@app.post("/upload/confirm-overwrite")
def confirm_overwrite(req: OverwriteRequest):
    """Called when user confirms they want to overwrite an existing plan."""
    _api_guard()
    # Security: resolve and validate staging path is within BASE_DIR
    # and matches the server-generated staging_<ts>.pdf pattern.
    try:
        staging_pdf = BASE_DIR.joinpath(Path(req.staging).name).resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid staging path")
    if staging_pdf.parent != BASE_DIR.resolve():
        raise HTTPException(status_code=400, detail="Invalid staging path")
    if not re.fullmatch(r"staging_\d{8}T\d{6}\.pdf", staging_pdf.name):
        raise HTTPException(status_code=400, detail="Invalid staging file name")
    if not staging_pdf.exists():
        raise HTTPException(status_code=404, detail="Staging file not found")

    # Security: sanitize plan_id the same way upload does to prevent path traversal
    safe_plan_id = get_safe_id(req.plan_id)

    try:
        payload = extract_lieferplan(staging_pdf)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        now_dt = datetime.now(timezone.utc)
        payload["uploaded_at"] = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        payload["ts_key"] = ts

        dirs = get_plan_dirs(safe_plan_id)
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)

        shutil.move(staging_pdf, dirs["raw"] / f"{ts}.pdf")
        extracted_file = dirs["extracted"] / f"{ts}.json"
        with extracted_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        plan_index.upsert_plan(safe_plan_id, payload, ts)

        from fastapi.responses import JSONResponse
        return JSONResponse({"redirect": f"/plan/{safe_plan_id}/{ts}"})
    except Exception as e:
        if staging_pdf.exists(): staging_pdf.unlink()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/upload-inventory")
def upload_inventory(file: UploadFile = File(...)):
    _api_guard()
    if not file.filename.endswith('.xlsx'):
        raise HTTPException(status_code=400, detail="Soubor musí být ve formátu .xlsx")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    temp_xlsx = BASE_DIR / f"tmp_inv_{ts}.xlsx"
    
    with temp_xlsx.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        inventory_data = parse_inventory_xlsx(temp_xlsx)
        
        # Save to inventory.json
        now_dt = datetime.now(timezone.utc).astimezone()
        payload = {
            "uploaded_at": now_dt.strftime("%d.%m.%Y %H:%M"),
            "items": inventory_data
        }
        
        inventory_file = DATA_DIR / "inventory.json"
        with inventory_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        plan_index.set_inventory(payload)

        # Cleanup temp file
        if temp_xlsx.exists():
            temp_xlsx.unlink()
            
        return RedirectResponse(url="/", status_code=303)
    except Exception as e:
        if temp_xlsx.exists():
            temp_xlsx.unlink()
        raise HTTPException(status_code=500, detail=f"Chyba při zpracování inventury: {str(e)}")

@app.get("/plan/{plan_id}/latest")
def latest_plan(request: Request, plan_id: str):
    if _ensure_data_ready():
        return _data_error_page(request)
    entry = plan_index.get(plan_id)
    if not entry:
        raise HTTPException(status_code=404, detail="No extractions found for this plan.")
    return view_plan(request, plan_id, entry["latest_ts"])

@app.get("/plan/{plan_id}/download")
def download(plan_id: str):
    _api_guard()
    entry = plan_index.get(plan_id)
    if not entry:
        raise HTTPException(status_code=404, detail="No extracted data found for this plan.")

    ts = entry["latest_ts"]
    raw_pdf_path = get_plan_dirs(plan_id)["raw"] / f"{ts}.pdf"

    if not raw_pdf_path.exists():
        raise HTTPException(status_code=404, detail="Original PDF not found for this plan.")

    return FileResponse(str(raw_pdf_path), filename=f"{plan_id}.pdf", media_type="application/pdf")

@app.get("/plan/{plan_id}/{ts}")
def view_plan(request: Request, plan_id: str, ts: str):
    if _ensure_data_ready():
        return _data_error_page(request)

    # Nejcastejsi pripad (nejnovejsi verze) obslouzime z indexu v pameti bez
    # diskoveho I/O; starsi verzi nacteme primo ze souboru (jedno cteni).
    entry = plan_index.get(plan_id)
    if entry and entry["latest_ts"] == ts:
        payload = copy.deepcopy(entry["data"])  # kopie, at nemutujeme cache
    else:
        target = get_plan_dirs(plan_id)["extracted"] / f"{ts}.json"
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"Version {ts} not found")
        payload = _load_json(target)
    
    # Calculate lead times and status flags for UI
    today = datetime.now().date()
    # Inject state from StateManager
    sa_no = payload.get("scheduling_agreement_no", "")
    mat_no = payload.get("material_no", "")
    
    for line in payload.get("lines", []):
        try:
            dd = datetime.strptime(line["delivery_date"], "%Y-%m-%d").date()
            days = (dd - today).days
            line["days_to_delivery"] = days
            
            # Logic flags for cleaner templates
            line["is_past"] = days < 0
            
            # Reconstruct urgency string for plan.html
            # In plan.html, we use 'is_urgent' boolean primarily for 'urgent-row' class (RED).
            # We want < 45 days + mod to be RED.
            # So is_urgent = True if < 45 + mod.
            # But what about <=2 days? Should that also be urgent?
            # User wants "red - <45 days + a modification".
            # The dashboard has different categories (critical, warning).
            
            # Let's align view_plan logic to just use 'urgency' string like dashboard if possible, 
            # OR map dashboard states to is_urgent.
            
            # For now, stick to the 'is_urgent' requested logic:
            line["is_urgent"] = (0 <= days < 45) and (line.get("modification") not in (None, 0))
            
            # Also calculate specificity for future use if we want to unify templates further
            urgency = "normal"
            if 0 <= days <= 2: urgency = "critical"
            elif 3 <= days <= 7: urgency = "warning"
            elif line["is_urgent"]: urgency = "urgent_mod"
            
            line["urgency"] = urgency
        except (ValueError, TypeError, KeyError):
            line["days_to_delivery"] = None
            line["is_past"] = False
            line["is_urgent"] = False
            
        # Add persistence check
        line["is_processed"] = state_manager.get_state(
            sa_no, mat_no, 
            line.get("delivery_date"), 
            line.get("order_quantity")
        )
    
    # --- GHOST ROWS LOGIC ---
    # We want to show "old" processed rows if the quantity changed.
    # 1. Identify all (date, qty) present in the current plan
    current_plan_pairs = set()
    for line in payload.get("lines", []):
         current_plan_pairs.add((line.get("delivery_date"), float(line.get("order_quantity"))))
         
    final_lines = []
    
    # We assume payload["lines"] is sorted by date.
    # We will process each line, and before adding it, check if there are ghost rows for this date.
    # To avoid adding ghost rows multiple times (if multiple lines have same date), we track processed dates.
    processed_ghost_dates = set()
    
    # Helper to create a ghost line
    def create_ghost_line(date_str, qty):
        # Parse date to get days_to_delivery
        days = None
        is_past = False
        try:
            dd = datetime.strptime(date_str, "%Y-%m-%d").date()
            days = (dd - today).days
            is_past = days < 0
        except (ValueError, TypeError): pass
        
        return {
            "delivery_date": date_str,
            "order_quantity": qty,
            "modification": None, # or some indicator?
            "days_to_delivery": days,
            "is_past": is_past,
            "is_urgent": False,
            "is_processed": True,
            "is_ghost": True # Flag for UI
        }

    for line in payload.get("lines", []):
        d_date = line.get("delivery_date")
        
        # If we haven't checked for ghost rows for this date yet
        if d_date not in processed_ghost_dates:
            processed_ghost_dates.add(d_date)
            
            # Get all processed versions from history
            try:
                hist_qtys = state_manager.get_processed_versions(sa_no, mat_no, d_date)
            except (json.JSONDecodeError, KeyError, OSError):
                hist_qtys = []
            
            # Find ghost rows: processed versions NOT in current plan
            ghosts = []
            for hq in hist_qtys:
                 if (d_date, hq) not in current_plan_pairs:
                     ghosts.append(create_ghost_line(d_date, hq))
            
            if ghosts:
                line["has_ghost"] = True
                line["ghosts"] = ghosts
                line["ghost_qty"] = ghosts[-1]["order_quantity"]
            else:
                 line["has_ghost"] = False
                 line["ghosts"] = []
        
        final_lines.append(line)
        
    # Assign display indices (Simple 1..N for real lines)
    for i, line in enumerate(final_lines):
        line["display_index"] = i + 1

    payload["lines"] = final_lines
    # ------------------------
    
    # Get history for changelog
    sa_no = payload.get("scheduling_agreement_no", "")
    history = get_history(sa_no)

    # Load Inventory Data (z indexu v pameti, bez diskoveho I/O)
    nadvyroba_qty = None
    inventory_uploaded_at = None
    inv_data = plan_index.inventory()
    if inv_data:
        try:
            inventory_uploaded_at = inv_data.get("uploaded_at")
            items = inv_data.get("items", {})

            # Match material_no. Lieferplan has trailing " /" which we must clear.
            # Also remove ALL spaces from both sides to handle inconsistent spacing (e.g. French plans)
            def clean_code(code: str) -> str:
                c = str(code).strip()
                if c.endswith('/'):
                    c = c[:-1]
                c = c.replace(" ", "")
                # For Mercedes-style codes (A + 10 digits), ignore varying color/variant suffixes
                if c.startswith('A') and len(c) >= 11:
                    return c[:11]
                return c

            plan_mat_clean = clean_code(mat_no)

            # Search through the inventory keys, cleaning them the same way
            for inv_key, qty in items.items():
                if plan_mat_clean == clean_code(inv_key):
                    nadvyroba_qty = qty
                    break
        except Exception as e:
            print(f"Error loading inventory: {e}")

    return TEMPLATES.TemplateResponse(request, "plan.html", {
        "payload": payload,
        "plan_key": plan_id,
        "timestamp": ts,
        "history": history,
        "format_num": format_cz_num,
        "today": datetime.now().date(),
        "nadvyroba_qty": nadvyroba_qty,
        "inventory_uploaded_at": inventory_uploaded_at
    })

class ToggleRowRequest(BaseModel):
    sa_no: str
    material: str
    date: str
    quantity: float
    state: bool

@app.post("/api/toggle-row")
def toggle_row(req: ToggleRowRequest):
    _api_guard()
    try:
        state_manager.set_state(req.sa_no, req.material, req.date, req.quantity, req.state)
        return {"status": "ok", "new_state": req.state}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class NoteCreateRequest(BaseModel):
    text: str
    user: str = "Uživatel"

class NoteUpdateRequest(BaseModel):
    text: str

@app.get("/api/notes/{sa_no}")
def get_notes(sa_no: str):
    _api_guard()
    return {"notes": notes_manager.get_notes(sa_no)}

@app.post("/api/notes/{sa_no}")
def add_note(sa_no: str, req: NoteCreateRequest):
    _api_guard()
    note = notes_manager.add_note(sa_no, req.user, req.text)
    return note

@app.put("/api/notes/{sa_no}/{note_id}")
def update_note(sa_no: str, note_id: str, req: NoteUpdateRequest):
    _api_guard()
    note = notes_manager.update_note(sa_no, note_id, req.text)
    if not note:
        raise HTTPException(status_code=404, detail="Poznámka nebyla nalezena")
    return note

@app.delete("/api/notes/{sa_no}/{note_id}")
def delete_note(sa_no: str, note_id: str):
    _api_guard()
    success = notes_manager.delete_note(sa_no, note_id)
    if not success:
        raise HTTPException(status_code=404, detail="Poznámka nebyla nalezena")
    return {"status": "ok"}

@app.post("/api/rebuild-index")
def rebuild_index():
    """Znovu projde datovou slozku a prestavi index. Pouzit, pokud nekdo
    zasahl do sdilene slozky mimo aplikaci (rucne pridal/smazal plany)."""
    _api_guard()
    try:
        plan_index.rebuild()
        return {"status": "ok", "plans": len(plan_index.plans())}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
