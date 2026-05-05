# `telemetry-backend/` — server-side stack for the sandwich pipeline

The pipeline's API for emitting telemetry lives in `pipeline/pipe/telemetry/`
and runs on artist workstations. This directory holds the receive side: the
Postgres schema, Grafana provisioning, and the dashboard JSON. The
**orchestrator** that boots Postgres and Grafana lives at
`pipeline/pipe/telemetry/local_stack.py`.

The whole stack is designed to run from any lab machine, with all state on
the production share. It is not a long-running service; you boot it
when you want to look at telemetry and shut it down when you're done.
The next person to boot it ingests every event that arrived in the spool in
the meantime.

## Requirements

- `uv`-managed pipeline checkout with the `dev` group installed (the
  ingester needs `psycopg`, which is in `pyproject.toml`'s `dev` group).
- Postgres 16 and Grafana OSS 10 binaries pre-extracted under
  `/groups/sandwich/05_production/.tools/postgres/` and `…/.tools/grafana/`.
  See **`install_tarballs.md`** in this directory for the one-time setup.
- Read/write access to `/groups/sandwich/05_production/.telemetry/` (where
  the spool, `pg_data/`, Grafana state, and the orchestrator lock live).

## Layout

```
telemetry-backend/
├── README.md                            # this file
├── install_tarballs.md                  # one-time .tools/ setup
├── grafana/
│   ├── dashboards/
│   │   └── tool_health.json             # errors and per-user activity
│   └── provisioning/
│       ├── dashboards/sandwich.yaml     # tells Grafana to load dashboards/ on startup
│       └── datasources/postgres.yaml    # Postgres datasource (env-substituted)
└── postgres/
    └── schema.sql                       # CREATE TABLE statements
```

State on the production share, owned by the orchestrator:

```
/groups/sandwich/05_production/.telemetry/
├── raw/<host>/<user>/*.jsonl    # workstation spool (existing)
├── pg_data/                     # Postgres data directory; initdb on first up
├── grafana/{data,log}/          # Grafana sessions / logs
├── pg.log                       # Postgres server log
└── locks/orchestrator.lock      # exclusive flock; one orchestrator across all hosts
```

## Day-to-day workflow

```sh
# Bring the stack up. Foreground; ^C when done.
python -m pipe.telemetry up

# One-shot ingest (no Grafana). Useful from a CI box or for a quick catch-up.
python -m pipe.telemetry catch-up

# Find out whether the stack is up and on which host.
python -m pipe.telemetry status
```

`up` prints something like:

```
telemetry stack up:
  postgres   127.0.0.1:55432  data=/groups/sandwich/05_production/.telemetry/pg_data
  grafana    http://cs-1017454.cs.byu.edu:3001
  spool      /groups/sandwich/05_production/.telemetry/raw
  log        /groups/sandwich/05_production/.telemetry/pg.log
press ^C to stop
```

Open the Grafana URL, log in (default `admin` / `admin` on first boot;
Grafana forces a password change), and the **Sandwich Pipeline → Tool
health** dashboard is live.

## Concurrency model

- `pipe telemetry up` acquires an exclusive `flock` on
  `/groups/sandwich/05_production/.telemetry/locks/orchestrator.lock` for
  its whole lifetime. A second `up` on any lab machine fails fast with the
  current holder's hostname, PID, and start time.
- Workstations writing JSONL events to the spool never touch Postgres, so
  there is no writer conflict against the database while the orchestrator
  is down — events accumulate in the spool and the next `up` (anywhere)
  catches up.
- If the lock file is present but no process holds the flock (e.g. an
  orchestrator was hard-killed), `pipe telemetry status` reports
  it as stale and the next `up` will succeed and overwrite the file.

## Notes on Postgres data on NFS

Postgres' data directory living on an NFS share is officially unsupported.
With the single-writer guarantee enforced by the orchestrator's flock, the
real risk reduces to durability under host crashes during fsync. For an
occasionally-booted analytics database the trade-off is acceptable, and
the workstation spool — which is independent and append-only — remains
the source of truth for any event that hasn't been ingested yet.

If telemetry ever becomes load-bearing for production decisions
(typically when the pollers come back), that's the moment to ask CSRs to
stand up Postgres and Grafana on a managed machine. Until then, this
self-orchestrated stack is the right size.

## Local development against a laptop spool

For dashboard work without leaving your laptop, override the spool dir and
the production root via env vars:

```sh
# 1. Generate synthetic events into a local dir
PYTHONPATH=pipeline:tests uv run python -m tests.telemetry.synthesize_events \
    --out /tmp/sandwich-poc-spool

# 2. Bring the stack up against that path. The orchestrator reads the
#    backend dir from pipeline.shared.util; you can point it at a temp
#    location by overriding production_path in pipeline/env.py for your
#    local checkout.
python -m pipe.telemetry up
```
