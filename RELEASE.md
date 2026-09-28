# Release checklist

AgentDocsBench 0.1.0 is prepared as a source release. This file does not publish anything.

## Still open

`v0.1.0` is published on PyPI as `agentdocsbench`. The repository URL is in `pyproject.toml`.

## GitHub social preview

GitHub does not read the social preview from the README. Upload it by hand:

1. Open the repository on GitHub.
2. Go to Settings, then General, then Social preview.
3. Upload `assets/brand/social-preview.png`.

Nothing in this repository applies that setting.

`LICENSE` is the MIT license, matching the license already used on the public `promptly-lite` repository. The copyright line is Souhaieb Saidi, 2026.

## Before tagging v0.1.0

- `pytest` passes
- README commands match `agentdocs --help`
- `agentdocs init <empty-dir> --agent cursor` creates a benchmark and refuses a non-empty directory
- `agentdocs doctor --config <that benchmark>` prints a report and exits 0 or 1 without running an agent
- CI passes: unit tests, Docker smoke, and docs-experiment smoke
- Docker smoke passes locally when you want that extra check
- Docs-experiment smoke passes locally when you want that extra check
- Package version in `pyproject.toml` is `0.1.0`
- [CHANGELOG.md](CHANGELOG.md) matches the version you tag
- A `LICENSE` file exists and matches any license metadata you add
- `git status` does not show `.agentdocs/`, virtualenvs, or credential files staged
- Raw `agent.*.log` and `verifier.*.log` files are not staged

## Not part of this checklist

Do not automate, from this repository's prep work:

- `git tag`
- `git push`
- a GitHub Release
- PyPI publishing
- publishing Docker images

Those happen only when a maintainer decides to release.
