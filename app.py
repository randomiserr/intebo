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
from generate_plan_xlsx import generate_xlsx

app = FastAPI()
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "templates")), name="static")

# Simple regex to make material/filename safe for Windows paths
SAFE_PATH_RE = re.compile(r"[^A-Za-z0-9_-]+")

def get_safe_id(val: str) -> str:
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
    """Find all unique versions (different Release Nr) that share the same Scheduling Agreement."""
    history_map = {}
    plans_dir = DATA_DIR / "plans"
    if not plans_dir.exists():
        return []
    
    for p_dir in plans_dir.iterdir():
        if p_dir.is_dir() and p_dir.name.startswith(f"SA_{sa_no}_"):
            ext_dir = p_dir / "extracted"
            if ext_dir.exists():
                for f in ext_dir.glob("*.json"):
                    try:
                        data = _load_json(f)
                        rn = data.get("release_nr", "Unknown")
                        ts = f.stem
                        # Keep newest per release nr
                        if rn not in history_map or ts > history_map[rn]["ts"]:
                            history_map[rn] = {
                                "plan_id": p_dir.name,
                                "ts": ts,
                                "release_nr": rn,
                                "uploaded_at": data.get("uploaded_at", "Unknown"),
                                "material_no": data.get("material_no")
                            }
                    except:
                        continue
    return sorted(history_map.values(), key=lambda x: x["ts"], reverse=True)

def format_cz_num(val: Any) -> str:
    if val is None: return ""
    try:
        fval = float(val)
        return f"{fval:,.0f}".replace(",", " ").replace(".", ",")
    except:
        return str(val)

def _load_json(path: Path) -> Dict:
    with path.open("r", encoding="utf-8") as h:
        return json.load(h)

@app.get("/")
def index(request: Request):
    plans_dir = DATA_DIR / "plans"
    plans = []
    if plans_dir.exists():
        for p_dir in plans_dir.iterdir():
            if p_dir.is_dir():
                ext_dir = p_dir / "extracted"
                uploaded_at = "Unknown"
                if ext_dir.exists():
                    files = sorted(ext_dir.glob("*.json"))
                    if files:
                        try:
                            data = _load_json(files[-1])
                            uploaded_at = data.get("uploaded_at", "Unknown")
                        except: pass
                plans.append({
                    "plan_key": p_dir.name,
                    "uploaded_at": uploaded_at
                })
    # Sort plans by uploaded_at descending
    plans = sorted(plans, key=lambda x: x["uploaded_at"], reverse=True)
    return TEMPLATES.TemplateResponse("index.html", {"request": request, "plans": plans})

@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    temp_pdf = BASE_DIR / f"tmp_{ts}.pdf"
    
    with temp_pdf.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        payload = extract_lieferplan(temp_pdf)
        sa = payload.get("scheduling_agreement_no", "Unknown")
        rn = payload.get("release_nr", "Unknown")
        
        plan_id = f"SA_{get_safe_id(sa)}_AN_{get_safe_id(rn)}"
        
        # Add timestamp metadata
        now_dt = datetime.now(timezone.utc)
        payload["uploaded_at"] = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        payload["ts_key"] = ts
        
        dirs = get_plan_dirs(plan_id)
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)

        # Move raw PDF and save extracted JSON
        shutil.move(temp_pdf, dirs["raw"] / f"{ts}.pdf")
        extracted_file = dirs["extracted"] / f"{ts}.json"
        
        with extracted_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        return RedirectResponse(url=f"/plan/{plan_id}/{ts}", status_code=303)
    except Exception as e:
        if temp_pdf.exists(): temp_pdf.unlink()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/plan/{plan_id}/latest")
def latest_plan(request: Request, plan_id: str, success: bool = False):
    dirs = get_plan_dirs(plan_id)
    files = sorted(dirs["extracted"].glob("*.json"))
    if not files:
        raise HTTPException(status_code=404, detail="No extractions found for this plan.")
    return view_plan(request, plan_id, files[-1].stem, success=success)

@app.get("/plan/{plan_id}/download")
def download(plan_id: str):
    f = get_plan_dirs(plan_id)["output"] / "Plan.xlsx"
    if not f.exists():
        raise HTTPException(status_code=404, detail="Plan.xlsx not found. Did you approve the plan?")
    return FileResponse(f, filename=f"{plan_id}.xlsx")

@app.get("/plan/{plan_id}/{ts}")
def view_plan(request: Request, plan_id: str, ts: str, success: bool = False):
    dirs = get_plan_dirs(plan_id)
    target = dirs["extracted"] / f"{ts}.json"
    
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Version {ts} not found")

    payload = _load_json(target)
    approved_path = dirs["approved"] / "current.json"
    approved_at = _load_json(approved_path).get("approved_at") if approved_path.exists() else None
    
    # Calculate lead times and status flags for UI
    today = datetime.now().date()
    for line in payload.get("lines", []):
        try:
            dd = datetime.strptime(line["delivery_date"], "%Y-%m-%d").date()
            days = (dd - today).days
            line["days_to_delivery"] = days
            
            # Logic flags for cleaner templates
            line["is_past"] = days < 0
            line["is_urgent"] = (0 <= days < 45) and (line.get("modification") not in (None, 0))
        except:
            line["days_to_delivery"] = None
            line["is_past"] = False
            line["is_urgent"] = False
    
    # Get history for changelog
    sa_no = payload.get("scheduling_agreement_no", "")
    history = get_history(sa_no)

    return TEMPLATES.TemplateResponse("plan.html", {
        "request": request,
        "payload": payload,
        "plan_key": plan_id,
        "timestamp": ts,
        "approved_at": approved_at,
        "success": success,
        "history": history,
        "format_num": format_cz_num,
        "today": datetime.now().date()
    })

@app.post("/plan/{plan_id}/{ts}/approve")
def approve_plan(plan_id: str, ts: str):
    import traceback
    try:
        dirs = get_plan_dirs(plan_id)
        source = dirs["extracted"] / f"{ts}.json"
        
        if not source.exists():
            files = sorted(dirs["extracted"].glob("*.json"))
            if not files: raise HTTPException(status_code=404)
            source = files[-1]

        payload = _load_json(source)
        payload["approved_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Save to approved
        dirs["approved"].mkdir(parents=True, exist_ok=True)
        with (dirs["approved"] / "current.json").open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        
        # Generate XLSX with history as changelog
        sa_no = payload.get("scheduling_agreement_no", "")
        history = get_history(sa_no)
        
        dirs["output"].mkdir(parents=True, exist_ok=True)
        generate_xlsx(payload, dirs["output"] / "Plan.xlsx", history=history)
        
        return RedirectResponse(url=f"/plan/{plan_id}/latest?success=true", status_code=303)
    except Exception as e:
        print(f"ERROR in approve_plan: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Chyba při exportu: {str(e)}")

