"""Config sync — propagate research configuration changes to linked projects.

When the research config is updated on the platform, this module can
push the updated prompt (program.md) and configuration to any number
of linked project directories. This ensures all researchers use the
same research persona, objectives, and methodology.
"""

import json
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from collab.database import get_db
from collab.models import Researcher
from collab.auth import require_user
from collab.api.research_config import _load_config, build_program_prompt

router = APIRouter(prefix="/api/config-sync", tags=["config-sync"])

LINKED_PROJECTS_FILE = Path(__file__).parent.parent / "linked_projects.json"


def _load_linked_projects() -> list[dict]:
  if LINKED_PROJECTS_FILE.exists():
    return json.loads(LINKED_PROJECTS_FILE.read_text())
  return []


def _save_linked_projects(projects: list[dict]):
  LINKED_PROJECTS_FILE.write_text(json.dumps(projects, indent=2, ensure_ascii=False))


class LinkProject(BaseModel):
  name: str
  path: str  # Absolute path to the project directory
  sync_program_md: bool = True  # Sync program.md
  sync_config_json: bool = True  # Sync research_config.json


@router.get("/projects")
def list_linked_projects():
  return _load_linked_projects()


@router.post("/projects")
def link_project(
  data: LinkProject,
  user: Researcher = Depends(require_user),
):
  projects = _load_linked_projects()
  # Check for duplicates
  for p in projects:
    if p["path"] == data.path:
      raise HTTPException(400, "Project already linked")
  # Verify path exists
  project_path = Path(data.path)
  if not project_path.is_dir():
    raise HTTPException(400, f"Directory not found: {data.path}")
  projects.append(data.model_dump())
  _save_linked_projects(projects)
  return {"status": "linked", "total": len(projects)}


@router.delete("/projects/{index}")
def unlink_project(
  index: int,
  user: Researcher = Depends(require_user),
):
  projects = _load_linked_projects()
  if index < 0 or index >= len(projects):
    raise HTTPException(404, "Project index out of range")
  removed = projects.pop(index)
  _save_linked_projects(projects)
  return {"status": "unlinked", "removed": removed["name"]}


@router.post("/push")
def push_config_to_all(
  user: Researcher = Depends(require_user),
):
  """Push current research config to all linked projects."""
  config = _load_config()
  prompt = build_program_prompt(config)
  config_json = json.dumps(config, indent=2, ensure_ascii=False)

  projects = _load_linked_projects()
  results = []

  for project in projects:
    project_path = Path(project["path"])
    status = {"name": project["name"], "path": project["path"]}

    if not project_path.is_dir():
      status["error"] = "Directory not found"
      results.append(status)
      continue

    try:
      if project.get("sync_program_md", True):
        (project_path / "program.md").write_text(prompt)
        status["program_md"] = "synced"

      if project.get("sync_config_json", True):
        (project_path / "research_config.json").write_text(config_json)
        status["config_json"] = "synced"

      status["status"] = "ok"
    except Exception as e:
      status["error"] = str(e)

    results.append(status)

  return {"synced": len([r for r in results if r.get("status") == "ok"]), "results": results}
