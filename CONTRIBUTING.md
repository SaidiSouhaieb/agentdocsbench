# Contributing

AgentDocsBench is a verifier-based benchmark harness. Changes should keep task correctness on the verifier, and they should keep provider-specific behavior inside the agent adapters.

## Setup

Python 3.12 or newer is required.

```bash
python -m pip install -e .
pytest
```

The unit tests must not call Codex, Claude, Cursor, or `cursor-agent`. They must not require provider credits. Docker integration stays optional: `pytest` must not need a Docker daemon. The fake images under `examples/docker-smoke/` and `examples/docs-experiment-smoke/` are manual and CI smoke tests.

`tests/test_init_project.py` covers the scaffold. `tests/test_doctor.py` covers diagnostics, including mocked Docker checks. Neither test module may invoke a real provider CLI.

## Changes

- Include tests for behavior you change.
- Keep structured artifacts free of environment values, credentials, prompts where the existing artifacts already omit them, and documentation file contents in experiment reports.
- Keep raw agent and verifier logs out of default CI uploads.
- Avoid new provider-specific branches in the core suite runner. Adapters own CLI details.
- This repository does not configure Ruff, Black, or mypy. Do not add a formatter or type checker as part of an unrelated change.

Product scope and the release checklist are in [RELEASE.md](RELEASE.md). Historical implementation notes are in [TECHNICAL.md](TECHNICAL.md).
