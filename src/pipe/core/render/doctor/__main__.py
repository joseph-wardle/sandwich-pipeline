"""Run the farm doctor without a window.

Needs no Qt, so any Python 3.11 with the pipeline's `src` on its path will do:

    PYTHONPATH=src .venv/bin/python -m pipe.core.render.doctor check
    PYTHONPATH=src .venv/bin/python -m pipe.core.render.doctor login
    PYTHONPATH=src .venv/bin/python -m pipe.core.render.doctor run --act --steer

`check` diagnoses the farm once and prints it. `run` checks every few minutes
and notes in its journal what it would retry; with `--act` it retries for
real, which needs `login` first. `status` shows what a running doctor is doing.
"""

from __future__ import annotations

import argparse
import fcntl
import getpass
import logging
import os
import signal
import socket
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any, TextIO

from pipe.core.render import farm
from pipe.core.render.doctor import cron, login, policy, records
from pipe.core.render.doctor.blades import History
from pipe.core.render.doctor.checkup import Checkup, Doctor, Settings, group
from pipe.core.render.doctor.login import Connection
from pipe.core.render.doctor.policy import Action
from pipe.core.render.doctor.records import Paths
from pipe.core.render.farm import FarmError, LoginRefused

log = logging.getLogger(__name__)

_DEFAULT_INTERVAL = 120


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(name)s] [%(levelname)s] %(message)s"
    )
    parser = argparse.ArgumentParser(
        description="Diagnose Tractor's errored tasks and retry what can pass."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="diagnose the farm once and print it")
    run = commands.add_parser("run", help="check the farm every few minutes")
    _add_run_options(run)
    run.add_argument("--once", action="store_true", help="check once, then stop")
    run.add_argument(
        "--if-not-running",
        action="store_true",
        help="quietly do nothing if a doctor is already running, as cron's starts do",
    )
    schedule = commands.add_parser(
        "schedule", help="have cron keep the doctor running on this computer"
    )
    _add_run_options(schedule)
    commands.add_parser(
        "unschedule", help="stop cron starting the doctor on this computer"
    )
    save = commands.add_parser(
        "login", help="save a Tractor password for the doctor and log in"
    )
    save.add_argument(
        "--user", default=getpass.getuser(), help="the Tractor user, you by default"
    )
    commands.add_parser("status", help="show what a running doctor is doing")
    undo = commands.add_parser(
        "unsteer", help="put back the service keys the doctor narrowed on a job"
    )
    undo.add_argument("jid", type=int, help="the job's id")
    undo.add_argument("tid", type=int, nargs="*", help="only these tasks")
    fresh = commands.add_parser(
        "clear",
        help="give a machine a fresh start, as after rebooting it: its earlier "
        "failures stop counting, and retries stop avoiding it",
    )
    fresh.add_argument("machine", help="the machine's name, as Tractor shows it")
    fresh.add_argument(
        "--undo", action="store_true", help="take back the machine's fresh start"
    )
    args = parser.parse_args()

    paths = records.default_paths()
    try:
        if args.command == "check":
            _check(paths)
        elif args.command == "run":
            owners = frozenset(args.owner) if args.owner else None
            _run(
                paths,
                Settings(acting=args.act, steer=args.steer, owners=owners),
                args.interval,
                args.once,
                args.if_not_running,
            )
        elif args.command == "schedule":
            _schedule(paths, args)
        elif args.command == "unschedule":
            _unschedule()
        elif args.command == "login":
            _login(paths, args.user)
        elif args.command == "unsteer":
            _unsteer(paths, args.jid, set(args.tid))
        elif args.command == "clear":
            records.clear(paths, args.machine, getpass.getuser(), args.undo)
            print(
                f"Took back {args.machine}'s fresh start."
                if args.undo
                else f"Gave {args.machine} a fresh start: its earlier failures no "
                "longer count, and retries stop avoiding it from the doctor's next "
                "round."
            )
        else:
            _status(paths)
    except FarmError as exc:
        sys.exit(str(exc))


def _add_run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--act",
        action="store_true",
        help="retry for real, rather than only noting what it would",
    )
    parser.add_argument(
        "--steer",
        action="store_true",
        help="keep retries off the blades that failed them",
    )
    parser.add_argument(
        "--owner",
        action="append",
        help="only this artist's jobs; give it again for more",
    )
    parser.add_argument(
        "--interval", type=int, default=_DEFAULT_INTERVAL, help="seconds between checks"
    )


def _schedule(paths: Paths, args: argparse.Namespace) -> None:
    if args.act and not (paths.session.exists() or paths.password.exists()):
        sys.exit(
            "The doctor has no Tractor login to retry with. Run "
            "`python -m pipe.core.render.doctor login` first."
        )
    arguments = ["--act"] if args.act else []
    arguments += ["--steer"] if args.steer else []
    for owner in args.owner or []:
        arguments += ["--owner", owner]
    arguments += ["--interval", str(args.interval)]
    line = cron.install(paths, arguments)
    host = socket.gethostname()
    print(f"Cron on {host} now starts the farm doctor every 10 minutes, unless")
    print("it's already running. It first starts within 10 minutes; check on it")
    print("from any lab computer with `python -m pipe.core.render.doctor status`.")
    print(
        f"\nIts output goes to {paths.state / 'doctor.log'}. The crontab line:\n{line}"
    )
    print(
        f"\nTo stop it, run `python -m pipe.core.render.doctor unschedule` on {host},"
    )
    print("then end the running doctor with `kill <pid>`, the pid `status` shows.")


def _unschedule() -> None:
    if cron.remove():
        print(f"Cron on {socket.gethostname()} no longer starts the farm doctor.")
        print("A doctor already running keeps going: `status` shows its pid to kill.")
    else:
        print(f"Cron on {socket.gethostname()} wasn't starting the farm doctor.")


def _check(paths: Paths) -> None:
    checkup = Doctor(History(paths.history), paths=paths).check()
    _print_checkup(checkup, sys.stdout)


def _run(
    paths: Paths, settings: Settings, interval: int, once: bool, quiet: bool = False
) -> None:
    with _only_doctor(paths, quiet):
        doctor = Doctor(History(paths.history, write=True), paths=paths)
        connection: Connection | None = None

        def connect() -> Connection:
            nonlocal connection
            if connection is None:
                connection = login.connect(paths)
            return connection

        started = time.time()
        stop = _stop_on_signal()
        log.info(
            "The farm doctor is %s%s%s",
            "retrying for real" if settings.acting else "watching only",
            ", steering retries" if settings.steer else "",
            f", for {', '.join(sorted(settings.owners))}" if settings.owners else "",
        )
        while True:
            status = _status_base(settings, started)
            try:
                checkup = doctor.check()
                entries = doctor.treat(checkup, settings, connect, paths)
                status |= _round_status(checkup, settings, entries)
                for entry in entries:
                    log.info(
                        "%s %s of job %s: %s",
                        entry["result"],
                        entry["task"],
                        entry["jid"],
                        entry["reason"],
                    )
            except FarmError as exc:
                status["error"] = str(exc)
                log.warning("This round failed: %s", exc)
            status["login"] = doctor.login
            status["next"] = time.time() + interval
            records.write_status(paths, status)
            if once or stop.wait(interval):
                break
        records.write_status(paths, status | {"stopped": time.time(), "next": None})


def _login(paths: Paths, user: str) -> None:
    password = getpass.getpass(f"Tractor password for {user}: ")
    if not password:
        sys.exit("No password given, so nothing was saved.")
    login.save_password(paths, password)
    login.forget_session(paths)
    try:
        connection = login.connect(paths, user)
    except LoginRefused:
        # Don't keep a password Tractor refused, as a mistyped one may be.
        paths.password.unlink(missing_ok=True)
        raise
    print(f"Logged in as {user} with the {connection.how}.")
    print(f"The password is in {paths.password} and the session in {paths.session},")
    print(
        "both readable only by you. The doctor uses the session until Tractor ends it."
    )


def _unsteer(paths: Paths, jid: int, tids: set[int]) -> None:
    """Undo `--steer` on a job's commands, as when a steered task never starts."""
    job = next((j for j in farm.all_jobs() if j.jid == jid), None)
    if job is None:
        sys.exit(f"Tractor has no job {jid}, or it is archived.")
    connection = login.connect(paths)
    changed = 0
    for task in farm.tasks(job):
        if tids and task.tid not in tids:
            continue
        for command in farm.commands(job, task):
            original = policy.unsteered(command.service)
            if original == command.service:
                continue
            connection.set_service(job.jid, command.cid, original)
            records.write_journal(
                paths,
                {
                    "when": time.time(),
                    "host": socket.gethostname(),
                    "by": f"{getpass.getuser()} (unsteer)",
                    "acting": True,
                    "jid": job.jid,
                    "tid": task.tid,
                    "job": job.title,
                    "task": task.title,
                    "owner": job.owner,
                    "services": [
                        {
                            "cid": command.cid,
                            "before": command.service,
                            "after": original,
                        }
                    ],
                    "result": "service key put back",
                    "reason": "Undid the doctor's steering.",
                },
            )
            print(f"{task.title}: {command.service}  ->  {original or '(none)'}")
            changed += 1
    print(f"Put back {changed} service key{'s' if changed != 1 else ''} on job {jid}.")


def _status(paths: Paths) -> None:
    status = records.read_status(paths)
    if status is None:
        print("The farm doctor hasn't run on this account.")
    else:
        for key, value in status.items():
            shown = (
                time.strftime("%b %d %H:%M:%S", time.localtime(value))
                if key in _TIMES and value
                else value
            )
            print(f"{key:>16}: {shown}")
    entries = records.read_journal(paths, 15)
    if entries:
        print("\nLatest in the journal:")
    for entry in entries:
        when = time.strftime("%b %d %H:%M", time.localtime(entry.get("when", 0)))
        print(
            f"  {when}  J{entry.get('jid')} {entry.get('task')}: {entry.get('result')} — {entry.get('reason')}"
        )


_TIMES = {"started", "checked", "next", "stopped"}


def _status_base(settings: Settings, started: float) -> dict[str, Any]:
    return {
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "user": getpass.getuser(),
        "started": started,
        "acting": settings.acting,
        "steering": settings.steer,
        "owners": sorted(settings.owners) if settings.owners else None,
    }


def _round_status(
    checkup: Checkup, settings: Settings, entries: list[dict[str, Any]]
) -> dict[str, Any]:
    plans = checkup.plans(settings.steer)
    count = {action: sum(1 for p in plans if p.action is action) for action in Action}
    return {
        "checked": checkup.when,
        "jobs": len(checkup.jobs),
        "errored": len(checkup.diagnoses),
        "retry_now": count[Action.RETRY],
        "waiting": count[Action.WAIT],
        "for_a_person": count[Action.HOLD],
        "retried": sum(1 for e in entries if e.get("result") == "retried"),
        "suspects": sorted(checkup.suspects),
        "problems": list(checkup.problems),
        "error": None,
    }


def _print_checkup(checkup: Checkup, out: TextIO) -> None:
    plans = checkup.plans(steer=True)
    out.write(
        f"{len(checkup.jobs)} unfinished jobs, {len(checkup.diagnoses)} errored tasks\n\n"
    )
    for problem in group(plans):
        first = problem.first
        d = first.diagnosis
        why = f"  Why: {d.why}\n" if d.why != first.reason else ""
        out.write(
            f"J{problem.job.jid} {problem.job.title} ({problem.job.owner}): {len(problem.plans)} task"
            f"{'s' if len(problem.plans) != 1 else ''}, e.g. {d.task.title}, {d.attempts} attempts\n"
            f"  {first.action.value}: {first.reason}\n"
            f"{why}"
            f"  Log: {d.latest.evidence}\n"
            f"  Blades: {', '.join(d.same_way_blades)}\n\n"
        )
    suspects = [h for h in checkup.blades if h.suspect]
    out.write(f"{len(suspects)} suspect blades\n")
    for health in suspects:
        out.write(f"  {health.blade.name}: {'; '.join(health.problems)}\n")
    for problem in checkup.problems:
        out.write(f"Couldn't read: {problem}\n")


@contextmanager
def _only_doctor(paths: Paths, quiet: bool) -> Generator[None]:
    """Hold the doctor's lock, so two doctors never retry the same task.
    If another holds it, exit: `quiet`ly, for cron's regular starts."""
    paths.state.mkdir(parents=True, exist_ok=True)
    with paths.lock.open("a") as lock:
        try:
            # A POSIX lock, which holds across the NFS home folders.
            fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if quiet:
                sys.exit(0)
            status = records.read_status(paths) or {}
            sys.exit(
                f"The farm doctor is already running on {status.get('host', 'another computer')} "
                f"(pid {status.get('pid', '?')}). Stop it before starting another."
            )
        yield


class _Stop:
    """Set by SIGTERM or Ctrl-C, ending the wait between rounds at once."""

    def __init__(self) -> None:
        self.stopped = False

    def wait(self, seconds: float) -> bool:
        end = time.time() + seconds
        while not self.stopped and time.time() < end:
            time.sleep(min(1.0, end - time.time()))
        return self.stopped


def _stop_on_signal() -> _Stop:
    stop = _Stop()

    def handle(_signal: int, _frame: object) -> None:
        log.info("Stopping after this round")
        stop.stopped = True

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
    return stop


if __name__ == "__main__":
    main()
