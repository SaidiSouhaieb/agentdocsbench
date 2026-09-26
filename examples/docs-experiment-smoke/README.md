# Documentation experiment smoke

This fixture runs the same one-task benchmark against two documentation directories. The image contains a fake `agent`. It reads `instruction.txt` and does not call a provider.

Reference docs say `WRITE OLD_RESULT`. Candidate docs say `WRITE EXPECTED_RESULT`. The verifier expects `EXPECTED_RESULT`, so the reference variant fails verification and the candidate variant passes. The experiment still exits 0. A verifier transition is an observation, not a regression gate.

```bash
docker build -t agentdocs-docs-experiment-smoke:local examples/docs-experiment-smoke

agentdocs experiment \
  --config examples/docs-experiment-smoke/agentdocs.yaml \
  --experiment examples/docs-experiment-smoke/docs-experiment.yaml \
  --runtime-config examples/docs-experiment-smoke/runtime.yaml

echo $?
```

Expected exit code: `0`.

Expected report: reference `FAIL`, candidate `PASS`, `newly_passing` for `docs_task`, `newly_failing` 0, `not_comparable` 0.
