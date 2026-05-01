# `telemetry-backend/` — server-side stack for the sandwich pipeline

The pipeline's API for emitting telemetry lives in `pipeline/pipe/telemetry/` 
and runs on artist workstations. This directory holds everything that runs 
on the *receive* side: the Postgres database that stores events, the Grafana
instance that displays them, the ingester service that bridges the JSONL
spool to Postgres, and the systemd timers that schedule the Tractor poll,
render harvest, and storage scan.

## Layout

```
telemetry-backend/
├── README.md                            # this file
├── grafana/
│   ├── dashboards/
│   │   ├── operational.json             # TD-on-call dashboard
│   │   └── retrospective.json           # end-of-show / capstone dashboard
│   └── provisioning/
│       ├── dashboards/sandwich.yaml     # tells Grafana to load dashboards/ on startup
│       └── datasources/postgres.yaml    # Postgres datasource definition
├── postgres/
│   └── schema.sql                       # CREATE TABLE statements
└── systemd/
    ├── sandwich-telemetry-ingester.service
    ├── sandwich-tractor-poll.{service,timer}
    ├── sandwich-render-harvest.{service,timer}
    └── sandwich-storage-scan.{service,timer}
```

## First-time install (after the host is provisioned)

1. Install `postgresql`, `grafana` (RPM, **not** Docker — the lab GLIBC is
   too old for the Docker image), and `python3.11`.
2. Create the service user:
   ```sh
   sudo useradd --system --no-create-home sandwich-telemetry
   ```
3. Create the database and apply the schema:
   ```sh
   sudo -u postgres createuser sandwich-telemetry
   sudo -u postgres createdb -O sandwich-telemetry sandwich_telemetry
   sudo -u postgres psql sandwich_telemetry < telemetry-backend/postgres/schema.sql
   ```
4. Mount the show share at `/mnt/show` (read-only is fine).
5. Install the pipeline at `/opt/sandwich-pipeline/` (the systemd units expect
   it there). The ingester runs as `python -m pipe.telemetry.ingester` from
   that install.
6. Copy and enable the services:
   ```sh
   sudo cp telemetry-backend/systemd/*.service telemetry-backend/systemd/*.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now sandwich-telemetry-ingester.service
   sudo systemctl enable --now sandwich-tractor-poll.timer
   sudo systemctl enable --now sandwich-render-harvest.timer
   sudo systemctl enable --now sandwich-storage-scan.timer
   ```
7. Edit each unit file's `Environment=` lines if your mount paths differ.
8. Provision Grafana — point its `provisioning_path` at
   `telemetry-backend/grafana/provisioning/`. Grafana picks up the datasource
   and dashboards on startup.

## Giving CSRs a small install bundle

CSRs don't need the whole pipeline repo to install this. Generate a tarball
containing only this directory:

```sh
git archive --output=sandwich-telemetry-backend.tar.gz HEAD:telemetry-backend/
```

The tarball has no Python and no pipeline code — just SQL, systemd units,
Grafana config, and this README. The ingester itself ships separately as part
of the pipeline install at step 5 above.

## Local Development

For local dashboard validation, you don't need systemd or the install layout.
Run the ingester from a checkout:

```sh
# 1. Generate synthetic events
PYTHONPATH=pipeline:tests uv run python -m tests.telemetry.synthesize_events \
    --out /tmp/poc-spool

# 2. Apply schema to a local Postgres
psql -d sandwich_telemetry_poc < telemetry-backend/postgres/schema.sql

# 3. Run the ingester once
PIPE_INGESTER_SPOOL_ROOT=/tmp/poc-spool \
PIPE_INGESTER_DB_DSN=postgresql://localhost/sandwich_telemetry_poc \
PYTHONPATH=pipeline uv run python -m pipe.telemetry.ingester --once

# 4. Point Grafana at telemetry-backend/grafana/provisioning/, open the dashboards
```
