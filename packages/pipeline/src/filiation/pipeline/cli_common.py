"""Run identity, shutdown handling, and shared options for filiation-pipeline commands."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager

import click


def head_commit() -> str:
    """Returns the SHA of the checked-out commit.

    Returns:
        str: Full 40-character SHA from `git rev-parse HEAD`.

    Raises:
        click.UsageError: If git is unavailable or this is not a repository.
    """
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise click.UsageError("Pass --commit-sha; git rev-parse HEAD failed") from exc


def run_identity(
    run_id: str | None, run_attempt: str | None, commit_sha: str | None, writer: str | None
) -> tuple[str, str, str]:
    """Resolves a run's id suffix, commit, and writer from options, GitHub Actions, or local defaults.

    Args:
        run_id: Run id suffix, e.g. GITHUB_RUN_ID.
        run_attempt: Re-run attempt, e.g. GITHUB_RUN_ATTEMPT.
        commit_sha: Commit of the code that runs; defaults to git HEAD.
        writer: "github-actions", "oracle-vm", or "local"; defaults by environment.

    Returns:
        tuple[str, str, str]: The run suffix, commit SHA, and writer.

    Raises:
        click.UsageError: If no commit was given and git rev-parse HEAD fails.
    """
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    # GitHub keeps GITHUB_RUN_ID on "Re-run jobs"; the attempt keeps manifest keys unique.
    suffix = f"{run_id}-{run_attempt}" if run_id and run_attempt else run_id
    return (
        suffix or f"local-{int(time.time())}",
        commit_sha or head_commit(),
        writer or ("github-actions" if in_actions else "local"),
    )


run_id_option = click.option("--run-id", envvar="GITHUB_RUN_ID", default=None, help="Run id suffix [env: GITHUB_RUN_ID]")
run_attempt_option = click.option(
    "--run-attempt",
    envvar="GITHUB_RUN_ATTEMPT",
    default=None,
    help="Re-run attempt, appended to the run id [env: GITHUB_RUN_ATTEMPT]",
)
commit_option = click.option("--commit-sha", envvar="GITHUB_SHA", default=None, help="Code commit [env: GITHUB_SHA]")
writer_option = click.option(
    "--writer",
    type=click.Choice(["github-actions", "oracle-vm", "local"]),
    default=None,
    help="Who is writing; defaults to github-actions under Actions, else local",
)


@contextmanager
def sigterm_as_exit() -> Iterator[None]:
    """Turns SIGTERM into SystemExit(143) while active, so cleanup in finally blocks still runs.

    Yields:
        None: Control to the guarded block; the previous handler is restored afterwards.
    """

    def handler(signum: int, frame: object) -> None:
        raise SystemExit(128 + signum)

    previous = signal.signal(signal.SIGTERM, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)
