"""Local telemetry stack orchestrator.

Boots Postgres, Grafana, and the ingester from any lab machine that mounts
the production share, with all state living on the share. State is durable
across boots: any machine that brings the stack up next ingests events that
arrived in the spool since the last shutdown.

Run from the pipeline checkout root (the repo isn't declared as an
installable package, so `pipe` has to go on `PYTHONPATH`):

    PYTHONPATH=pipeline uv run python -m core.telemetry up        # ^C to stop
    PYTHONPATH=pipeline uv run python -m core.telemetry catch-up  # one-shot ingest
    PYTHONPATH=pipeline uv run python -m core.telemetry status    # who holds the lock

Concurrency: the orchestrator holds an exclusive `flock` on
``<production>/.telemetry/locks/orchestrator.lock`` for its whole lifetime.
A second orchestrator on any host fails fast with the holder's identity.
Inside that lock, only one Postgres instance points at ``pg_data/``.

This file is the only place that knows about the ``.tools/postgres`` and
``.tools/grafana`` tarball layout — see ``telemetry-backend/install_tarballs.md``
for the one-time extraction.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.util.util import (
    get_production_path,
    get_shared_telemetry_backend_dir,
)

_LOG = logging.getLogger("core.telemetry.local_stack")

_DEFAULT_PG_PORT = 55432
_DEFAULT_GRAFANA_PORT = 3001
_DB_NAME = "sandwich_telemetry"
_DB_USER = "sandwich-telemetry"

_REPO_TELEMETRY_BACKEND = Path(__file__).resolve().parents[3] / "telemetry-backend"


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StackPaths:
    """Where everything the orchestrator touches lives."""

    backend: Path
    pg_data: Path
    pg_log: Path
    grafana_data: Path
    grafana_log: Path
    grafana_dashboards: Path
    grafana_provisioning: Path
    locks_dir: Path
    lock_file: Path
    spool_root: Path
    schema_sql: Path
    pg_bin: Path
    grafana_homepath: Path


def _resolve_paths() -> StackPaths:
    backend = get_shared_telemetry_backend_dir()
    # `.tools/` is a sibling of the production_path (it sits at the show root,
    # next to 05_production), not under it. Matches the existing `.tools/uv/`.
    tools = get_production_path().parent / ".tools"
    return StackPaths(
        backend=backend,
        pg_data=backend / "pg_data",
        pg_log=backend / "pg.log",
        grafana_data=backend / "grafana" / "data",
        grafana_log=backend / "grafana" / "log",
        grafana_dashboards=_REPO_TELEMETRY_BACKEND / "grafana" / "dashboards",
        grafana_provisioning=_REPO_TELEMETRY_BACKEND / "grafana" / "provisioning",
        locks_dir=backend / "locks",
        lock_file=backend / "locks" / "orchestrator.lock",
        spool_root=backend / "raw",
        schema_sql=_REPO_TELEMETRY_BACKEND / "postgres" / "schema.sql",
        pg_bin=tools / "postgres" / "bin",
        grafana_homepath=tools / "grafana",
    )


def _check_tarballs(paths: StackPaths) -> None:
    """Fail with a clear message if the .tools binaries are missing."""

    missing: list[str] = []
    if not (paths.pg_bin / "postgres").exists():
        missing.append(f"  Postgres: {paths.pg_bin / 'postgres'}")
    if not (paths.grafana_homepath / "bin" / "grafana").exists():
        missing.append(f"  Grafana:  {paths.grafana_homepath / 'bin' / 'grafana'}")
    if missing:
        raise SystemExit(
            "Telemetry stack binaries are not installed:\n"
            + "\n".join(missing)
            + "\nSee telemetry-backend/install_tarballs.md for the one-time setup."
        )
    if not paths.schema_sql.exists():
        raise SystemExit(
            f"Schema not found at {paths.schema_sql}. "
            "The pipeline checkout must include telemetry-backend/."
        )


# ---------------------------------------------------------------------------
# Lock acquisition
# ---------------------------------------------------------------------------


def _acquire_lock(lock_path: Path) -> int:
    """Acquire an exclusive flock on `lock_path`. Returns the open fd.

    Caller must keep the fd open for the whole orchestrator lifetime; close
    on shutdown to release the lock. Raises SystemExit with the holder's
    identity if another orchestrator already holds it.
    """

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        try:
            existing = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        host = existing.get("host", "unknown")
        pid = existing.get("pid", "unknown")
        started_at = existing.get("started_at", "unknown")
        raise SystemExit(
            f"Telemetry stack is already running on host {host} "
            f"(pid {pid}, started {started_at}).\n"
            f"Lock: {lock_path}"
        ) from None

    info = {
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    payload = json.dumps(info, indent=2).encode("utf-8")
    os.lseek(fd, 0, 0)
    os.ftruncate(fd, 0)
    os.write(fd, payload)
    os.fsync(fd)
    return fd


def _read_lock_info(lock_path: Path) -> dict[str, str] | None:
    if not lock_path.exists():
        return None
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Postgres lifecycle
# ---------------------------------------------------------------------------


def _enforce_pg_data_perms(paths: StackPaths) -> None:
    """Force `pg_data` to mode 0700 before every Postgres start.

    Postgres refuses to start unless `pg_data` is 0700 or 0750. The
    production share's parent dirs are 0770 (group-rwx), and that mode
    silently propagates to `pg_data` between boots — observed both at
    initdb time and after the directory has been sitting on the share
    while the stack is down. Re-chmodding on every up is cheap and the
    only way to keep the next `pipe telemetry up` from failing with a
    confusing FATAL log line.
    """

    paths.pg_data.parent.mkdir(parents=True, exist_ok=True)
    paths.pg_data.mkdir(mode=0o700, exist_ok=True)
    paths.pg_data.chmod(0o700)


def _ensure_pg_initialized(paths: StackPaths) -> bool:
    """Run `initdb` if the data dir is empty. Returns True on first init."""

    version_file = paths.pg_data / "PG_VERSION"
    if version_file.exists():
        return False

    _LOG.info("initializing postgres data dir at %s", paths.pg_data)
    subprocess.run(
        [
            str(paths.pg_bin / "initdb"),
            "-D",
            str(paths.pg_data),
            "-U",
            _DB_USER,
            "--auth-host=trust",
            "--auth-local=trust",
            "--encoding=UTF8",
        ],
        check=True,
    )
    return True


def _start_postgres(paths: StackPaths, port: int) -> None:
    paths.pg_log.parent.mkdir(parents=True, exist_ok=True)
    _LOG.info("starting postgres on 127.0.0.1:%d (data=%s)", port, paths.pg_data)
    subprocess.run(
        [
            str(paths.pg_bin / "pg_ctl"),
            "-D",
            str(paths.pg_data),
            "-l",
            str(paths.pg_log),
            "-o",
            f"-p {port} -h 127.0.0.1",
            "-w",
            "start",
        ],
        check=True,
    )


def _stop_postgres(paths: StackPaths) -> None:
    if not (paths.pg_data / "postmaster.pid").exists():
        return
    _LOG.info("stopping postgres")
    subprocess.run(
        [
            str(paths.pg_bin / "pg_ctl"),
            "-D",
            str(paths.pg_data),
            "-m",
            "fast",
            "stop",
        ],
        check=False,
    )


def _wait_pg_ready(paths: StackPaths, port: int, timeout_seconds: int = 30) -> None:
    pg_isready = paths.pg_bin / "pg_isready"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = subprocess.run(
            [str(pg_isready), "-h", "127.0.0.1", "-p", str(port), "-U", _DB_USER],
            capture_output=True,
        )
        if result.returncode == 0:
            return
        time.sleep(0.5)
    raise SystemExit(
        f"Postgres did not become ready on 127.0.0.1:{port} within "
        f"{timeout_seconds}s. Check {paths.pg_log}."
    )


def _ensure_database_and_schema(paths: StackPaths, port: int) -> None:
    """Create the database and apply schema.sql if not already present."""

    psql = paths.pg_bin / "psql"
    check = subprocess.run(
        [
            str(psql),
            "-h",
            "127.0.0.1",
            "-p",
            str(port),
            "-U",
            _DB_USER,
            "-d",
            "postgres",
            "-tAc",
            f"SELECT 1 FROM pg_database WHERE datname = '{_DB_NAME}';",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    if check.stdout.strip() != "1":
        _LOG.info("creating database %s", _DB_NAME)
        subprocess.run(
            [
                str(psql),
                "-h",
                "127.0.0.1",
                "-p",
                str(port),
                "-U",
                _DB_USER,
                "-d",
                "postgres",
                "-c",
                f'CREATE DATABASE "{_DB_NAME}" OWNER "{_DB_USER}";',
            ],
            check=True,
        )

    _LOG.info("applying schema %s", paths.schema_sql)
    subprocess.run(
        [
            str(psql),
            "-h",
            "127.0.0.1",
            "-p",
            str(port),
            "-U",
            _DB_USER,
            "-d",
            _DB_NAME,
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            str(paths.schema_sql),
        ],
        check=True,
    )


# ---------------------------------------------------------------------------
# Ingester + Grafana subprocesses
# ---------------------------------------------------------------------------


def _build_dsn(port: int) -> str:
    return f"postgresql://{_DB_USER}@127.0.0.1:{port}/{_DB_NAME}"


def _spawn_ingester(
    paths: StackPaths, port: int, *, once: bool
) -> subprocess.Popen[bytes]:
    cmd = [
        sys.executable,
        "-m",
        "core.telemetry.ingester",
        "--spool-root",
        str(paths.spool_root),
        "--db-dsn",
        _build_dsn(port),
    ]
    if once:
        cmd.append("--once")
    _LOG.info("spawning ingester (%s)", "once" if once else "continuous")
    return subprocess.Popen(cmd)


def _spawn_grafana(
    paths: StackPaths, *, pg_port: int, grafana_port: int
) -> subprocess.Popen[bytes]:
    paths.grafana_data.mkdir(parents=True, exist_ok=True)
    paths.grafana_log.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["GF_SANDWICH_PG_URL"] = f"127.0.0.1:{pg_port}"
    env["GF_SANDWICH_PG_PASSWORD"] = ""
    env["GF_SANDWICH_DASHBOARDS_DIR"] = str(paths.grafana_dashboards)

    # Grafana 12+ ships a single `bin/grafana` with subcommands; the old
    # standalone `bin/grafana-server` was removed. Both forms accept the
    # `cfg:default.X=Y` positional overrides, so this invocation works on
    # 11.x through current.
    cmd = [
        str(paths.grafana_homepath / "bin" / "grafana"),
        "server",
        "--homepath",
        str(paths.grafana_homepath),
        f"cfg:default.paths.data={paths.grafana_data}",
        f"cfg:default.paths.logs={paths.grafana_log}",
        f"cfg:default.paths.provisioning={paths.grafana_provisioning}",
        f"cfg:default.server.http_port={grafana_port}",
        "cfg:default.auth.anonymous.enabled=false",
        "cfg:default.users.allow_sign_up=false",
    ]
    _LOG.info("starting grafana on http://%s:%d", socket.gethostname(), grafana_port)
    return subprocess.Popen(cmd, env=env)


# ---------------------------------------------------------------------------
# Subcommand: up
# ---------------------------------------------------------------------------


def _wait_until_signal_or_grafana_exit(grafana: subprocess.Popen[bytes]) -> None:
    shutdown_requested = False

    def _on_signal(signum: int, _frame: object) -> None:
        nonlocal shutdown_requested
        _LOG.info("received signal %d; shutting down", signum)
        shutdown_requested = True

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    while not shutdown_requested:
        if grafana.poll() is not None:
            _LOG.warning("grafana exited unexpectedly with code %d", grafana.returncode)
            return
        time.sleep(1.0)


def cmd_up(args: argparse.Namespace) -> int:
    paths = _resolve_paths()
    _check_tarballs(paths)
    paths.spool_root.mkdir(parents=True, exist_ok=True)

    lock_fd = _acquire_lock(paths.lock_file)
    grafana_proc: subprocess.Popen[bytes] | None = None
    ingester_proc: subprocess.Popen[bytes] | None = None
    try:
        _enforce_pg_data_perms(paths)
        _ensure_pg_initialized(paths)
        _start_postgres(paths, args.pg_port)
        _wait_pg_ready(paths, args.pg_port)
        _ensure_database_and_schema(paths, args.pg_port)

        ingester_proc = _spawn_ingester(paths, args.pg_port, once=False)
        grafana_proc = _spawn_grafana(
            paths, pg_port=args.pg_port, grafana_port=args.grafana_port
        )

        url = f"http://{socket.gethostname()}:{args.grafana_port}"
        print("telemetry stack up:")
        print(f"  postgres   127.0.0.1:{args.pg_port}  data={paths.pg_data}")
        print(f"  grafana    {url}")
        print(f"  spool      {paths.spool_root}")
        print(f"  log        {paths.pg_log}")
        print("press ^C to stop")

        _wait_until_signal_or_grafana_exit(grafana_proc)
    finally:
        _LOG.info("tearing down stack")
        if grafana_proc and grafana_proc.poll() is None:
            grafana_proc.terminate()
            try:
                grafana_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                grafana_proc.kill()
        if ingester_proc and ingester_proc.poll() is None:
            ingester_proc.terminate()
            try:
                ingester_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                ingester_proc.kill()
        _stop_postgres(paths)
        os.close(lock_fd)
        try:
            paths.lock_file.unlink()
        except FileNotFoundError:
            pass
    return 0


# ---------------------------------------------------------------------------
# Subcommand: catch-up
# ---------------------------------------------------------------------------


def cmd_catch_up(args: argparse.Namespace) -> int:
    paths = _resolve_paths()
    _check_tarballs(paths)
    paths.spool_root.mkdir(parents=True, exist_ok=True)

    lock_fd = _acquire_lock(paths.lock_file)
    try:
        _enforce_pg_data_perms(paths)
        _ensure_pg_initialized(paths)
        _start_postgres(paths, args.pg_port)
        try:
            _wait_pg_ready(paths, args.pg_port)
            _ensure_database_and_schema(paths, args.pg_port)
            ingester = _spawn_ingester(paths, args.pg_port, once=True)
            ingester.wait()
            return ingester.returncode or 0
        finally:
            _stop_postgres(paths)
    finally:
        os.close(lock_fd)
        try:
            paths.lock_file.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# Subcommand: status
# ---------------------------------------------------------------------------


def cmd_status(_args: argparse.Namespace) -> int:
    paths = _resolve_paths()
    info = _read_lock_info(paths.lock_file)
    if not info:
        print(f"telemetry stack: not running (lock file absent at {paths.lock_file})")
        return 0
    can_take_lock = _try_acquire_lock_nonblocking(paths.lock_file)
    if can_take_lock:
        print(
            f"telemetry stack: lock file present but stale "
            f"(no holder); contents:\n{json.dumps(info, indent=2)}"
        )
    else:
        print("telemetry stack: running")
        print(json.dumps(info, indent=2))
    return 0


def _try_acquire_lock_nonblocking(lock_path: Path) -> bool:
    """True if we could acquire the lock right now (i.e. it's stale)."""

    try:
        fd = os.open(lock_path, os.O_RDWR)
    except FileNotFoundError:
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pipe telemetry",
        description="Boot the local telemetry stack (postgres + ingester + grafana).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="start the stack and block on ^C")
    up.add_argument("--pg-port", type=int, default=_DEFAULT_PG_PORT)
    up.add_argument("--grafana-port", type=int, default=_DEFAULT_GRAFANA_PORT)
    up.set_defaults(func=cmd_up)

    catch = sub.add_parser("catch-up", help="one-shot ingester pass; no grafana")
    catch.add_argument("--pg-port", type=int, default=_DEFAULT_PG_PORT)
    catch.set_defaults(func=cmd_catch_up)

    status = sub.add_parser("status", help="print whether the stack is running")
    status.set_defaults(func=cmd_status)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    if shutil.which("flock") is None:
        # Pure sanity; we use fcntl, not the flock(1) binary, but the absence
        # of flock(1) usually signals a very minimal environment.
        _LOG.debug("flock(1) not in PATH; using fcntl.flock from Python")
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
