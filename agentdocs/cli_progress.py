"""Render benchmark lifecycle events in the terminal."""

from __future__ import annotations

from rich.console import Console

from agentdocs.agents.failures import failure_display_label
from agentdocs.progress import (
    AgentFailed,
    AgentFinished,
    AgentStarted,
    ProgressEvent,
    SuiteFinished,
    SuiteStarted,
    TaskFinished,
    TaskStarted,
    VerifierFailed,
    VerifierFinished,
    VerifierStarted,
    WorkspaceChangesCaptured,
)


class CliProgressReporter:
    """Print task progress. Quiet mode prints nothing.

    Interactive terminals show a status line while the agent or verifier is
    running. Other destinations get one stable line per event.
    """

    def __init__(
        self,
        console: Console,
        *,
        verbose: bool = False,
        debug: bool = False,
        quiet: bool = False,
    ) -> None:
        self._console = console
        self._verbose = verbose
        self._debug = debug
        self._quiet = quiet
        self._index = 0
        self._total = 0
        self._task_id = ""
        self._status = None

    def __call__(self, event: ProgressEvent) -> None:
        if self.quiet:
            return
        if isinstance(event, SuiteStarted):
            self._total = event.total_tasks
            return
        if isinstance(event, TaskStarted):
            self._index = event.index
            self._total = event.total
            self._task_id = event.task_id
            self._stop_status()
            self._console.print(f"[{event.index}/{event.total}] {event.task_id}")
            return
        if isinstance(event, AgentStarted):
            self._task_id = event.task_id
            choice = event.requested_model or "provider default"
            self._console.print(
                f"{self._label()}: agent started ({event.agent}, {choice})"
            )
            if self._console.is_terminal:
                self._start_status(f"{self._label()} {event.agent} running ({choice})...")
            return
        if isinstance(event, AgentFinished):
            self._stop_status()
            duration = _seconds(event.result.duration_seconds)
            line = f"{self._label()}: agent exit {event.result.exit_code} in {duration}"
            failure = event.result.failure
            if failure is not None:
                line = f"{line} · {failure_display_label(failure.kind)}"
            self._console.print(line)
            if self._verbose and failure is not None:
                blocking = "yes" if failure.blocking else "no"
                self._console.print("      Provider failure:")
                self._console.print(f"        kind: {failure.kind.value}")
                self._console.print(f"        blocking: {blocking}")
                self._console.print(f"        rule: {failure.rule_id}")
                self._console.print(f"        source: {failure.source}")
            if self._verbose or self._debug:
                stdout_lines = _line_count(event.result.stdout)
                stderr_note = "present" if event.result.stderr else "empty"
                self._console.print(
                    f"      events: {len(event.result.events)}, "
                    f"stdout lines: {stdout_lines}, stderr: {stderr_note}"
                )
            if self._debug:
                _print_capture(
                    self._console,
                    event.result.agent,
                    event.task_id,
                    "stdout",
                    event.result.stdout,
                )
                _print_capture(
                    self._console,
                    event.result.agent,
                    event.task_id,
                    "stderr",
                    event.result.stderr,
                )
            return
        if isinstance(event, WorkspaceChangesCaptured):
            self._console.print(
                f"{self._label()}: workspace changes "
                f"+{event.files_added} ~{event.files_modified} -{event.files_deleted}"
            )
            if self._verbose:
                self._console.print(
                    "      directories: "
                    f"+{event.directories_added} -{event.directories_deleted}"
                )
                self._console.print(f"      type changes: {event.type_changed}")
                for path in event.paths:
                    self._console.print(f"      {path}", markup=False, highlight=False)
                if event.paths_omitted:
                    self._console.print(f"      {event.paths_omitted} more not shown")
            return
        if isinstance(event, AgentFailed):
            self._stop_status()
            self._console.print(
                f"{self._label()}: agent failed after {_seconds(event.duration_seconds)}: "
                f"{event.error}"
            )
            return
        if isinstance(event, VerifierStarted):
            self._console.print(f"{self._label()}: verifier started")
            if self._console.is_terminal:
                self._start_status(f"{self._label()} verifying...")
            return
        if isinstance(event, VerifierFinished):
            self._stop_status()
            label = "PASS" if event.result.passed else "FAIL"
            self._console.print(
                f"{self._label()}: {label}, verifier exit {event.result.exit_code}"
            )
            if self._debug:
                _print_capture(
                    self._console,
                    "verifier",
                    event.task_id,
                    "stdout",
                    event.result.stdout,
                )
                _print_capture(
                    self._console,
                    "verifier",
                    event.task_id,
                    "stderr",
                    event.result.stderr,
                )
            return
        if isinstance(event, VerifierFailed):
            self._stop_status()
            self._console.print(f"{self._label()}: verifier failed: {event.error}")
            return
        if isinstance(event, TaskFinished):
            label = "PASS" if event.passed else "FAIL"
            self._console.print(
                f"{self._label()}: {label} in {_seconds(event.duration_seconds)}"
            )
            return
        if isinstance(event, SuiteFinished):
            return

    @property
    def quiet(self) -> bool:
        return self._quiet

    def _label(self) -> str:
        if self._index and self._total:
            return f"[{self._index}/{self._total}] {self._task_id}"
        return self._task_id

    def _start_status(self, text: str) -> None:
        self._stop_status()
        self._status = self._console.status(text)
        self._status.start()

    def _stop_status(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None


def _seconds(value: float) -> str:
    return f"{value:.1f}s"


def _line_count(text: str) -> int:
    if not text:
        return 0
    return len(text.splitlines())


def _print_capture(
    console: Console,
    source: str,
    task_id: str,
    stream: str,
    text: str,
) -> None:
    console.print(f"--- {source} {stream}: {task_id} ---")
    if text:
        console.print(text, end="" if text.endswith("\n") else "\n")
    console.print(f"--- end {stream} ---")
