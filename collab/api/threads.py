"""Research threads — hypotheses, discussions, insights."""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc

from collab.database import get_db
from collab.models import ResearchThread, ThreadComment, Researcher
from collab.auth import require_user

router = APIRouter(prefix="/api/threads", tags=["threads"])


class ThreadCreate(BaseModel):
  title: str
  body: str
  category: str = "hypothesis"  # hypothesis, discussion, insight, question


class ThreadCommentCreate(BaseModel):
  body: str
  linked_experiment_id: Optional[int] = None


@router.post("")
def create_thread(
  data: ThreadCreate,
  user: Researcher = Depends(require_user),
  db: Session = Depends(get_db),
):
  thread = ResearchThread(author_id=user.id, **data.model_dump())
  db.add(thread)
  db.commit()
  db.refresh(thread)
  return {"id": thread.id, "status": "created"}


@router.get("")
def list_threads(
  category: Optional[str] = None,
  limit: int = 50,
  offset: int = 0,
  db: Session = Depends(get_db),
):
  q = db.query(ResearchThread)
  if category:
    q = q.filter(ResearchThread.category == category)
  total = q.count()
  threads = (
    q.order_by(desc(ResearchThread.is_pinned), desc(ResearchThread.updated_at))
    .offset(offset).limit(limit).all()
  )
  return {
    "total": total,
    "threads": [
      {
        "id": t.id,
        "title": t.title,
        "author": t.author.display_name,
        "category": t.category,
        "is_pinned": t.is_pinned,
        "comment_count": len(t.comments),
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
      }
      for t in threads
    ],
  }


@router.get("/{thread_id}")
def get_thread(thread_id: int, db: Session = Depends(get_db)):
  thread = db.query(ResearchThread).filter(ResearchThread.id == thread_id).first()
  if not thread:
    raise HTTPException(404, "Thread not found")
  return {
    "id": thread.id,
    "title": thread.title,
    "body": thread.body,
    "author": thread.author.display_name,
    "author_id": thread.author_id,
    "category": thread.category,
    "is_pinned": thread.is_pinned,
    "comments": [
      {
        "id": c.id,
        "author": c.author.display_name,
        "body": c.body,
        "linked_experiment_id": c.linked_experiment_id,
        "linked_experiment": (
          {
            "commit_hash": c.linked_experiment.commit_hash,
            "val_bpb": c.linked_experiment.val_bpb,
            "description": c.linked_experiment.description,
          }
          if c.linked_experiment else None
        ),
        "created_at": c.created_at.isoformat(),
      }
      for c in thread.comments
    ],
    "created_at": thread.created_at.isoformat(),
    "updated_at": thread.updated_at.isoformat(),
  }


@router.post("/{thread_id}/comments")
def add_comment(
  thread_id: int,
  data: ThreadCommentCreate,
  user: Researcher = Depends(require_user),
  db: Session = Depends(get_db),
):
  thread = db.query(ResearchThread).filter(ResearchThread.id == thread_id).first()
  if not thread:
    raise HTTPException(404, "Thread not found")
  comment = ThreadComment(
    thread_id=thread_id,
    author_id=user.id,
    body=data.body,
    linked_experiment_id=data.linked_experiment_id,
  )
  db.add(comment)
  db.commit()
  db.refresh(comment)
  return {"id": comment.id, "status": "created"}
