# 10 — Deployment / Runtime

## Recommended local architecture

```text
web frontend
API
worker/orchestrator
database
artifact store
sandbox manager
tool runner containers
optional model gateway
```

## Development
- Frontend: keep existing stack if stable (e.g. Next.js/React)
- Backend: keep existing backend if stable (e.g. FastAPI)
- DB: SQLite acceptable for local prototype; design migration path to Postgres
- Artifact Store: filesystem first, S3-compatible later
- Queue: in-process/dev; durable queue when concurrent runs matter

## Sandbox
Each run/tool receives:
- filesystem quota
- time limit
- CPU/memory limit
- network policy
- environment allowlist

## Observability
Record:
- run/task duration
- tool crashes/timeouts
- token usage
- cost
- findings by verification state
- coverage
- cache hits
- recovery events
