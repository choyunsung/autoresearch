"""Fork & Merge Request web routes."""

from typing import Optional
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from pathlib import Path

from collab.database import get_db
from collab.models import (
  Researcher, Experiment, ResearchFork, MergeRequest, MergeRequestComment,
)
from collab.auth import get_current_user_from_cookie, require_user

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def _user_ctx(request: Request, db: Session):
  user = get_current_user_from_cookie(request, db)
  return {"request": request, "user": user}


# ── Fork routes ─────────────────────────────────────────────────────────────

@router.get("/forks", response_class=HTMLResponse)
def forks_list(request: Request, db: Session = Depends(get_db)):
  ctx = _user_ctx(request, db)
  forks = db.query(ResearchFork).order_by(desc(ResearchFork.created_at)).all()

  # Enrich with experiment counts per fork
  fork_data = []
  for f in forks:
    exp_count = db.query(Experiment).filter(
      Experiment.researcher_id == f.forker_id,
      Experiment.branch_name == f.fork_branch_name,
    ).count()
    kept_count = db.query(Experiment).filter(
      Experiment.researcher_id == f.forker_id,
      Experiment.branch_name == f.fork_branch_name,
      Experiment.status == "keep",
    ).count()
    best_exp = (
      db.query(Experiment)
      .filter(
        Experiment.researcher_id == f.forker_id,
        Experiment.branch_name == f.fork_branch_name,
        Experiment.status == "keep",
        Experiment.val_bpb > 0,
      )
      .order_by(Experiment.val_bpb)
      .first()
    )
    fork_data.append({
      "fork": f,
      "exp_count": exp_count,
      "kept_count": kept_count,
      "best_val_bpb": best_exp.val_bpb if best_exp else None,
    })

  ctx["fork_data"] = fork_data
  return templates.TemplateResponse("forks.html", ctx)


@router.get("/experiments/{experiment_id}/fork", response_class=HTMLResponse)
def fork_form(experiment_id: int, request: Request, db: Session = Depends(get_db)):
  ctx = _user_ctx(request, db)
  if not ctx["user"]:
    return RedirectResponse("/login", status_code=303)
  exp = db.query(Experiment).filter(Experiment.id == experiment_id).first()
  if not exp:
    raise HTTPException(404, "Experiment not found")
  ctx["experiment"] = exp
  return templates.TemplateResponse("fork_form.html", ctx)


@router.post("/experiments/{experiment_id}/fork")
def create_fork(
  experiment_id: int,
  request: Request,
  fork_branch_name: str = Form(...),
  description: str = Form(""),
  user: Researcher = Depends(require_user),
  db: Session = Depends(get_db),
):
  exp = db.query(Experiment).filter(Experiment.id == experiment_id).first()
  if not exp:
    raise HTTPException(404, "Experiment not found")

  # Prevent self-fork (you can still do it, but warn)
  fork = ResearchFork(
    forker_id=user.id,
    source_experiment_id=exp.id,
    source_researcher_id=exp.researcher_id,
    fork_branch_name=fork_branch_name,
    source_branch_name=exp.branch_name,
    source_commit_hash=exp.commit_hash,
    description=description,
  )
  db.add(fork)
  db.commit()
  return RedirectResponse(f"/forks/{fork.id}", status_code=303)


@router.get("/forks/{fork_id}", response_class=HTMLResponse)
def fork_detail(fork_id: int, request: Request, db: Session = Depends(get_db)):
  ctx = _user_ctx(request, db)
  fork = db.query(ResearchFork).filter(ResearchFork.id == fork_id).first()
  if not fork:
    raise HTTPException(404, "Fork not found")

  # Get experiments on this fork branch
  experiments = (
    db.query(Experiment)
    .filter(
      Experiment.researcher_id == fork.forker_id,
      Experiment.branch_name == fork.fork_branch_name,
    )
    .order_by(desc(Experiment.created_at))
    .all()
  )

  # Source experiment
  source_exp = fork.source_experiment

  ctx.update({
    "fork": fork,
    "experiments": experiments,
    "source_exp": source_exp,
  })
  return templates.TemplateResponse("fork_detail.html", ctx)


# ── Merge Request routes ────────────────────────────────────────────────────

@router.get("/forks/{fork_id}/merge/new", response_class=HTMLResponse)
def merge_request_form(fork_id: int, request: Request, db: Session = Depends(get_db)):
  ctx = _user_ctx(request, db)
  if not ctx["user"]:
    return RedirectResponse("/login", status_code=303)

  fork = db.query(ResearchFork).filter(ResearchFork.id == fork_id).first()
  if not fork:
    raise HTTPException(404, "Fork not found")

  # Get fork stats
  kept = (
    db.query(Experiment)
    .filter(
      Experiment.researcher_id == fork.forker_id,
      Experiment.branch_name == fork.fork_branch_name,
      Experiment.status == "keep",
      Experiment.val_bpb > 0,
    )
    .order_by(Experiment.val_bpb)
    .all()
  )
  total = db.query(Experiment).filter(
    Experiment.researcher_id == fork.forker_id,
    Experiment.branch_name == fork.fork_branch_name,
  ).count()

  ctx.update({
    "fork": fork,
    "kept_experiments": kept,
    "total_experiments": total,
    "best_val_bpb": kept[0].val_bpb if kept else 0,
  })
  return templates.TemplateResponse("merge_request_form.html", ctx)


@router.post("/forks/{fork_id}/merge")
def create_merge_request(
  fork_id: int,
  title: str = Form(...),
  body: str = Form(""),
  user: Researcher = Depends(require_user),
  db: Session = Depends(get_db),
):
  fork = db.query(ResearchFork).filter(ResearchFork.id == fork_id).first()
  if not fork:
    raise HTTPException(404, "Fork not found")

  # Compute stats
  kept = (
    db.query(Experiment)
    .filter(
      Experiment.researcher_id == fork.forker_id,
      Experiment.branch_name == fork.fork_branch_name,
      Experiment.status == "keep",
      Experiment.val_bpb > 0,
    )
    .all()
  )
  total = db.query(Experiment).filter(
    Experiment.researcher_id == fork.forker_id,
    Experiment.branch_name == fork.fork_branch_name,
  ).count()

  mr = MergeRequest(
    fork_id=fork_id,
    author_id=user.id,
    target_researcher_id=fork.source_researcher_id,
    title=title,
    body=body,
    best_val_bpb=min((e.val_bpb for e in kept), default=0),
    experiments_count=total,
    kept_count=len(kept),
  )
  db.add(mr)
  db.commit()
  return RedirectResponse(f"/merge-requests/{mr.id}", status_code=303)


@router.get("/merge-requests", response_class=HTMLResponse)
def merge_requests_list(request: Request, db: Session = Depends(get_db)):
  ctx = _user_ctx(request, db)
  mrs = db.query(MergeRequest).order_by(desc(MergeRequest.created_at)).all()
  ctx["merge_requests"] = mrs
  return templates.TemplateResponse("merge_requests.html", ctx)


@router.get("/merge-requests/{mr_id}", response_class=HTMLResponse)
def merge_request_detail(mr_id: int, request: Request, db: Session = Depends(get_db)):
  ctx = _user_ctx(request, db)
  mr = db.query(MergeRequest).filter(MergeRequest.id == mr_id).first()
  if not mr:
    raise HTTPException(404, "Merge request not found")

  # Get fork experiments for comparison
  fork = mr.fork
  fork_experiments = (
    db.query(Experiment)
    .filter(
      Experiment.researcher_id == fork.forker_id,
      Experiment.branch_name == fork.fork_branch_name,
      Experiment.status == "keep",
    )
    .order_by(Experiment.val_bpb)
    .all()
  )

  ctx.update({"mr": mr, "fork_experiments": fork_experiments})
  return templates.TemplateResponse("merge_request_detail.html", ctx)


@router.post("/merge-requests/{mr_id}/comment")
def post_mr_comment(
  mr_id: int,
  body: str = Form(...),
  user: Researcher = Depends(require_user),
  db: Session = Depends(get_db),
):
  comment = MergeRequestComment(
    merge_request_id=mr_id,
    author_id=user.id,
    body=body,
  )
  db.add(comment)
  db.commit()
  return RedirectResponse(f"/merge-requests/{mr_id}", status_code=303)


@router.post("/merge-requests/{mr_id}/review")
def review_merge_request(
  mr_id: int,
  action: str = Form(...),  # approve, reject, merge
  reviewer_comment: str = Form(""),
  user: Researcher = Depends(require_user),
  db: Session = Depends(get_db),
):
  from datetime import datetime, timezone
  mr = db.query(MergeRequest).filter(MergeRequest.id == mr_id).first()
  if not mr:
    raise HTTPException(404, "Merge request not found")

  # Only the target researcher can review
  if user.id != mr.target_researcher_id:
    raise HTTPException(403, "Only the original researcher can review this merge request")

  if action == "approve":
    mr.status = "approved"
  elif action == "reject":
    mr.status = "rejected"
  elif action == "merge":
    mr.status = "merged"
    mr.fork.status = "merged"
  else:
    raise HTTPException(400, "Invalid action")

  mr.reviewer_comment = reviewer_comment
  mr.reviewed_at = datetime.now(timezone.utc)
  db.commit()
  return RedirectResponse(f"/merge-requests/{mr_id}", status_code=303)
