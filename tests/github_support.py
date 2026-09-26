"""Helpers for tests that pretend to be GitHub Actions files."""

from __future__ import annotations

from pathlib import Path

import pytest


def github_files(monkeypatch: pytest.MonkeyPatch, directory: Path) -> tuple[Path, Path]:
    summary = directory / "step-summary.md"
    output = directory / "output.txt"
    summary.write_text("", encoding="utf-8")
    output.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    return summary, output


def parse_github_output(text: str) -> dict[str, str]:
    lines = text.splitlines()
    parsed: dict[str, str] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if "<<" not in line:
            raise AssertionError(f"unexpected GitHub output line: {line!r}")
        key, delimiter = line.split("<<", 1)
        index += 1
        body: list[str] = []
        while index < len(lines) and lines[index] != delimiter:
            body.append(lines[index])
            index += 1
        if index >= len(lines):
            raise AssertionError(f"missing delimiter for {key}")
        parsed[key] = "\n".join(body)
        index += 1
    return parsed
