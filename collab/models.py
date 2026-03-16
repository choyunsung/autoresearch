"""SQLAlchemy models for the collaborative research platform."""

from datetime import datetime, timezone
from sqlalchemy import (
  Column, Integer, String, Float, Text, DateTime, ForeignKey, Boolean, Table
)
from sqlalchemy.orm import relationship
from collab.database import Base


def utcnow():
  return datetime.now(timezone.utc)


# Many-to-many: experiments <-> tags
experiment_tags = Table(
  "experiment_tags", Base.metadata,
  Column("experiment_id", Integer, ForeignKey("experiments.id"), primary_key=True),
  Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
)


class Researcher(Base):
  __tablename__ = "researchers"

  id = Column(Integer, primary_key=True, index=True)
  username = Column(String(50), unique=True, nullable=False, index=True)
  display_name = Column(String(100), nullable=False)
  email = Column(String(200), unique=True, nullable=False)
  password_hash = Column(String(200), nullable=False)
  institution = Column(String(200), default="")
  bio = Column(Text, default="")
  gpu_info = Column(String(200), default="")  # e.g. "H100 80GB"
  created_at = Column(DateTime, default=utcnow)

  experiments = relationship("Experiment", back_populates="researcher")
  threads = relationship("ResearchThread", back_populates="author")
  thread_comments = relationship("ThreadComment", back_populates="author")
  experiment_comments = relationship("ExperimentComment", back_populates="author")


class Experiment(Base):
  __tablename__ = "experiments"

  id = Column(Integer, primary_key=True, index=True)
  researcher_id = Column(Integer, ForeignKey("researchers.id"), nullable=False)
  branch_name = Column(String(100), nullable=False)  # e.g. "autoresearch/mar5"
  commit_hash = Column(String(40), nullable=False)
  val_bpb = Column(Float, nullable=False)
  memory_gb = Column(Float, default=0.0)
  status = Column(String(20), nullable=False)  # keep, discard, crash
  description = Column(Text, nullable=False)
  # Extended metrics
  training_seconds = Column(Float, default=0.0)
  mfu_percent = Column(Float, default=0.0)
  total_tokens_m = Column(Float, default=0.0)
  num_steps = Column(Integer, default=0)
  num_params_m = Column(Float, default=0.0)
  depth = Column(Integer, default=0)
  # Code diff (optional, for showing what changed)
  code_diff = Column(Text, default="")
  # Hyperparameters snapshot (JSON string)
  hyperparams_json = Column(Text, default="{}")
  created_at = Column(DateTime, default=utcnow)

  researcher = relationship("Researcher", back_populates="experiments")
  comments = relationship("ExperimentComment", back_populates="experiment", cascade="all, delete-orphan")
  tags = relationship("ExperimentTag", secondary=experiment_tags, back_populates="experiments")


class ExperimentTag(Base):
  __tablename__ = "tags"

  id = Column(Integer, primary_key=True, index=True)
  name = Column(String(50), unique=True, nullable=False)

  experiments = relationship("Experiment", secondary=experiment_tags, back_populates="tags")


class ResearchThread(Base):
  __tablename__ = "research_threads"

  id = Column(Integer, primary_key=True, index=True)
  author_id = Column(Integer, ForeignKey("researchers.id"), nullable=False)
  title = Column(String(300), nullable=False)
  body = Column(Text, nullable=False)  # Markdown supported
  category = Column(String(50), default="hypothesis")  # hypothesis, discussion, insight, question
  is_pinned = Column(Boolean, default=False)
  created_at = Column(DateTime, default=utcnow)
  updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

  author = relationship("Researcher", back_populates="threads")
  comments = relationship("ThreadComment", back_populates="thread", cascade="all, delete-orphan")


class ThreadComment(Base):
  __tablename__ = "thread_comments"

  id = Column(Integer, primary_key=True, index=True)
  thread_id = Column(Integer, ForeignKey("research_threads.id"), nullable=False)
  author_id = Column(Integer, ForeignKey("researchers.id"), nullable=False)
  body = Column(Text, nullable=False)
  # Optional link to an experiment as evidence
  linked_experiment_id = Column(Integer, ForeignKey("experiments.id"), nullable=True)
  created_at = Column(DateTime, default=utcnow)

  thread = relationship("ResearchThread", back_populates="comments")
  author = relationship("Researcher", back_populates="thread_comments")
  linked_experiment = relationship("Experiment")


class ExperimentComment(Base):
  __tablename__ = "experiment_comments"

  id = Column(Integer, primary_key=True, index=True)
  experiment_id = Column(Integer, ForeignKey("experiments.id"), nullable=False)
  author_id = Column(Integer, ForeignKey("researchers.id"), nullable=False)
  body = Column(Text, nullable=False)
  created_at = Column(DateTime, default=utcnow)

  experiment = relationship("Experiment", back_populates="comments")
  author = relationship("Researcher", back_populates="experiment_comments")


class ResearchFork(Base):
  """A researcher forks another's experiment branch to continue research independently."""
  __tablename__ = "research_forks"

  id = Column(Integer, primary_key=True, index=True)
  # Who forked
  forker_id = Column(Integer, ForeignKey("researchers.id"), nullable=False)
  # Original source
  source_experiment_id = Column(Integer, ForeignKey("experiments.id"), nullable=False)
  source_researcher_id = Column(Integer, ForeignKey("researchers.id"), nullable=False)
  # Fork metadata
  fork_branch_name = Column(String(100), nullable=False)  # e.g. "autoresearch/mar16-yunsung-fork"
  source_branch_name = Column(String(100), nullable=False)
  source_commit_hash = Column(String(40), nullable=False)  # commit forked from
  description = Column(Text, default="")
  status = Column(String(20), default="active")  # active, merged, abandoned
  created_at = Column(DateTime, default=utcnow)

  forker = relationship("Researcher", foreign_keys=[forker_id], backref="forks_created")
  source_researcher = relationship("Researcher", foreign_keys=[source_researcher_id])
  source_experiment = relationship("Experiment")
  merge_requests = relationship("MergeRequest", back_populates="fork", cascade="all, delete-orphan")


class MergeRequest(Base):
  """Request to merge fork results back into the source researcher's branch."""
  __tablename__ = "merge_requests"

  id = Column(Integer, primary_key=True, index=True)
  fork_id = Column(Integer, ForeignKey("research_forks.id"), nullable=False)
  author_id = Column(Integer, ForeignKey("researchers.id"), nullable=False)
  target_researcher_id = Column(Integer, ForeignKey("researchers.id"), nullable=False)
  title = Column(String(300), nullable=False)
  body = Column(Text, default="")  # Description of what improved
  # Key metrics to show improvement
  best_val_bpb = Column(Float, default=0.0)
  experiments_count = Column(Integer, default=0)
  kept_count = Column(Integer, default=0)
  # Review state
  status = Column(String(20), default="open")  # open, approved, rejected, merged
  reviewer_comment = Column(Text, default="")
  reviewed_at = Column(DateTime, nullable=True)
  created_at = Column(DateTime, default=utcnow)

  fork = relationship("ResearchFork", back_populates="merge_requests")
  author = relationship("Researcher", foreign_keys=[author_id])
  target_researcher = relationship("Researcher", foreign_keys=[target_researcher_id])
  comments = relationship("MergeRequestComment", back_populates="merge_request", cascade="all, delete-orphan")


class MergeRequestComment(Base):
  __tablename__ = "merge_request_comments"

  id = Column(Integer, primary_key=True, index=True)
  merge_request_id = Column(Integer, ForeignKey("merge_requests.id"), nullable=False)
  author_id = Column(Integer, ForeignKey("researchers.id"), nullable=False)
  body = Column(Text, nullable=False)
  created_at = Column(DateTime, default=utcnow)

  merge_request = relationship("MergeRequest", back_populates="comments")
  author = relationship("Researcher")
