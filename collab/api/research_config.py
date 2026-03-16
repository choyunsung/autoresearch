"""Research configuration — persona, objectives, and prompt generation.

This module manages the shared research configuration that all researchers
use when running autoresearch experiments. Changes here propagate to the
generated program.md prompts that agents use.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc

from collab.database import get_db
from collab.models import Researcher
from collab.auth import require_user

router = APIRouter(prefix="/api/research-config", tags=["research-config"])


class ResearchConfig(BaseModel):
  """The shared research configuration."""
  persona: str = ""
  research_objective: str = ""
  methodology: str = ""
  constraints: str = ""
  evaluation_criteria: str = ""
  custom_instructions: str = ""


# In-memory config (persisted to a JSON file alongside the DB)
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "research_config.json"

def _load_config() -> dict:
  if CONFIG_PATH.exists():
    return json.loads(CONFIG_PATH.read_text())
  return {}

def _save_config(config: dict):
  CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False))


@router.get("")
def get_config():
  return _load_config()


@router.put("")
def update_config(
  config: ResearchConfig,
  user: Researcher = Depends(require_user),
):
  data = config.model_dump()
  data["last_updated_by"] = user.display_name
  _save_config(data)
  return {"status": "updated"}


@router.get("/generate-prompt")
def generate_prompt():
  """Generate the program.md prompt incorporating the research config."""
  config = _load_config()
  return {"prompt": build_program_prompt(config)}


def build_program_prompt(config: dict) -> str:
  """Build the full agent prompt (program.md) from research config.

  This is the key integration point: the autoresearch agent instructions
  are generated from the collaborative research configuration.
  """
  persona = config.get("persona", "")
  objective = config.get("research_objective", "")
  methodology = config.get("methodology", "")
  constraints = config.get("constraints", "")
  evaluation = config.get("evaluation_criteria", "")
  custom = config.get("custom_instructions", "")

  prompt = """# autoresearch

This is an experiment to have the LLM do its own research.
"""

  if persona:
    prompt += f"""
## Research Persona

{persona}
"""

  if objective:
    prompt += f"""
## Research Objective

{objective}
"""

  prompt += """
## Setup

To set up a new experiment, work with the user to:

1. **Agree on a run tag**: propose a tag based on today's date (e.g. `mar5`). The branch `autoresearch/<tag>` must not already exist — this is a fresh run.
2. **Create the branch**: `git checkout -b autoresearch/<tag>` from current master.
3. **Read the in-scope files**: The repo is small. Read these files for full context:
   - `README.md` — repository context.
   - `prepare.py` — fixed constants, data prep, tokenizer, dataloader, evaluation. Do not modify.
   - `train.py` — the file you modify. Model architecture, optimizer, training loop.
4. **Verify data exists**: Check that `~/.cache/autoresearch/` contains data shards and a tokenizer. If not, tell the human to run `uv run prepare.py`.
5. **Initialize results.tsv**: Create `results.tsv` with just the header row. The baseline will be recorded after the first run.
6. **Confirm and go**: Confirm setup looks good.

Once you get confirmation, kick off the experimentation.

## Experimentation

Each experiment runs on a single GPU. The training script runs for a **fixed time budget of 5 minutes** (wall clock training time, excluding startup/compilation). You launch it simply as: `uv run train.py`.

**What you CAN do:**
- Modify `train.py` — this is the only file you edit. Everything is fair game: model architecture, optimizer, hyperparameters, training loop, batch size, model size, etc.

**What you CANNOT do:**
- Modify `prepare.py`. It is read-only.
- Install new packages or add dependencies.
- Modify the evaluation harness.
"""

  if methodology:
    prompt += f"""
## Research Methodology

{methodology}
"""

  prompt += """
**The goal is simple: get the lowest val_bpb.** Since the time budget is fixed, you don't need to worry about training time — it's always 5 minutes. Everything is fair game: change the architecture, the optimizer, the hyperparameters, the batch size, the model size.
"""

  if constraints:
    prompt += f"""
## Additional Constraints

{constraints}
"""

  if evaluation:
    prompt += f"""
## Evaluation Criteria

{evaluation}
"""

  prompt += """
**Simplicity criterion**: All else being equal, simpler is better. A small improvement that adds ugly complexity is not worth it.

**The first run**: Your very first run should always be to establish the baseline.

## Output format

Once the script finishes it prints a summary like this:

```
---
val_bpb:          0.997900
training_seconds: 300.1
total_seconds:    325.9
peak_vram_mb:     45060.2
mfu_percent:      39.80
total_tokens_M:   499.6
num_steps:        953
num_params_M:     50.3
depth:            8
```

## Logging results

When an experiment is done, log it to `results.tsv` (tab-separated, NOT comma-separated).

The TSV has 5 columns: `commit\\tval_bpb\\tmemory_gb\\tstatus\\tdescription`

## Reporting to the collaboration platform

After each experiment, report results to the platform:
```bash
python sync_results.py report \\
  --branch autoresearch/<tag> \\
  --commit <hash> \\
  --val-bpb <value> \\
  --memory-gb <value> \\
  --status <keep|discard|crash> \\
  --description "<what you tried>" \\
  --with-diffs
```

This shares your results with all researchers in real-time.

## The experiment loop

LOOP FOREVER:

1. Look at the git state
2. Tune `train.py` with an experimental idea
3. git commit
4. Run the experiment: `uv run train.py > run.log 2>&1`
5. Extract metrics: `grep "^val_bpb:\\|^peak_vram_mb:" run.log`
6. If crashed, read `tail -n 50 run.log` and attempt fix
7. Record in results.tsv
8. Report to platform (sync_results.py report ...)
9. If improved, keep. Otherwise git reset.

**NEVER STOP**: Once the experiment loop has begun, do NOT pause to ask the human.
"""

  if custom:
    prompt += f"""
## Custom Instructions

{custom}
"""

  return prompt
