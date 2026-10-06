#!/bin/zsh
set -eu
cd "${0:A:h}"
exec .venv/bin/python -m uvicorn server:app --host 0.0.0.0 --port 8768 --no-access-log --timeout-graceful-shutdown 2
