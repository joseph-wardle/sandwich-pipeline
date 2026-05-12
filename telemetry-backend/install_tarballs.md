# One-time tarball setup for the local telemetry stack

The orchestrator (`python -m pipe.telemetry up`) expects pre-extracted
Postgres and Grafana binaries under `/groups/sandwich/.tools/`. This is a
one-time setup; once installed, every lab machine that mounts the share
can run the stack with no further setup.

You only need to do this once per show, from any lab machine. No root,
no CSRs.

## Target layout

`.tools/` is a sibling of `05_production/`, not under it. The orchestrator
resolves it as `production_path.parent / ".tools"`. The `uv` install the
team already maintains lives at the same level.

```
/groups/sandwich/
├── .tools/
│   ├── uv/                          (existing)
│   ├── postgres/                    (NEW; PG 18 binaries)
│   │   ├── bin/                     # postgres, pg_ctl, psql, pg_isready, initdb, ...
│   │   ├── lib/
│   │   └── share/
│   └── grafana/                     (NEW; Grafana OSS 13 binaries)
│       ├── bin/                     # grafana (and grafana-cli on 11.x; the legacy grafana-server is absent on 12+)
│       ├── conf/                    # defaults.ini
│       ├── public/
│       └── plugins-bundled/
└── 05_production/
    └── .telemetry/                  # spool + pg_data + grafana state
```

## Postgres

The `theseus-rs/postgresql-binaries` GitHub release ships clean,
relocatable Postgres builds for Linux x86-64. They are ordinary `.tar.gz`
artifacts — no license token, no installer.

**Tested-working version: `18.3.0`.** You can also browse
<https://github.com/theseus-rs/postgresql-binaries/releases> for newer
tags; pick the `MAJOR.MINOR.PATCH` form (e.g. `17.9.0`, `18.3.0`) — the
URLs require all three components. PG 16, 17, and 18 all work with this
schema.

Each curl is on a single line so trailing whitespace can't break a line
continuation. The `rm -rf` + `mkdir` before `tar` is deliberate: it makes
re-running the block idempotent and avoids `mv`-into-existing-dir
mistakes (where a leftover empty `postgres/` from a previous failed
attempt would cause the extract to land at `postgres/postgres-extract/`
instead of `postgres/`).

The block is wrapped in a subshell with `set -e` so that if the leading
`cd` fails for any reason (path missing, typo, permissions), the rest
of the commands abort instead of silently extracting a 600 MB install
tree into your current directory.

```sh
( set -e
  cd /groups/sandwich/.tools

  PG_VERSION=18.3.0
  PG_URL="https://github.com/theseus-rs/postgresql-binaries/releases/download/${PG_VERSION}/postgresql-${PG_VERSION}-x86_64-unknown-linux-gnu.tar.gz"

  curl -fL -o postgres.tar.gz "$PG_URL"
  rm -rf postgres
  mkdir postgres
  tar -xzf postgres.tar.gz -C postgres --strip-components=1
  rm postgres.tar.gz

  # Sanity check: should print "postgres (PostgreSQL) 18.3" or similar.
  ./postgres/bin/postgres --version
)
```

## Grafana OSS

Grafana ships a self-contained Linux tarball directly from `dl.grafana.com`.
No license token.

**Tested-working version: `13.0.1`.** Earlier 11.x and 12.x releases also
work with the dashboards in this directory; the orchestrator invokes
`bin/grafana server` (the unified subcommand introduced in 12.x). Browse
<https://grafana.com/grafana/download?platform=linux&edition=oss> if you
want a different version. The version string is `MAJOR.MINOR.PATCH`
(e.g. `11.6.0`, `13.0.1`).

```sh
( set -e
  cd /groups/sandwich/.tools

  GF_VERSION=11.6.0
  GF_URL="https://dl.grafana.com/oss/release/grafana-${GF_VERSION}.linux-amd64.tar.gz"

  curl -fL -o grafana.tar.gz "$GF_URL"
  rm -rf grafana
  mkdir grafana
  tar -xzf grafana.tar.gz -C grafana --strip-components=1
  rm grafana.tar.gz

  # Sanity check: should print Grafana's version banner.
  ./grafana/bin/grafana --version
)
```

## First boot

```sh
cd <pipeline-checkout>
uv sync --dev
PYTHONPATH=src uv run python -m core.telemetry up
```

`PYTHONPATH=src` is required because the repo isn't declared as an
installable package — `core.telemetry` lives at `src/core/telemetry/`
and can't be found on `sys.path` without it.

On first boot the orchestrator runs `initdb` against
`/groups/sandwich/05_production/.telemetry/pg_data/`, creates the
`sandwich_telemetry` database, applies `telemetry-backend/postgres/schema.sql`,
and provisions the Grafana datasource and `tool_health` dashboard. Subsequent
boots skip the init steps and reuse the existing data.

## Updating the binaries later

To update Postgres or Grafana, replace the directory contents in place
while the orchestrator is **down** (verify with `python -m pipe.telemetry status`).
A Postgres major-version upgrade is more involved (`pg_upgrade` against the
old `pg_data`); coordinate before bumping past 16.x.


