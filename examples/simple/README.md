# Simple benchmark

Two tasks, one starter project, and a short docs tree. This example calls a real coding agent.

`agentdocs.yaml` uses Codex. The Claude Code and Cursor Agent variants use the same docs, starter, prompts, and verifiers:

```bash
agentdocs test --config examples/simple/agentdocs.yaml
agentdocs test --config examples/simple/agentdocs.claude.yaml
agentdocs test --config examples/simple/agentdocs.cursor.yaml
```

Each command needs that provider's CLI installed and authenticated. A run can consume provider quota or credits.

`agentdocs init my-benchmark --agent codex` creates a separate starter benchmark. This example does not depend on that command.

PASS means the verifier exited 0. The agent exit code is recorded separately.

For a run that does not call a provider, use [examples/docker-smoke/README.md](../docker-smoke/README.md).
