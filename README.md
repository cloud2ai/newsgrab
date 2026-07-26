# newsgrab

A standalone, reusable content-collection service. Not tied to any single
consuming project.

## Architecture

Two containers, communicating over an internal docker network only (no
public exposure, no application-layer auth -- see each service's own
README for details):

- **`playwright-service`**: stealth-configured browser automation
  (Xvfb + Chromium + Playwright), exposed via a generic action-dispatch
  HTTP API. See `playwright-service/README.md`.
- **`collector-service`**: pluggable content-collection backends (Google
  News first) with an async job API for callers. Calls `playwright-service`
  internally. See `collector-service/README.md`.

## Design

`docs/superpowers/specs/2026-07-26-newsgrab-design.md`

## Running

```bash
docker compose up -d
```
