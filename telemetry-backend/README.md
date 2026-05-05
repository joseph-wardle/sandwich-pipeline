# `telemetry-backend/` — server-side stack for the sandwich pipeline

The pipeline's API for emitting telemetry lives in `pipeline/pipe/telemetry/`
and runs on artist workstations. This directory holds everything that runs
on the *receive* side: the Postgres database that stores events, the Grafana
instance that displays them, and the ingester that bridges the JSONL spool
to Postgres.

The whole stack runs as three containers under one `docker-compose.yaml`.
There are no systemd units to install and no managed-host dependency — the
host just needs Docker and a mount of the same NFS path that workstations
write the spool to.

## Layout

```
telemetry-backend/
├── README.md                            # this file
├── docker-compose.yaml                  # postgres + ingester + grafana
├── Dockerfile.ingester                  # builds the ingester image
├── grafana/
│   ├── dashboards/
│   │   └── tool_health.json             # errors and per-user activity
│   └── provisioning/
│       ├── dashboards/sandwich.yaml     # tells Grafana to load dashboards/ on startup
│       └── datasources/postgres.yaml    # Postgres datasource definition
└── postgres/
    └── schema.sql                       # CREATE TABLE statements (auto-applied on first start)
```

## First-time install

1. Pick a host. Anything with Docker and a mount of the show's NFS spool
   path works — a lab workstation, a small VM, or your own machine while
   the system is small.
2. Install Docker (with Compose v2).
3. Mount the share so the host sees the same spool path the workstations
   write to (e.g. `/mnt/show/.telemetry/raw`).
4. Create a `.env` file next to `docker-compose.yaml`:
   ```sh
   POSTGRES_PASSWORD=<a strong password>
   GF_SECURITY_ADMIN_PASSWORD=<a strong password>
   PIPE_INGESTER_SPOOL_HOST_PATH=/mnt/show/.telemetry/raw
   GRAFANA_HOST_PORT=3000
   ```
5. Bring the stack up:
   ```sh
   cd telemetry-backend
   docker compose up -d
   ```
   The Postgres container applies `postgres/schema.sql` on its first start
   (the schema file is mounted as a `docker-entrypoint-initdb.d` script).
   The ingester container starts tailing the spool. Grafana auto-loads the
   datasource and the `tool_health` dashboard.
6. Open Grafana at `http://<host>:3000`, log in as `admin` with the
   password from `.env`, and the **Sandwich Pipeline → Tool health**
   dashboard is live.

## Day-to-day operations

```sh
# Tail the ingester
docker compose logs -f ingester

# Confirm rows are landing
docker compose exec postgres psql -U sandwich-telemetry -d sandwich_telemetry \
    -c "SELECT host_user, event_type, status, occurred_at FROM events ORDER BY occurred_at DESC LIMIT 20;"

# Restart Grafana after editing a dashboard JSON
docker compose restart grafana

# Stop everything
docker compose down
```

## Local development

For dashboard work without leaving your laptop, run the same compose stack
with `PIPE_INGESTER_SPOOL_HOST_PATH` pointing at a local directory and feed
it synthetic events:

```sh
# 1. Generate synthetic events into the local spool path
PYTHONPATH=pipeline:tests uv run python -m tests.telemetry.synthesize_events \
    --out /tmp/poc-spool

# 2. Bring the stack up against that path
PIPE_INGESTER_SPOOL_HOST_PATH=/tmp/poc-spool \
POSTGRES_PASSWORD=devpw \
GF_SECURITY_ADMIN_PASSWORD=devpw \
docker compose up -d

# 3. Point your browser at http://localhost:3000
```

## When to put this on a managed host

If and when telemetry becomes load-bearing for production decisions —
typically when the pollers come back and we depend on continuous data —
that's the moment to ask CSRs to stand up Postgres and Grafana on a
managed machine. Until then, this self-hosted stack is the right size.
