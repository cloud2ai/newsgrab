#!/bin/sh
set -eu

# UVICORN_RELOAD=true (set by docker-compose.dev.yml) enables uvicorn's
# autoreload, watching /app/app -- the directory docker-compose.dev.yml
# bind-mounts the host's collector-service/app over, so edits on the host
# take effect without rebuilding or restarting the container. Unset in the
# production compose file, so normal deployments are unaffected.
if [ "${UVICORN_RELOAD:-}" = "true" ]; then
  exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir /app/app
else
  exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
fi
