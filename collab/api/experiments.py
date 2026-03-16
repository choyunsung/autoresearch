"""Experiment CRUD and sync API."""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc

from collab.database import get_db
from collab.models import Experiment, ExperimentComment, Researcher
from collab.auth import require_user, get_current_user_api

router = APIRouter(prefix="/api/experiments", tags=["experiments"])


class ExperimentCreate(BaseModel):
  branch_name: str
  commit_hash: str
  val_bpb: float
  memory_gb: float = 0.0
  status: str  # keep, discard, crash
  description: str
  training_seconds: float = 0.0
  mfu_percent: float = 0.0
  total_tokens_m: float = 0.0
  num_steps: int = 0
  num_params_m: float = 0.0
  depth: int = 0
  code_diff: str = ""
  hyperparams_json: str = "{}"


class ExperimentBatchSync(BaseModel):
  """Batch upload from results.tsv via CLI sync tool."""
  branch_name: str
  experiments: list[ExperimentCreate]


class CommentCreate(BaseModel):
  body: str


@router.post("")
def create_experiment(
  data: ExperimentCreate,
  user: Researcher = Depends(get_current_user_api),
  db: Session = Depends(get_db),
):
  exp = Experiment(researcher_id=user.id, **data.model_dump())
  db.add(exp)
  db.commit()
  db.refresh(exp)
  return {"id": exp.id, "status": "created"}


@router.post("/sync")
def batch_sync(
  data: ExperimentBatchSync,
  user: Researcher = Depends(get_current_user_api),
  db: Session = Depends(get_db),
):
  """Sync a batch of experiments (from CLI sync tool). Skips duplicates by commit_hash."""
  created = 0
  skipped = 0
  for exp_data in data.experiments:
    existing = db.query(Experiment).filter(
      Experiment.researcher_id == user.id,
      Experiment.commit_hash == exp_data.commit_hash,
    ).first()
    if existing:
      skipped += 1
      continue
    exp = Experiment(
      researcher_id=user.id,
      branch_name=data.branch_name,
      **exp_data.model_dump(),
    )
    db.add(exp)
    created += 1
  db.commit()
  return {"created": created, "skipped": skipped}


@router.get("")
def list_experiments(
  researcher_id: Optional[int] = None,
  branch: Optional[str] = None,
  status_filter: Optional[str] = Query(None, alias="status"),
  limit: int = 100,
  offset: int = 0,
  db: Session = Depends(get_db),
):
  q = db.query(Experiment)
  if researcher_id:
    q = q.filter(Experiment.researcher_id == researcher_id)
  if branch:
    q = q.filter(Experiment.branch_name == branch)
  if status_filter:
    q = q.filter(Experiment.status == status_filter)
  total = q.count()
  experiments = q.order_by(desc(Experiment.created_at)).offset(offset).limit(limit).all()
  return {
    "total": total,
    "experiments": [
      {
        "id": e.id,
        "researcher": e.researcher.display_name,
        "researcher_id": e.researcher_id,
        "branch_name": e.branch_name,
        "commit_hash": e.commit_hash,
        "val_bpb": e.val_bpb,
        "memory_gb": e.memory_gb,
        "status": e.status,
        "description": e.description,
        "training_seconds": e.training_seconds,
        "mfu_percent": e.mfu_percent,
        "total_tokens_m": e.total_tokens_m,
        "num_steps": e.num_steps,
        "num_params_m": e.num_params_m,
        "depth": e.depth,
        "code_diff": e.code_diff,
        "hyperparams_json": e.hyperparams_json,
        "comment_count": len(e.comments),
        "created_at": e.created_at.isoformat(),
      }
      for e in experiments
    ],
  }


@router.get("/{experiment_id}")
def get_experiment(experiment_id: int, db: Session = Depends(get_db)):
  exp = db.query(Experiment).filter(Experiment.id == experiment_id).first()
  if not exp:
    raise HTTPException(404, "Experiment not found")
  return {
    "id": exp.id,
    "researcher": exp.researcher.display_name,
    "researcher_id": exp.researcher_id,
    "branch_name": exp.branch_name,
    "commit_hash": exp.commit_hash,
    "val_bpb": exp.val_bpb,
    "memory_gb": exp.memory_gb,
    "status": exp.status,
    "description": exp.description,
    "training_seconds": exp.training_seconds,
    "mfu_percent": exp.mfu_percent,
    "total_tokens_m": exp.total_tokens_m,
    "num_steps": exp.num_steps,
    "num_params_m": exp.num_params_m,
    "depth": exp.depth,
    "code_diff": exp.code_diff,
    "hyperparams_json": exp.hyperparams_json,
    "comments": [
      {
        "id": c.id,
        "author": c.author.display_name,
        "body": c.body,
        "created_at": c.created_at.isoformat(),
      }
      for c in exp.comments
    ],
    "created_at": exp.created_at.isoformat(),
  }


@router.post("/{experiment_id}/comments")
def add_comment(
  experiment_id: int,
  data: CommentCreate,
  user: Researcher = Depends(require_user),
  db: Session = Depends(get_db),
):
  exp = db.query(Experiment).filter(Experiment.id == experiment_id).first()
  if not exp:
    raise HTTPException(404, "Experiment not found")
  comment = ExperimentComment(
    experiment_id=experiment_id,
    author_id=user.id,
    body=data.body,
  )
  db.add(comment)
  db.commit()
  db.refresh(comment)
  return {"id": comment.id, "status": "created"}


@router.get("/leaderboard/best")
def leaderboard(db: Session = Depends(get_db)):
  """Best val_bpb per researcher (only 'keep' experiments)."""
  from sqlalchemy import func
  results = (
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
  return [
    {
      "researcher": r.display_name,
      "gpu": r.gpu_info,
      "best_bpb": r.best_bpb,
      "total_experiments": r.total_experiments,
    }
    for r in results
  ]
