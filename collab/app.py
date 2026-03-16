"""AutoResearch Collab — Collaborative Research Platform.

Run: uvicorn platform.app:app --reload --port 8000
"""

import json
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc, func

from collab.database import get_db, init_db, engine, Base, SessionLocal
from collab.models import Researcher, Experiment, ResearchThread, ExperimentComment
from collab.auth import (
  hash_password, verify_password, create_token,
  get_current_user_from_cookie, require_user,
)
from collab.api.experiments import router as experiments_router
from collab.api.threads import router as threads_router
from collab.api.research_config import (
  router as research_config_router,
  _load_config as load_research_config,
  _save_config as save_research_config,
  build_program_prompt,
)
from collab.api.config_sync import (
  router as config_sync_router,
  _load_linked_projects,
  _save_linked_projects,
)
from collab.web.forks import router as forks_router
from collab.web.profile import router as profile_router
from collab.auth import _LoginRequired

app = FastAPI(title="AutoResearch Collab", version="1.0.0")

# Static files and templates
PLATFORM_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=PLATFORM_DIR / "static"), name="static")
templates = Jinja2Templates(directory=PLATFORM_DIR / "templates")

# API routers
app.include_router(experiments_router)
app.include_router(threads_router)
app.include_router(research_config_router)
app.include_router(config_sync_router)
app.include_router(forks_router)
app.include_router(profile_router)


@app.exception_handler(_LoginRequired)
async def login_redirect_handler(request: Request, exc: _LoginRequired):
  return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


@app.on_event("startup")
def on_startup():
  Base.metadata.create_all(bind=engine)


@app.middleware("http")
async def first_user_setup_middleware(request: Request, call_next):
  """If no users exist, redirect to /setup (initial account creation)."""
  path = request.url.path
  # Allow static, setup, and API paths through
  if path.startswith(("/static", "/setup", "/api")):
    return await call_next(request)
  db = SessionLocal()
  try:
    count = db.query(Researcher).count()
  finally:
    db.close()
  if count == 0 and path != "/setup":
    return RedirectResponse("/setup", status_code=303)
  return await call_next(request)


# ── Helper ──────────────────────────────────────────────────────────────────

def _user_ctx(request: Request, db: Session):
  """Common template context with current user."""
  user = get_current_user_from_cookie(request, db)
  return {"request": request, "user": user}


# ── Auth pages ──────────────────────────────────────────────────────────────

@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, db: Session = Depends(get_db)):
  """Initial setup — create the first admin account."""
  if db.query(Researcher).count() > 0:
    return RedirectResponse("/", status_code=303)
  return templates.TemplateResponse("setup.html", {"request": request, "user": None})


@app.post("/setup")
def setup_submit(
  request: Request,
  username: str = Form(...),
  display_name: str = Form(...),
  email: str = Form(...),
  password: str = Form(...),
  institution: str = Form(""),
  gpu_info: str = Form(""),
  db: Session = Depends(get_db),
):
  if db.query(Researcher).count() > 0:
    return RedirectResponse("/", status_code=303)
  researcher = Researcher(
    username=username,
    display_name=display_name,
    email=email,
    password_hash=hash_password(password),
    institution=institution,
    gpu_info=gpu_info,
  )
  db.add(researcher)
  db.commit()
  token = create_token({"sub": researcher.id, "username": researcher.username})
  response = RedirectResponse("/", status_code=303)
  response.set_cookie("access_token", token, httponly=True, max_age=72 * 3600)
  return response


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
  return templates.TemplateResponse("login.html", _user_ctx(request, db))


@app.post("/login")
def login_submit(
  request: Request,
  username: str = Form(...),
  password: str = Form(...),
  db: Session = Depends(get_db),
):
  researcher = db.query(Researcher).filter(Researcher.username == username).first()
  if not researcher or not verify_password(password, researcher.password_hash):
    return templates.TemplateResponse("login.html", {
      "request": request, "user": None, "error": "Invalid credentials",
    })
  token = create_token({"sub": researcher.id, "username": researcher.username})
  response = RedirectResponse("/", status_code=303)
  response.set_cookie("access_token", token, httponly=True, max_age=72 * 3600)
  return response


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, db: Session = Depends(get_db)):
  return templates.TemplateResponse("register.html", _user_ctx(request, db))


@app.post("/register")
def register_submit(
  request: Request,
  username: str = Form(...),
  display_name: str = Form(...),
  email: str = Form(...),
  password: str = Form(...),
  institution: str = Form(""),
  gpu_info: str = Form(""),
  db: Session = Depends(get_db),
):
  if db.query(Researcher).filter(Researcher.username == username).first():
    return templates.TemplateResponse("register.html", {
      "request": request, "user": None, "error": "Username already taken",
    })
  researcher = Researcher(
    username=username,
    display_name=display_name,
    email=email,
    password_hash=hash_password(password),
    institution=institution,
    gpu_info=gpu_info,
  )
  db.add(researcher)
  db.commit()
  token = create_token({"sub": researcher.id, "username": researcher.username})
  response = RedirectResponse("/", status_code=303)
  response.set_cookie("access_token", token, httponly=True, max_age=72 * 3600)
  return response


@app.get("/logout")
def logout():
  response = RedirectResponse("/", status_code=303)
  response.delete_cookie("access_token")
  return response


# ── Web pages ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
  ctx = _user_ctx(request, db)

  # Stats
  total_experiments = db.query(Experiment).count()
  total_researchers = db.query(Researcher).count()
  kept_experiments = db.query(Experiment).filter(Experiment.status == "keep").count()

  # Best val_bpb overall
  best = (
    db.query(Experiment)
    .filter(Experiment.status == "keep", Experiment.val_bpb > 0)
    .order_by(Experiment.val_bpb)
    .first()
  )

  # Recent experiments
  recent_experiments = (
    db.query(Experiment)
    .order_by(desc(Experiment.created_at))
    .limit(20)
    .all()
  )

  # Recent threads
  recent_threads = (
    db.query(ResearchThread)
    .order_by(desc(ResearchThread.updated_at))
    .limit(10)
    .all()
  )

  # Leaderboard
  leaderboard = (
    db.query(
      Researcher.display_name,
      Researcher.gpu_info,
      func.min(Experiment.val_bpb).label("best_bpb"),
      func.count(Experiment.id).label("total_experiments"),
    )
    .join(Experiment, Experiment.researcher_id == Researcher.id)
    .filter(Experiment.status == "keep", Experiment.val_bpb > 0)
    .group_by(Researcher.id)
    .order_by(func.min(Experiment.val_bpb))
    .all()
  )

  # Chart data: all kept experiments sorted by time
  chart_experiments = (
    db.query(Experiment)
    .filter(Experiment.status == "keep", Experiment.val_bpb > 0)
    .order_by(Experiment.created_at)
    .all()
  )
  chart_data = {}
  for e in chart_experiments:
    name = e.researcher.display_name
    if name not in chart_data:
      chart_data[name] = []
    chart_data[name].append({
      "x": e.created_at.isoformat(),
      "y": e.val_bpb,
      "desc": e.description,
    })

  ctx.update({
    "total_experiments": total_experiments,
    "total_researchers": total_researchers,
    "kept_experiments": kept_experiments,
    "best_experiment": best,
    "recent_experiments": recent_experiments,
    "recent_threads": recent_threads,
    "leaderboard": leaderboard,
    "chart_data_json": json.dumps(chart_data),
  })
  return templates.TemplateResponse("dashboard.html", ctx)


@app.get("/experiments", response_class=HTMLResponse)
def experiments_page(
  request: Request,
  researcher_id: Optional[int] = None,
  branch: Optional[str] = None,
  status: Optional[str] = None,
  db: Session = Depends(get_db),
):
  ctx = _user_ctx(request, db)
  q = db.query(Experiment)
  if researcher_id:
    q = q.filter(Experiment.researcher_id == researcher_id)
  if branch:
    q = q.filter(Experiment.branch_name == branch)
  if status:
    q = q.filter(Experiment.status == status)
  experiments = q.order_by(desc(Experiment.created_at)).limit(200).all()

  # Get unique branches and researchers for filter dropdowns
  branches = db.query(Experiment.branch_name).distinct().all()
  researchers = db.query(Researcher).all()

  ctx.update({
    "experiments": experiments,
    "branches": [b[0] for b in branches],
    "researchers": researchers,
    "filter_researcher_id": researcher_id,
    "filter_branch": branch,
    "filter_status": status,
  })
  return templates.TemplateResponse("experiments.html", ctx)


@app.get("/experiments/{experiment_id}", response_class=HTMLResponse)
def experiment_detail(experiment_id: int, request: Request, db: Session = Depends(get_db)):
  ctx = _user_ctx(request, db)
  exp = db.query(Experiment).filter(Experiment.id == experiment_id).first()
  if not exp:
    raise HTTPException(404, "Experiment not found")
  ctx["experiment"] = exp
  return templates.TemplateResponse("experiment_detail.html", ctx)


@app.post("/experiments/{experiment_id}/comment")
def post_experiment_comment(
  experiment_id: int,
  request: Request,
  body: str = Form(...),
  user: Researcher = Depends(require_user),
  db: Session = Depends(get_db),
):
  comment = ExperimentComment(
    experiment_id=experiment_id,
    author_id=user.id,
    body=body,
  )
  db.add(comment)
  db.commit()
  return RedirectResponse(f"/experiments/{experiment_id}", status_code=303)


@app.get("/threads", response_class=HTMLResponse)
def threads_page(
  request: Request,
  category: Optional[str] = None,
  db: Session = Depends(get_db),
):
  ctx = _user_ctx(request, db)
  q = db.query(ResearchThread)
  if category:
    q = q.filter(ResearchThread.category == category)
  threads = q.order_by(desc(ResearchThread.is_pinned), desc(ResearchThread.updated_at)).all()
  ctx.update({"threads": threads, "filter_category": category})
  return templates.TemplateResponse("threads.html", ctx)


@app.get("/threads/new", response_class=HTMLResponse)
def new_thread_page(request: Request, db: Session = Depends(get_db)):
  ctx = _user_ctx(request, db)
  if not ctx["user"]:
    return RedirectResponse("/login", status_code=303)
  return templates.TemplateResponse("thread_form.html", ctx)


@app.post("/threads/new")
def create_thread(
  request: Request,
  title: str = Form(...),
  body: str = Form(...),
  category: str = Form("hypothesis"),
  user: Researcher = Depends(require_user),
  db: Session = Depends(get_db),
):
  thread = ResearchThread(author_id=user.id, title=title, body=body, category=category)
  db.add(thread)
  db.commit()
  return RedirectResponse(f"/threads/{thread.id}", status_code=303)


@app.get("/threads/{thread_id}", response_class=HTMLResponse)
def thread_detail(thread_id: int, request: Request, db: Session = Depends(get_db)):
  ctx = _user_ctx(request, db)
  thread = db.query(ResearchThread).filter(ResearchThread.id == thread_id).first()
  if not thread:
    raise HTTPException(404, "Thread not found")

  # Get experiments for linking
  experiments = db.query(Experiment).filter(Experiment.status == "keep").order_by(desc(Experiment.created_at)).limit(50).all()
  ctx.update({"thread": thread, "experiments": experiments})
  return templates.TemplateResponse("thread_detail.html", ctx)


@app.post("/threads/{thread_id}/comment")
def post_thread_comment(
  thread_id: int,
  request: Request,
  body: str = Form(...),
  linked_experiment_id: Optional[int] = Form(None),
  user: Researcher = Depends(require_user),
  db: Session = Depends(get_db),
):
  from collab.models import ThreadComment
  comment = ThreadComment(
    thread_id=thread_id,
    author_id=user.id,
    body=body,
    linked_experiment_id=linked_experiment_id if linked_experiment_id else None,
  )
  db.add(comment)
  db.commit()
  return RedirectResponse(f"/threads/{thread_id}", status_code=303)


@app.get("/leaderboard", response_class=HTMLResponse)
def leaderboard_page(request: Request, db: Session = Depends(get_db)):
  ctx = _user_ctx(request, db)
  from sqlalchemy import case, Integer
  leaderboard = (
    db.query(
      Researcher,
      func.min(Experiment.val_bpb).label("best_bpb"),
      func.count(Experiment.id).label("total_experiments"),
      func.sum(case((Experiment.status == "keep", 1), else_=0)).label("kept"),
    )
    .join(Experiment, Experiment.researcher_id == Researcher.id)
    .filter(Experiment.val_bpb > 0)
    .group_by(Researcher.id)
    .order_by(func.min(Experiment.val_bpb))
    .all()
  )
  ctx["leaderboard"] = leaderboard
  return templates.TemplateResponse("leaderboard.html", ctx)


# ── API: generate token for CLI sync ────────────────────────────────────────

@app.post("/api/token")
def generate_api_token(
  username: str = Form(...),
  password: str = Form(...),
  db: Session = Depends(get_db),
):
  researcher = db.query(Researcher).filter(Researcher.username == username).first()
  if not researcher or not verify_password(password, researcher.password_hash):
    raise HTTPException(401, "Invalid credentials")
  token = create_token({"sub": researcher.id, "username": researcher.username})
  return {"token": token}


# ── Research Config web routes ──────────────────────────────────────────────

@app.get("/research-config", response_class=HTMLResponse)
def research_config_page(request: Request, db: Session = Depends(get_db)):
  ctx = _user_ctx(request, db)
  if not ctx["user"]:
    return RedirectResponse("/login", status_code=303)
  ctx["config"] = load_research_config()
  ctx["success"] = request.query_params.get("success")
  return templates.TemplateResponse("research_config.html", ctx)


@app.post("/research-config")
def save_research_config_form(
  request: Request,
  persona: str = Form(""),
  research_objective: str = Form(""),
  methodology: str = Form(""),
  constraints: str = Form(""),
  evaluation_criteria: str = Form(""),
  custom_instructions: str = Form(""),
  user: Researcher = Depends(require_user),
  db: Session = Depends(get_db),
):
  config = {
    "persona": persona,
    "research_objective": research_objective,
    "methodology": methodology,
    "constraints": constraints,
    "evaluation_criteria": evaluation_criteria,
    "custom_instructions": custom_instructions,
    "last_updated_by": user.display_name,
  }
  save_research_config(config)

  # Regenerate program.md in the repo root
  prompt = build_program_prompt(config)
  program_path = PLATFORM_DIR.parent / "program.md"
  program_path.write_text(prompt)

  # Auto-sync to all linked projects
  config_json = json.dumps(config, indent=2, ensure_ascii=False)
  for project in _load_linked_projects():
    project_path = Path(project["path"])
    if not project_path.is_dir():
      continue
    if project.get("sync_program_md", True):
      (project_path / "program.md").write_text(prompt)
    if project.get("sync_config_json", True):
      (project_path / "research_config.json").write_text(config_json)

  return RedirectResponse("/research-config?success=1", status_code=303)


@app.get("/research-config/preview", response_class=HTMLResponse)
def research_config_preview(request: Request, db: Session = Depends(get_db)):
  ctx = _user_ctx(request, db)
  config = load_research_config()
  ctx["prompt"] = build_program_prompt(config)
  return templates.TemplateResponse("prompt_preview.html", ctx)


@app.get("/research-config/download")
def download_program_md():
  from fastapi.responses import PlainTextResponse
  config = load_research_config()
  prompt = build_program_prompt(config)
  return PlainTextResponse(
    prompt,
    media_type="text/markdown",
    headers={"Content-Disposition": "attachment; filename=program.md"},
  )
