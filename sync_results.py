#!/usr/bin/env python3
"""
CLI tool to sync local results.tsv to the AutoResearch Collab platform.

Usage:
  # First, get a token:
  python sync_results.py login --username myuser --password mypass --server http://localhost:8000

  # Sync results:
  python sync_results.py sync --branch autoresearch/mar5 [--results results.tsv] [--server http://localhost:8000]

  # Sync with git diff info (captures code changes per commit):
  python sync_results.py sync --branch autoresearch/mar5 --with-diffs
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
  import requests
except ImportError:
  print("Install requests: pip install requests")
  sys.exit(1)

TOKEN_FILE = Path.home() / ".autoresearch-collab-token"
DEFAULT_SERVER = "http://localhost:7891"


def save_token(token: str):
  TOKEN_FILE.write_text(token)
  TOKEN_FILE.chmod(0o600)
  print(f"Token saved to {TOKEN_FILE}")


def load_token() -> str:
  if not TOKEN_FILE.exists():
    print("Not logged in. Run: sync_results.py login --username USER --password PASS")
    sys.exit(1)
  return TOKEN_FILE.read_text().strip()


def cmd_login(args):
  resp = requests.post(f"{args.server}/api/token", data={
    "username": args.username,
    "password": args.password,
  })
  if resp.status_code != 200:
    print(f"Login failed: {resp.text}")
    sys.exit(1)
  token = resp.json()["token"]
  save_token(token)
  print("Login successful!")


def get_git_diff(commit_hash: str) -> str:
  """Get the git diff for a specific commit."""
  try:
    result = subprocess.run(
      ["git", "diff", f"{commit_hash}^..{commit_hash}", "--", "train.py"],
      capture_output=True, text=True, timeout=10,
    )
    return result.stdout[:5000]  # Limit diff size
  except Exception:
    return ""


def get_hyperparams_from_commit(commit_hash: str) -> dict:
  """Extract hyperparameters from train.py at a specific commit."""
  try:
    result = subprocess.run(
      ["git", "show", f"{commit_hash}:train.py"],
      capture_output=True, text=True, timeout=10,
    )
    params = {}
    for line in result.stdout.split("\n"):
      line = line.strip()
      # Match lines like: DEPTH = 8
      if "=" in line and line[0].isupper() and not line.startswith("#"):
        parts = line.split("=", 1)
        key = parts[0].strip()
        val = parts[1].split("#")[0].strip()  # Remove inline comments
        if key in (
          "ASPECT_RATIO", "HEAD_DIM", "WINDOW_PATTERN", "TOTAL_BATCH_SIZE",
          "EMBEDDING_LR", "UNEMBEDDING_LR", "MATRIX_LR", "SCALAR_LR",
          "WEIGHT_DECAY", "ADAM_BETAS", "WARMUP_RATIO", "WARMDOWN_RATIO",
          "DEPTH", "DEVICE_BATCH_SIZE", "FINAL_LR_FRAC",
        ):
          params[key] = val
    return params
  except Exception:
    return {}


def parse_results_tsv(path: Path) -> list[dict]:
  """Parse results.tsv into list of experiment dicts."""
  experiments = []
  with open(path) as f:
    header = f.readline().strip().split("\t")
    for line in f:
      parts = line.strip().split("\t")
      if len(parts) < 5:
        continue
      experiments.append({
        "commit_hash": parts[0],
        "val_bpb": float(parts[1]),
        "memory_gb": float(parts[2]),
        "status": parts[3],
        "description": parts[4],
      })
  return experiments


def cmd_sync(args):
  token = load_token()
  results_path = Path(args.results)
  if not results_path.exists():
    print(f"File not found: {results_path}")
    sys.exit(1)

  experiments = parse_results_tsv(results_path)
  print(f"Found {len(experiments)} experiments in {results_path}")

  # Enrich with git data if requested
  if args.with_diffs:
    print("Enriching with git diffs and hyperparameters...")
    for exp in experiments:
      exp["code_diff"] = get_git_diff(exp["commit_hash"])
      exp["hyperparams_json"] = json.dumps(get_hyperparams_from_commit(exp["commit_hash"]))

  # Batch sync
  payload = {
    "branch_name": args.branch,
    "experiments": experiments,
  }

  resp = requests.post(
    f"{args.server}/api/experiments/sync",
    json=payload,
    headers={"Authorization": f"Bearer {token}"},
  )

  if resp.status_code != 200:
    print(f"Sync failed: {resp.text}")
    sys.exit(1)

  result = resp.json()
  print(f"Sync complete: {result['created']} new, {result['skipped']} skipped (duplicates)")


def cmd_report(args):
  """Post a single experiment result (used by modified train.py)."""
  token = load_token()

  payload = {
    "branch_name": args.branch,
    "commit_hash": args.commit,
    "val_bpb": args.val_bpb,
    "memory_gb": args.memory_gb,
    "status": args.status,
    "description": args.description,
    "training_seconds": args.training_seconds or 0,
    "mfu_percent": args.mfu_percent or 0,
    "total_tokens_m": args.total_tokens_m or 0,
    "num_steps": args.num_steps or 0,
    "num_params_m": args.num_params_m or 0,
    "depth": args.depth or 0,
  }

  if args.with_diffs:
    payload["code_diff"] = get_git_diff(args.commit)
    payload["hyperparams_json"] = json.dumps(get_hyperparams_from_commit(args.commit))

  resp = requests.post(
    f"{args.server}/api/experiments",
    json=payload,
    headers={"Authorization": f"Bearer {token}"},
  )

  if resp.status_code != 200:
    print(f"Report failed: {resp.text}")
    sys.exit(1)

  print(f"Experiment reported: {resp.json()}")


def main():
  parser = argparse.ArgumentParser(description="AutoResearch Collab sync tool")
  sub = parser.add_subparsers(dest="command")

  # login
  login_p = sub.add_parser("login")
  login_p.add_argument("--username", required=True)
  login_p.add_argument("--password", required=True)
  login_p.add_argument("--server", default=DEFAULT_SERVER)

  # sync
  sync_p = sub.add_parser("sync")
  sync_p.add_argument("--branch", required=True, help="Branch name (e.g. autoresearch/mar5)")
  sync_p.add_argument("--results", default="results.tsv", help="Path to results.tsv")
  sync_p.add_argument("--server", default=DEFAULT_SERVER)
  sync_p.add_argument("--with-diffs", action="store_true", help="Include git diffs")

  # report (single experiment)
  report_p = sub.add_parser("report")
  report_p.add_argument("--branch", required=True)
  report_p.add_argument("--commit", required=True)
  report_p.add_argument("--val-bpb", type=float, required=True)
  report_p.add_argument("--memory-gb", type=float, default=0)
  report_p.add_argument("--status", required=True, choices=["keep", "discard", "crash"])
  report_p.add_argument("--description", required=True)
  report_p.add_argument("--training-seconds", type=float)
  report_p.add_argument("--mfu-percent", type=float)
  report_p.add_argument("--total-tokens-m", type=float)
  report_p.add_argument("--num-steps", type=int)
  report_p.add_argument("--num-params-m", type=float)
  report_p.add_argument("--depth", type=int)
  report_p.add_argument("--with-diffs", action="store_true")
  report_p.add_argument("--server", default=DEFAULT_SERVER)

  args = parser.parse_args()
  if args.command == "login":
    cmd_login(args)
  elif args.command == "sync":
    cmd_sync(args)
  elif args.command == "report":
    cmd_report(args)
  else:
    parser.print_help()


if __name__ == "__main__":
  main()
