"""User profile web routes."""

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from pathlib import Path

from collab.database import get_db
from collab.models import Researcher, Experiment, ResearchFork, MergeRequest
from collab.auth import get_current_user_from_cookie, require_user

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def _user_ctx(request: Request, db: Session):
  user = get_current_user_from_cookie(request, db)
  return {"request": request, "user": user}


@router.get("/profile", response_class=HTMLResponse)
def my_profile(request: Request, db: Session = Depends(get_db)):
  ctx = _user_ctx(request, db)
  if not ctx["user"]:
    return RedirectResponse("/login", status_code=303)
  return RedirectResponse(f"/researchers/{ctx['user'].id}", status_code=303)


@router.get("/profile/edit", response_class=HTMLResponse)
def edit_profile_page(request: Request, db: Session = Depends(get_db)):
  ctx = _user_ctx(request, db)
  if not ctx["user"]:
    return RedirectResponse("/login", status_code=303)
  return templates.TemplateResponse("profile_edit.html", ctx)


@router.post("/profile/edit")
def edit_profile_submit(
  request: Request,
  display_name: str = Form(...),
  email: str = Form(...),
  institution: str = Form(""),
  bio: str = Form(""),
  gpu_info: str = Form(""),
  user: Researcher = Depends(require_user),
  db: Session = Depends(get_db),
):
  user.display_name = display_name
  user.email = email
  user.institution = institution
  user.bio = bio
  user.gpu_info = gpu_info
  db.commit()
  return RedirectResponse(f"/researchers/{user.id}", status_code=303)


@router.get("/profile/token", response_class=HTMLResponse)
def api_token_page(request: Request, db: Session = Depends(get_db)):
  ctx = _user_ctx(request, db)
  if not ctx["user"]:
    return RedirectResponse("/login", status_code=303)
  return templates.TemplateResponse("api_token.html", ctx)


@router.post("/profile/token")
def generate_token_page(
  request: Request,
  user: Researcher = Depends(require_user),
  db: Session = Depends(get_db),
):
  from collab.auth import create_token
  token = create_token({"sub": user.id, "username": user.username})
  ctx = _user_ctx(request, db)
  ctx["token"] = token
  return templates.TemplateResponse("api_token.html", ctx)


@router.get("/researchers/{researcher_id}", response_class=HTMLResponse)
def researcher_profile(researcher_id: int, request: Request, db: Session = Depends(get_db)):
  ctx = _user_ctx(request, db)
  researcher = db.query(Researcher).filter(Researcher.id == researcher_id).first()
  if not researcher:
    raise HTTPException(404, "Researcher not found")

  # Stats
  total_exp = db.query(Experiment).filter(Experiment.researcher_id == researcher_id).count()
  kept_exp = db.query(Experiment).filter(
    Experiment.researcher_id == researcher_id, Experiment.status == "keep"
  ).count()
  best = (
    db.query(Experiment)
    .filter(Experiment.researcher_id == researcher_id, Experiment.status == "keep", Experiment.val_bpb > 0)
    .order_by(Experiment.val_bpb)
    .first()
  )

  # Recent experiments
  recent = (
    db.query(Experiment)
    .filter(Experiment.researcher_id == researcher_id)
    .order_by(desc(Experiment.created_at))
    .limit(20)
    .all()
  )

  # Branches
  branches = (
    db.query(Experiment.branch_name, func.count(Experiment.id).label("count"))
    .filter(Experiment.researcher_id == researcher_id)
    .group_by(Experiment.branch_name)
    .all()
  )

  # Forks created by this researcher
  forks_created = (
    db.query(ResearchFork)
    .filter(ResearchFork.forker_id == researcher_id)
    .order_by(desc(ResearchFork.created_at))
    .all()
  )

  # Forks of this researcher's work
  forks_of_work = (
    db.query(ResearchFork)
    .filter(ResearchFork.source_researcher_id == researcher_id)
    .order_by(desc(ResearchFork.created_at))
    .all()
  )

  # Pending merge requests (for this researcher to review)
  pending_mrs = (
    db.query(MergeRequest)
    .filter(MergeRequest.target_researcher_id == researcher_id, MergeRequest.status == "open")
    .all()
  )

  ctx.update({
    "researcher": researcher,
    "total_exp": total_exp,
    "kept_exp": kept_exp,
    "best_experiment": best,
    "recent_experiments": recent,
    "branches": branches,
    "forks_created": forks_created,
    "forks_of_work": forks_of_work,
    "pending_mrs": pending_mrs,
    "is_own_profile": ctx["user"] and ctx["user"].id == researcher_id,
  })
  return templates.TemplateResponse("profile.html", ctx)
