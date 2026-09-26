# GitHub Actions template

`benchmark.yml` is an example for your repository. It does not run in the AgentDocsBench repository. Copy it to `.github/workflows/agentdocs.yml` and replace the image build, config path, runtime path, and install path.

AgentDocsBench 0.1.0 is not published to PyPI. The template installs from `/path/to/agentdocsbench`. Point that at a checkout, or replace it with a git URL you control.

The workflow calls the same `agentdocs test` command used locally, with `--github-actions`. That flag writes the job summary and step outputs. It does not upload artifacts, call the GitHub API, or manage provider credentials. The upload step is separate and lists structured files only.

## Runtime file

Keep secret values out of `runtime.yaml`. List the variable name, and let the workflow pass the secret:

```yaml
backend: docker

agents:
  cursor:
    image: my-agent:local
    network: bridge
    env_passthrough:
      - PROVIDER_TOKEN

verifier:
  image: my-verifier:local
```

```yaml
env:
  PROVIDER_TOKEN: ${{ secrets.PROVIDER_TOKEN }}
```

Do not write `${{ secrets.PROVIDER_TOKEN }}` inside `runtime.yaml`. AgentDocsBench would treat that text as a variable name, and a copied config could persist it.

The agent image is yours. It needs the provider CLI installed before the benchmark starts. The verifier image needs the verifier command, such as `python3`. The verifier container network is `none`. The Docker socket is not mounted into either container. A private registry name in `image` can appear in the job summary. Registry credentials do not.

## Running a provider CLI on the runner

You can omit `--runtime-config` and run the provider CLI on the GitHub-hosted runner. That means installing the CLI, authenticating it, and accepting that the agent process has the runner's access. AgentDocsBench does not install those CLIs or log in to them.

Docker is the tighter boundary when you can supply an image.

## Fork pull requests

GitHub does not pass normal repository secrets to workflows triggered by untrusted fork pull requests. Run a real-provider benchmark from a trusted branch, `workflow_dispatch`, or an environment that matches your security model.

Do not use `pull_request_target` to execute a pull request's benchmark or generated code with repository secrets. That pattern runs untrusted code in a trusted context.
