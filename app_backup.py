import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from extract_lieferplan import extract_lieferplan
from generate_plan_xlsx import generate_xlsx

app = FastAPI()
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))

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


def list_recent_plans() -> List[Dict[str, str]]:
    plans_dir = DATA_DIR / "plans"
    if not plans_dir.exists():
        return []
    plan_entries = []
    for plan_dir in plans_dir.iterdir():
        if plan_dir.is_dir():
            plan_entries.append(
                {
                    "plan_key": plan_dir.name,
                    "modified": plan_dir.stat().st_mtime,
                }
            )
    return sorted(plan_entries, key=lambda item: item["modified"], reverse=True)


def _load_json(path: Path) -> Dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _latest_extracted(plan_dir: Path) -> Optional[Path]:
    extracted_dir = plan_dir / "extracted"
    if not extracted_dir.exists():
        return None
    candidates = sorted(extracted_dir.glob("*.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _next_version(approved_dir: Path) -> str:
    existing = sorted(approved_dir.glob("v*.json"))
    max_version = 0
    for path in existing:
        match = re.match(r"v(\d+)\.json", path.name)
        if match:
            max_version = max(max_version, int(match.group(1)))
    return f"v{max_version + 1:04d}.json"


def _plan_dirs(plan_key: str) -> Dict[str, Path]:
    plan_dir = DATA_DIR / "plans" / plan_key
    return {
        "plan_dir": plan_dir,
        "raw": plan_dir / "raw",
        "extracted": plan_dir / "extracted",
        "approved": plan_dir / "approved",
        "output": plan_dir / "output",
    }


def _render_plan(
    request: Request,
    payload: Dict,
    plan_key: str,
    timestamp: str,
    approved_at: Optional[str] = None,
    message: Optional[str] = None,
):
    return TEMPLATES.TemplateResponse(
        "plan.html",
        {
            "request": request,
            "plan_key": plan_key,
            "timestamp": timestamp,
            "payload": payload,
            "approved_at": approved_at,
            "message": message,
        },
    )


@app.get("/")
def index(request: Request):
    plans = list_recent_plans()
    return TEMPLATES.TemplateResponse(
        "index.html",
        {
            "request": request,
            "plans": plans,
        },
    )


@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=400, detail="Invalid content type.")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    temp_pdf = BASE_DIR / f"tmp_{timestamp}.pdf"

    with temp_pdf.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        payload = extract_lieferplan(temp_pdf)
        print(f"DEBUG: Extracted payload for {file.filename}: {json.dumps(payload, indent=2)}")
    except Exception as exc:  # noqa: BLE001
        temp_pdf.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Extraction failed: {exc}")

    plan_key = compute_plan_key(payload)
    dirs = _plan_dirs(plan_key)
    for directory in dirs.values():
        directory.mkdir(parents=True, exist_ok=True)

    raw_path = dirs["raw"] / f"{timestamp}.pdf"
    extracted_path = dirs["extracted"] / f"{timestamp}.json"

    shutil.move(temp_pdf, raw_path)
    with extracted_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    return RedirectResponse(url=f"/plan/{plan_key}/{timestamp}", status_code=303)


@app.get("/plan/{plan_key}/{timestamp}")
def plan_detail(request: Request, plan_key: str, timestamp: str):
    dirs = _plan_dirs(plan_key)
    extracted_path = dirs["extracted"] / f"{timestamp}.json"
    if not extracted_path.exists():
        raise HTTPException(status_code=404, detail="Plan or timestamp not found.")

    payload = _load_json(extracted_path)
    approved_path = dirs["approved"] / "current.json"
    approved_at = None
    if approved_path.exists():
        approved_payload = _load_json(approved_path)
        approved_at = approved_payload.get("approved_at")

    return _render_plan(request, payload, plan_key, timestamp, approved_at=approved_at)


@app.post("/plan/{plan_key}/{timestamp}/approve")
def approve_plan(plan_key: str, timestamp: str):
    dirs = _plan_dirs(plan_key)
    extracted_path = dirs["extracted"] / f"{timestamp}.json"
    
    # Fallback to latest if specific timestamp is missing (e.g. after key change)
    if not extracted_path.exists():
        latest = _latest_extracted(dirs["plan_dir"])
        if latest:
            extracted_path = latest
            print(f"DEBUG: Specified timestamp {timestamp} not found, falling back to latest: {latest.name}")
        else:
            raise HTTPException(status_code=404, detail="Plan extraction file not found.")

    payload = _load_json(extracted_path)
    approved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    payload["approved_at"] = approved_at

    current_path = dirs["approved"] / "current.json"
    dirs["approved"].mkdir(parents=True, exist_ok=True)
    version_path = dirs["approved"] / _next_version(dirs["approved"])

    with current_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    with version_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    output_path = dirs["output"] / "Plan.xlsx"
    dirs["output"].mkdir(parents=True, exist_ok=True)
    generate_xlsx(payload, output_path)

    return RedirectResponse(url=f"/plan/{plan_key}/latest", status_code=303)


@app.get("/plan/{plan_key}/latest")
def latest_plan(request: Request, plan_key: str):
    dirs = _plan_dirs(plan_key)
    latest_path = _latest_extracted(dirs["plan_dir"])
    if latest_path is None:
        raise HTTPException(status_code=404, detail="No extracted plans found.")
    timestamp = latest_path.stem
    payload = _load_json(latest_path)
    approved_path = dirs["approved"] / "current.json"
    approved_at = None
    if approved_path.exists():
        approved_payload = _load_json(approved_path)
        approved_at = approved_payload.get("approved_at")

    return _render_plan(request, payload, plan_key, timestamp, approved_at=approved_at)


@app.get("/plan/{plan_key}/download")
def download_plan(plan_key: str):
    output_path = DATA_DIR / "plans" / plan_key / "output" / "Plan.xlsx"
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="Plan output not found.")
    return FileResponse(output_path, filename="Plan.xlsx")
