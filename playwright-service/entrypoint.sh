#!/bin/sh
set -eu

# `docker restart` (or any restart that doesn't recreate the container)
# reuses the same filesystem, so a lock file left behind by the previous
# Xvfb/Chromium process is still there when this script runs again --
# observed in practice: Xvfb refused to (re)bind display :99 ("Server is
# already active for display 99"), which cascaded into Chromium failing
# to find a display and the whole container never becoming healthy again
# until it was recreated from scratch. Clear both proactively so a plain
# restart is as reliable as a fresh container.
rm -f /tmp/.X99-lock
rm -f /tmp/chrome-data/Singleton*

Xvfb :99 -screen 0 1920x1080x24 &

# Chrome does not read HTTP_PROXY/HTTPS_PROXY the way curl/requests do -- it
# needs an explicit --proxy-server flag, or every navigation to a host behind
# the proxy fails with net::ERR_PROXY_CONNECTION_FAILED. Check both upper-
# and lower-case forms since Docker/Compose commonly injects the lowercase
# one, matching the same convention already used by newshub's
# articlehub/collectors/browserless_client.py.
PROXY="${HTTPS_PROXY:-${HTTP_PROXY:-${https_proxy:-${http_proxy:-}}}}"

set -- \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-data \
  --disable-blink-features=AutomationControlled \
  --disable-background-timer-throttling \
  --disable-backgrounding-occluded-windows \
  --disable-renderer-backgrounding \
  --no-sandbox \
  --disable-dev-shm-usage \
  --disable-web-security \
  --mute-audio \
  --disable-features=IsolateOrigins,site-per-process

if [ -n "$PROXY" ]; then
  echo "Using proxy for Chromium: $PROXY"
  set -- "$@" "--proxy-server=$PROXY"
fi

chromium "$@" about:blank &

echo "Waiting for Chromium CDP endpoint on :9222..."
i=1
while [ "$i" -le 30 ]; do
  if curl -sf "http://localhost:9222/json/version" > /dev/null 2>&1; then
    echo "Chromium CDP is ready"
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "Chromium CDP did not become ready within 30s" >&2
    exit 1
  fi
  i=$((i + 1))
  sleep 1
done

# UVICORN_RELOAD=true (set by docker-compose.dev.yml) enables uvicorn's
# autoreload, watching /app/app -- the directory docker-compose.dev.yml
# bind-mounts the host's playwright-service/app over, so edits on the host
# take effect without rebuilding or restarting the container (Chromium/Xvfb
# above are unaffected by a reload, only the FastAPI process restarts).
# Unset in the production compose file, so normal deployments are
# unaffected.
if [ "${UVICORN_RELOAD:-}" = "true" ]; then
  exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir /app/app
else
  exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
fi
