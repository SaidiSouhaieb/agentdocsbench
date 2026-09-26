# Nimbus stress benchmark

This is an advanced 15-task benchmark for the fictional Nimbus SDK. It is separate from `examples/simple/`. A real-provider run can consume a large amount of provider quota or credits.

```bash
agentdocs test --config examples/stress/agentdocs.yaml
```

`agentdocs.yaml` selects Codex. Each task starts from `benchmark/starter` in a fresh workspace. The docs under `docs/` are the only product specification. Verifiers check behavior locally and do not use the network.

An optional matrix of provider-default targets, without pinned model ids:

```bash
agentdocs matrix \
  --config examples/stress/agentdocs.yaml \
  --matrix examples/stress/matrix.example.yaml
```

The matrix report lists measurements for each target. It does not rank them.

`STRESS_TEST_REPORT.md` is one saved local observation. It is not a score for the product or the agent.
