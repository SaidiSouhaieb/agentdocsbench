"""Best-effort model listing through the installed provider CLIs.

Selection does not depend on this module. A provider can accept ``--model``
even when this listing says discovery is unavailable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass

_DISCOVERY_TIMEOUT_SECONDS = 30
_DETAIL_LIMIT = 400
_SECRET_MARKERS = (
    "api_key",
    "api key",
    "token",
    "authorization",
    "secret",
    "password",
    "cookie",
)


class ModelDiscoveryError(RuntimeError):
    """A provider listing could not be parsed. This is not a benchmark failure."""


@dataclass(frozen=True)
class ModelInfo:
    """One model id reported by a provider CLI."""

    id: str
    display_name: str | None = None
    is_default: bool = False
    source: str | None = None


@dataclass(frozen=True)
class ModelDiscoveryResult:
    """What one provider CLI could say about its models.

    ``status`` is ``available``, ``unsupported``, ``executable_not_found``,
    or ``failed``. An empty ``models`` tuple with ``available`` means the CLI
    answered and listed nothing.
    """

    agent: str
    models: tuple[ModelInfo, ...]
    status: str
    message: str | None = None

    @property
    def discovery_supported(self) -> bool:
        return self.status == "available"


def discover_models(agent: str) -> ModelDiscoveryResult:
    """List models for one supported agent type."""
    if agent == "codex":
        return discover_codex_models()
    if agent == "claude":
        return discover_claude_models()
    if agent == "cursor":
        return discover_cursor_models()
    raise ValueError(
        f"Unsupported agent type {agent!r}. Supported agent types: claude, codex, cursor."
    )


def discover_all_models() -> tuple[ModelDiscoveryResult, ...]:
    """List models for Codex, Claude, and Cursor. Each result stands alone."""
    return tuple(discover_models(agent) for agent in ("codex", "claude", "cursor"))


def discover_codex_models() -> ModelDiscoveryResult:
    """Read the catalog shipped inside the local Codex CLI.

    ``codex debug models --bundled`` skips the CLI's catalog refresh. The
    result is that binary's bundled list, not the models this account can use.
    """
    executable = shutil.which("codex")
    if executable is None:
        return _missing("codex", "codex")
    completed = _run([executable, "debug", "models", "--bundled"], agent="codex")
    if isinstance(completed, ModelDiscoveryResult):
        return completed
    if completed.returncode != 0:
        return _failed("codex", completed)
    try:
        models = parse_codex_model_catalog(completed.stdout or "")
    except ModelDiscoveryError as exc:
        return ModelDiscoveryResult(
            agent="codex",
            models=(),
            status="failed",
            message=str(exc),
        )
    return ModelDiscoveryResult(
        agent="codex",
        models=models,
        status="available",
        message=(
            "Local catalog shipped with this Codex CLI. "
            "This is not the authenticated account's model list."
        ),
    )


def discover_claude_models() -> ModelDiscoveryResult:
    """Report that this Claude Code CLI has no non-interactive model list.

    Claude Code 2.1.283 accepts ``--model`` and an interactive ``/model``
    picker. It has no machine-readable list command. This function does not
    start Claude.
    """
    if shutil.which("claude") is None:
        return _missing("claude", "claude")
    return ModelDiscoveryResult(
        agent="claude",
        models=(),
        status="unsupported",
        message=(
            "Claude Code supports --model, but this installed CLI does not "
            "expose a supported non-interactive model-list command. "
            "Run `claude` and use `/model` to inspect models available to this account."
        ),
    )


def discover_cursor_models() -> ModelDiscoveryResult:
    """Run ``agent models`` or ``cursor-agent models`` and parse its text list."""
    executable = _cursor_executable()
    if executable is None:
        return ModelDiscoveryResult(
            agent="cursor",
            models=(),
            status="executable_not_found",
            message=(
                "Cursor Agent CLI executable 'agent' was not found in PATH, "
                "and 'cursor-agent' was not found either."
            ),
        )
    completed = _run([executable, "models"], agent="cursor")
    if isinstance(completed, ModelDiscoveryResult):
        return completed
    if completed.returncode != 0:
        return _failed("cursor", completed)
    try:
        models = parse_cursor_model_listing(completed.stdout or "")
    except ModelDiscoveryError as exc:
        return ModelDiscoveryResult(
            agent="cursor",
            models=(),
            status="failed",
            message=str(exc),
        )
    return ModelDiscoveryResult(
        agent="cursor",
        models=models,
        status="available",
        message="Models reported by the installed Cursor CLI for this account.",
    )


def parse_codex_model_catalog(text: str) -> tuple[ModelInfo, ...]:
    """Parse ``codex debug models`` JSON. Hidden catalog rows are omitted."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelDiscoveryError("Codex model catalog was not valid JSON.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ModelDiscoveryError("Codex model catalog did not contain a models list.")
    models: list[ModelInfo] = []
    for item in payload["models"]:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            continue
        if item.get("visibility") == "hide":
            continue
        display = item.get("display_name")
        display_name = display.strip() if isinstance(display, str) and display.strip() else None
        models.append(
            ModelInfo(
                id=slug.strip(),
                display_name=display_name,
                source="codex-local-catalog",
            )
        )
    return tuple(models)


def parse_cursor_model_listing(text: str) -> tuple[ModelInfo, ...]:
    """Parse ``cursor-agent models`` rows of the form ``id - display name``."""
    models: list[ModelInfo] = []
    for raw in text.splitlines():
        line = raw.strip()
        if " - " not in line:
            continue
        model_id, display = line.split(" - ", 1)
        model_id = model_id.strip()
        display = display.strip()
        if not model_id or any(character.isspace() for character in model_id):
            continue
        is_default = "(default)" in display or "(current, default)" in display
        models.append(
            ModelInfo(
                id=model_id,
                display_name=display or None,
                is_default=is_default,
                source="cursor-account",
            )
        )
    if not models:
        raise ModelDiscoveryError("Cursor model listing did not contain any model rows.")
    return tuple(models)


def _cursor_executable() -> str | None:
    for name in ("agent", "cursor-agent"):
        executable = shutil.which(name)
        if executable is not None:
            return executable
    return None


def _missing(agent: str, executable: str) -> ModelDiscoveryResult:
    return ModelDiscoveryResult(
        agent=agent,
        models=(),
        status="executable_not_found",
        message=f"CLI executable {executable!r} was not found in PATH.",
    )


def _failed(agent: str, completed: subprocess.CompletedProcess[str]) -> ModelDiscoveryResult:
    detail = _safe_detail(completed.stderr or completed.stdout or "")
    message = f"Model listing exited {completed.returncode}."
    if detail:
        message = f"{message} {detail}"
    return ModelDiscoveryResult(agent=agent, models=(), status="failed", message=message)


def _run(
    command: list[str],
    *,
    agent: str,
) -> subprocess.CompletedProcess[str] | ModelDiscoveryResult:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_DISCOVERY_TIMEOUT_SECONDS,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ModelDiscoveryResult(
            agent=agent,
            models=(),
            status="failed",
            message=(
                f"Model listing did not finish within {_DISCOVERY_TIMEOUT_SECONDS:g} seconds."
            ),
        )
    except OSError as exc:
        return ModelDiscoveryResult(
            agent=agent,
            models=(),
            status="failed",
            message=f"Model listing could not start: {exc}",
        )


def _safe_detail(text: str) -> str:
    lines = []
    for line in text.splitlines():
        lowered = line.lower()
        if any(marker in lowered for marker in _SECRET_MARKERS):
            continue
        lines.append(line.strip())
    detail = " ".join(part for part in lines if part)
    if len(detail) > _DETAIL_LIMIT:
        return detail[: _DETAIL_LIMIT - 3] + "..."
    return detail
