# Changelog

## 0.1.0

First public snapshot of the AgentDocsBench harness. This version is not published to PyPI. No release date is recorded here.

### Core benchmark execution

- YAML benchmark configuration for docs, a starter project, a coding agent, and ordered tasks
- One temporary workspace per task
- A suite runner and the `agentdocs test` command
- Live progress, with quiet, verbose, and debug display modes
- Persistent run artifacts: `result.json`, `summary.md`, `benchmark.json`, `changes.json`, and raw logs

### Agent integrations

- Codex, Claude Code, and Cursor Agent adapters
- Optional model selection, with `--model` overriding `agent.model`, which overrides the provider default
- `agentdocs models` for CLIs that can enumerate models

### Verification and provenance

- Deterministic verifiers. PASS follows the verifier exit code
- Benchmark fingerprints for docs, starter, tasks, and verifiers
- Workspace change manifests captured before verification
- Classification of known blocking provider failures, separate from verifier FAIL

### Comparison and experiments

- Benchmark matrices over explicit agent and model targets
- Offline comparison of saved runs
- Documentation experiments that vary only the docs directory and report observed task transitions

### Isolation and CI

- Optional Docker execution with separate agent and verifier containers
- Runtime provenance on completed runs
- Opt-in GitHub Actions job summaries and step outputs
- Repository CI for unit tests and two fake Docker smoke jobs that do not use provider credentials

### Onboarding

- `agentdocs init` writes a starter benchmark for a supported agent
- `agentdocs doctor` checks config, paths, and provider or Docker availability without running a benchmark
- A zero-credit Docker smoke path is the first README example
- A first-benchmark guide in `docs/first-benchmark.md`

### Developer tooling

- Python package `agentdocsbench` 0.1.0, console script `agentdocs`
- MIT license
- Pytest suite that does not launch real provider CLIs
