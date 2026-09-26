from __future__ import annotations

import json
import subprocess

import pytest

from agentdocs.models import (
    ModelDiscoveryError,
    discover_claude_models,
    discover_codex_models,
    discover_cursor_models,
    parse_codex_model_catalog,
    parse_cursor_model_listing,
)


def test_codex_catalog_keeps_visible_order() -> None:
    payload = {
        "models": [
            {"slug": "model-b", "display_name": "Model B", "visibility": "list"},
            {"slug": "hidden-one", "display_name": "Hidden", "visibility": "hide"},
            {"slug": "model-a", "display_name": "  Model A  ", "visibility": "list"},
        ]
    }

    models = parse_codex_model_catalog(json.dumps(payload))

    assert [model.id for model in models] == ["model-b", "model-a"]
    assert models[0].display_name == "Model B"
    assert models[0].source == "codex-local-catalog"
    assert models[1].display_name == "Model A"
    assert all(model.is_default is False for model in models)


def test_codex_catalog_rejects_malformed_json() -> None:
    with pytest.raises(ModelDiscoveryError, match="not valid JSON"):
        parse_codex_model_catalog("{")
    with pytest.raises(ModelDiscoveryError, match="models list"):
        parse_codex_model_catalog(json.dumps({"models": {}}))


def test_codex_discovery_reports_missing_and_failed_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agentdocs.models.shutil.which", lambda _name: None)
    missing = discover_codex_models()
    assert missing.status == "executable_not_found"
    assert missing.discovery_supported is False
    assert missing.models == ()

    def fail(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 2, stdout="", stderr="unknown command\n")

    monkeypatch.setattr("agentdocs.models.shutil.which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr("agentdocs.models.subprocess.run", fail)
    failed = discover_codex_models()
    assert failed.status == "failed"
    assert "exited 2" in (failed.message or "")


def test_codex_discovery_parses_bundled_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        payload = {"models": [{"slug": "model-a", "display_name": "Model A"}]}
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("agentdocs.models.shutil.which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr("agentdocs.models.subprocess.run", fake_run)

    result = discover_codex_models()

    assert result.status == "available"
    assert [model.id for model in result.models] == ["model-a"]
    args = captured["args"]
    assert args == ["/usr/bin/codex", "debug", "models", "--bundled"]
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["shell"] is False


def test_claude_discovery_is_unsupported_without_launching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("claude should not be launched")

    monkeypatch.setattr("agentdocs.models.shutil.which", lambda name: "/usr/bin/claude" if name == "claude" else None)
    monkeypatch.setattr("agentdocs.models.subprocess.run", explode)

    result = discover_claude_models()

    assert result.status == "unsupported"
    assert result.discovery_supported is False
    assert result.models == ()
    assert "/model" in (result.message or "")


def test_claude_discovery_reports_missing_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentdocs.models.shutil.which", lambda _name: None)

    result = discover_claude_models()

    assert result.status == "executable_not_found"


def test_cursor_listing_marks_the_default_row() -> None:
    text = "Available models\n\nauto - Auto (current, default)\nmodel-b - Model B\n"

    models = parse_cursor_model_listing(text)

    assert [model.id for model in models] == ["auto", "model-b"]
    assert models[0].is_default is True
    assert models[0].source == "cursor-account"
    assert models[1].is_default is False


def test_cursor_listing_rejects_empty_output() -> None:
    with pytest.raises(ModelDiscoveryError, match="did not contain"):
        parse_cursor_model_listing("Available models\n")


def test_cursor_discovery_uses_fallback_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def which(name: str) -> str | None:
        if name == "cursor-agent":
            return "/usr/local/bin/cursor-agent"
        return None

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="model-a - Model A\n",
            stderr="",
        )

    monkeypatch.setattr("agentdocs.models.shutil.which", which)
    monkeypatch.setattr("agentdocs.models.subprocess.run", fake_run)

    result = discover_cursor_models()

    assert result.status == "available"
    assert result.models[0].id == "model-a"
    assert captured["args"] == ["/usr/local/bin/cursor-agent", "models"]


def test_cursor_discovery_reports_missing_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentdocs.models.shutil.which", lambda _name: None)

    result = discover_cursor_models()

    assert result.status == "executable_not_found"
    assert result.models == ()
