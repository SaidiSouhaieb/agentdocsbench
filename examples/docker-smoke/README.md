# Docker smoke

This fixture does not call Codex, Claude, or Cursor. The image provides a fake `agent` executable and `python3`.

Build it yourself. AgentDocsBench does not build or pull images.

```bash
docker build -t agentdocs-docker-smoke:local examples/docker-smoke

agentdocs test \
  --config examples/docker-smoke/agentdocs.yaml \
  --runtime-config examples/docker-smoke/runtime.yaml
```

The fake agent writes `result.txt` containing `DOCKER_OK`. It also leaves `isolation-error.txt` if `/workspace/verifier` is visible or `/workspace/docs` is writable. The verifier fails when that marker exists, and it fails if `/workspace/docs` is visible in the verifier container.
