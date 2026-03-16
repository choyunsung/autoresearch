#!/usr/bin/env python3
"""
AutoResearch Collab — Python Service Manager

Usage:
  python service.py start       # Start server (daemonized)
  python service.py stop        # Stop server
  python service.py restart     # Restart server
  python service.py status      # Check if running
  python service.py run         # Run in foreground (for debugging)
  python service.py logs        # Tail logs
  python service.py logs error  # Tail error logs
"""

import os
import sys
import signal
import subprocess
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
PID_FILE = BASE_DIR / ".server.pid"
LOG_FILE = BASE_DIR / "logs" / "server.log"
ERR_FILE = BASE_DIR / "logs" / "server.error.log"

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = os.environ.get("PORT", "7891")


def _ensure_dirs():
  (BASE_DIR / "logs").mkdir(exist_ok=True)


def _read_pid() -> int | None:
  if not PID_FILE.exists():
    return None
  try:
    pid = int(PID_FILE.read_text().strip())
    os.kill(pid, 0)  # Check if process exists
    return pid
  except (ValueError, ProcessLookupError, PermissionError):
    PID_FILE.unlink(missing_ok=True)
    return None


def _write_pid(pid: int):
  PID_FILE.write_text(str(pid))


def cmd_start():
  pid = _read_pid()
  if pid:
    print(f"Already running (PID {pid})")
    print(f"  http://localhost:{PORT}")
    return

  _ensure_dirs()

  log_fd = open(LOG_FILE, "a")
  err_fd = open(ERR_FILE, "a")

  proc = subprocess.Popen(
    [
      sys.executable, "-m", "uvicorn",
      "collab.app:app",
      "--host", HOST,
      "--port", PORT,
    ],
    cwd=str(BASE_DIR),
    stdout=log_fd,
    stderr=err_fd,
    start_new_session=True,  # Detach from terminal
  )

  _write_pid(proc.pid)

  # Wait a moment and verify it started
  time.sleep(2)
  if proc.poll() is not None:
    print("Failed to start. Check logs:")
    print(f"  tail {ERR_FILE}")
    PID_FILE.unlink(missing_ok=True)
    return

  print(f"Started (PID {proc.pid})")
  print(f"  http://localhost:{PORT}")
  print(f"  Logs: {LOG_FILE}")


def cmd_stop():
  pid = _read_pid()
  if not pid:
    print("Not running")
    return

  os.kill(pid, signal.SIGTERM)

  # Wait for graceful shutdown
  for _ in range(30):
    try:
      os.kill(pid, 0)
      time.sleep(0.1)
    except ProcessLookupError:
      break
  else:
    os.kill(pid, signal.SIGKILL)

  PID_FILE.unlink(missing_ok=True)
  print(f"Stopped (PID {pid})")


def cmd_restart():
  cmd_stop()
  time.sleep(1)
  cmd_start()


def cmd_status():
  pid = _read_pid()
  if pid:
    print(f"Running (PID {pid})")
    print(f"  http://localhost:{PORT}")
  else:
    print("Stopped")


def cmd_run():
  """Run in foreground (Ctrl+C to stop)."""
  _ensure_dirs()
  print(f"Starting on http://{HOST}:{PORT} (foreground, Ctrl+C to stop)")
  os.chdir(str(BASE_DIR))
  os.execlp(
    sys.executable, sys.executable, "-m", "uvicorn",
    "collab.app:app",
    "--host", HOST,
    "--port", PORT,
    "--reload",
  )


def cmd_logs():
  _ensure_dirs()
  target = ERR_FILE if len(sys.argv) > 2 and sys.argv[2] == "error" else LOG_FILE
  if not target.exists():
    print(f"No log file yet: {target}")
    return
  os.execlp("tail", "tail", "-f", "-n", "50", str(target))


def main():
  if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(1)

  cmd = sys.argv[1]
  commands = {
    "start": cmd_start,
    "stop": cmd_stop,
    "restart": cmd_restart,
    "status": cmd_status,
    "run": cmd_run,
    "logs": cmd_logs,
  }

  if cmd not in commands:
    print(f"Unknown command: {cmd}")
    print(__doc__)
    sys.exit(1)

  commands[cmd]()


if __name__ == "__main__":
  main()
