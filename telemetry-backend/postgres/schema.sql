-- Telemetry database schema for the sandwich-pipeline.
--
-- One wide events table holds every event type the pipeline emits. Stable
-- envelope columns are typed and indexed; event-specific fields live in
-- payload (JSONB) so adding a new event type requires zero DDL.
--
-- The ingester service reads JSONL files from the shared spool and inserts
-- into events. It maintains a single row per (hostname, host_user) in
-- ingester_status to track the read offset so it can resume cleanly.

CREATE TABLE IF NOT EXISTS events (
    event_id          UUID PRIMARY KEY,
    occurred_at       TIMESTAMPTZ NOT NULL,
    event_type        TEXT NOT NULL,
    status            TEXT NOT NULL,
    dcc               TEXT,
    host_user         TEXT,
    hostname          TEXT,
    action_id         UUID,
    scope_show        TEXT,
    scope_sequence    TEXT,
    scope_shot        TEXT,
    scope_asset       TEXT,
    scope_department  TEXT,
    duration_ms       INTEGER,
    error_code        TEXT,
    error_message     TEXT,
    payload           JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Time + type is the dominant filter for almost every dashboard panel.
CREATE INDEX IF NOT EXISTS events_time_type
    ON events (occurred_at DESC, event_type);

-- The retrospective audience filters by shot and asset.
CREATE INDEX IF NOT EXISTS events_scope_shot
    ON events (scope_shot)
    WHERE scope_shot IS NOT NULL;

CREATE INDEX IF NOT EXISTS events_scope_asset
    ON events (scope_asset)
    WHERE scope_asset IS NOT NULL;

-- Error panels filter on (event_type, error_code) over time.
CREATE INDEX IF NOT EXISTS events_status_error
    ON events (event_type, error_code)
    WHERE status = 'error';

-- The "events per hour by hostname" health panel.
CREATE INDEX IF NOT EXISTS events_hostname_time
    ON events (hostname, occurred_at DESC);

-- The "failed_tool" cross-event-type panel uses an expression index so the
-- query plan stays sharp without committing the field to a typed column.
CREATE INDEX IF NOT EXISTS events_failed_tool
    ON events ((payload ->> 'failed_tool'))
    WHERE status = 'error' AND payload ? 'failed_tool';


-- Ingester progress per workstation spool. The "ingester lag" Grafana panel
-- queries this table directly: now() - max(last_event_at) tells you how far
-- behind the ingester is from real-time.
CREATE TABLE IF NOT EXISTS ingester_status (
    spool_path        TEXT PRIMARY KEY,
    last_jsonl_file   TEXT,
    last_byte_offset  BIGINT NOT NULL DEFAULT 0,
    last_event_at     TIMESTAMPTZ,
    last_read_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    events_inserted   BIGINT NOT NULL DEFAULT 0,
    events_rejected   BIGINT NOT NULL DEFAULT 0
);
