"""Saleae Capture — Web API & Routes."""

import json
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from collab.database import get_db
from collab.auth import get_current_user_from_cookie, require_user
from collab.models import Researcher

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

CAPTURES_DIR = Path(__file__).parent.parent.parent / "captures"
CAPTURES_DIR.mkdir(exist_ok=True)


def _user_ctx(request: Request, db: Session):
  user = get_current_user_from_cookie(request, db)
  return {"request": request, "user": user}


def _get_capture_interface(use_simulator: bool = False):
  """Get capture interface (real or simulator)."""
  if use_simulator:
    from collab.saleae.capture import SaleaeSimulator
    return SaleaeSimulator()
  else:
    from collab.saleae.capture import SaleaeCapture
    cap = SaleaeCapture()
    cap.connect()
    return cap


def _list_captures() -> list[dict]:
  """List all saved captures."""
  captures = []
  for f in sorted(CAPTURES_DIR.glob("*.npz"), reverse=True):
    try:
      import numpy as np
      data = np.load(f, allow_pickle=True)
      info = json.loads(str(data.get("capture_info", "{}")))
      raw = data.get("raw_data")
      captures.append({
        "filename": f.name,
        "path": str(f),
        "size_kb": f.stat().st_size / 1024,
        "channels": raw.shape[0] if raw is not None else 0,
        "samples": raw.shape[1] if raw is not None and raw.ndim > 1 else 0,
        "sample_rate": info.get("sample_rate", 0),
        "duration": info.get("duration", 0),
        "captured_at": info.get("captured_at", ""),
        "device": info.get("device", info.get("source", "")),
        "label": info.get("label", ""),
        "description": info.get("description", ""),
      })
    except Exception:
      captures.append({"filename": f.name, "path": str(f), "error": True})
  return captures


# ── Web Routes ──────────────────────────────────────────────────────────────

@router.get("/saleae", response_class=HTMLResponse)
def saleae_page(request: Request, db: Session = Depends(get_db)):
  ctx = _user_ctx(request, db)
  ctx["captures"] = _list_captures()

  # Check device connectivity
  device_status = "disconnected"
  devices = []
  sample_rates = []
  try:
    from collab.saleae.capture import SaleaeCapture, SALEAE_AVAILABLE
    if SALEAE_AVAILABLE:
      cap = SaleaeCapture()
      cap.connect()
      devices = cap.get_devices()
      sample_rates = cap.get_sample_rates()[:10]  # Top 10
      device_status = "connected"
  except Exception as e:
    device_status = f"error: {e}"

  ctx.update({
    "device_status": device_status,
    "devices": devices,
    "sample_rates": sample_rates,
  })
  return templates.TemplateResponse("saleae.html", ctx)


@router.post("/saleae/capture")
def start_capture(
  request: Request,
  channels: str = Form("0,1,2,3"),
  sample_rate: int = Form(50000),
  duration: float = Form(10.0),
  label: str = Form(""),
  description: str = Form(""),
  use_simulator: bool = Form(False),
  user: Researcher = Depends(require_user),
  db: Session = Depends(get_db),
):
  """Start a new capture."""
  ch_list = [int(c.strip()) for c in channels.split(",") if c.strip().isdigit()]
  if not ch_list:
    ch_list = [0, 1, 2, 3]

  try:
    cap = _get_capture_interface(use_simulator=use_simulator)
    cap.configure(
      analog_channels=ch_list,
      sample_rate=sample_rate,
      duration=duration,
      label=label,
      description=description,
    )
    result = cap.capture()

    # Save
    ts = time.strftime("%Y%m%d_%H%M%S")
    safe_label = label.replace(" ", "_").replace("/", "-")[:30] if label else "capture"
    filename = f"{ts}_{safe_label}.npz"
    save_path = str(CAPTURES_DIR / filename)
    cap.save_npz(save_path)

    # Also save CSV
    csv_path = save_path.replace(".npz", ".csv")
    cap.save_csv(csv_path)

  except Exception as e:
    raise HTTPException(500, f"캡처 실패: {e}")

  return RedirectResponse("/saleae", status_code=303)


@router.get("/saleae/captures/{filename}")
def download_capture(filename: str):
  """Download a capture file."""
  file_path = CAPTURES_DIR / filename
  if not file_path.exists():
    raise HTTPException(404, "File not found")
  return FileResponse(file_path, filename=filename)


@router.get("/saleae/view/{filename}", response_class=HTMLResponse)
def view_capture(filename: str, request: Request, db: Session = Depends(get_db)):
  """View a capture with chart visualization."""
  import numpy as np

  ctx = _user_ctx(request, db)
  file_path = CAPTURES_DIR / filename
  if not file_path.exists():
    raise HTTPException(404, "File not found")

  data = np.load(file_path, allow_pickle=True)
  info = json.loads(str(data.get("capture_info", "{}")))
  raw = data.get("raw_data")
  time_arr = data.get("time")
  ch_indices = data.get("channel_indices")

  # Downsample for chart (max 2000 points per channel)
  max_points = 2000
  chart_data = {}
  if raw is not None:
    n_ch, n_samples = raw.shape
    step = max(1, n_samples // max_points)
    for i in range(n_ch):
      ch_name = f"CH{ch_indices[i]}" if ch_indices is not None else f"CH{i}"
      t_down = time_arr[::step].tolist() if time_arr is not None else list(range(0, n_samples, step))
      v_down = raw[i, ::step].tolist()
      chart_data[ch_name] = [{"x": t, "y": v} for t, v in zip(t_down, v_down)]

  ctx.update({
    "filename": filename,
    "info": info,
    "n_channels": raw.shape[0] if raw is not None else 0,
    "n_samples": raw.shape[1] if raw is not None and raw.ndim > 1 else 0,
    "chart_data_json": json.dumps(chart_data),
  })
  return templates.TemplateResponse("saleae_view.html", ctx)


@router.post("/saleae/delete/{filename}")
def delete_capture(
  filename: str,
  user: Researcher = Depends(require_user),
  db: Session = Depends(get_db),
):
  for ext in [".npz", ".csv"]:
    p = CAPTURES_DIR / filename.replace(".npz", ext).replace(".csv", ext)
    if p.exists():
      p.unlink()
  return RedirectResponse("/saleae", status_code=303)


# ── JSON API ────────────────────────────────────────────────────────────────

class CaptureRequest(BaseModel):
  analog_channels: list[int] = [0, 1, 2, 3]
  sample_rate: int = 50000
  duration: float = 10.0
  label: str = ""
  description: str = ""
  use_simulator: bool = False


@router.post("/api/saleae/capture")
def api_capture(
  data: CaptureRequest,
  user: Researcher = Depends(require_user),
  db: Session = Depends(get_db),
):
  """API endpoint for programmatic capture."""
  cap = _get_capture_interface(use_simulator=data.use_simulator)
  cap.configure(
    analog_channels=data.analog_channels,
    sample_rate=data.sample_rate,
    duration=data.duration,
    label=data.label,
    description=data.description,
  )
  result = cap.capture()

  ts = time.strftime("%Y%m%d_%H%M%S")
  safe_label = data.label.replace(" ", "_")[:30] if data.label else "capture"
  filename = f"{ts}_{safe_label}.npz"
  cap.save_npz(str(CAPTURES_DIR / filename))
  cap.save_csv(str(CAPTURES_DIR / filename.replace(".npz", ".csv")))

  ch_stats = {}
  for ch, arr in result.channels.items():
    ch_stats[f"ch{ch}"] = {
      "min": float(arr.min()),
      "max": float(arr.max()),
      "mean": float(arr.mean()),
      "std": float(arr.std()),
    }

  return {
    "filename": filename,
    "channels": len(result.channels),
    "samples": len(next(iter(result.channels.values()))),
    "sample_rate": result.actual_sample_rate,
    "duration": result.actual_duration,
    "stats": ch_stats,
  }


@router.get("/api/saleae/captures")
def api_list_captures():
  return _list_captures()


@router.get("/api/saleae/devices")
def api_devices():
  try:
    from collab.saleae.capture import SaleaeCapture
    cap = SaleaeCapture()
    cap.connect()
    return {
      "status": "connected",
      "devices": cap.get_devices(),
      "sample_rates": cap.get_sample_rates()[:20],
    }
  except Exception as e:
    return {"status": "error", "error": str(e)}
