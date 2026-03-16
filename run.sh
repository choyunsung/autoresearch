#!/bin/bash
# AutoResearch Collab — shortcut for service.py
# Usage: ./run.sh [start|stop|restart|status|run|logs]
cd "$(dirname "$0")"
PYENV_VERSION=3.11.13 python service.py "${1:-run}"
