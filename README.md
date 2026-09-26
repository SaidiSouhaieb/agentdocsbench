# AgentDocsBench

AgentDocsBench tests whether coding agents can use documentation to complete programming tasks.

A task is PASS or FAIL from a deterministic verifier. The agent's own claim that it is done does not decide the result. One run does not prove that a document is good, and it does not rank agents or models.

```text
docs + starter + task
        ↓
coding agent
        ↓
project edits
        ↓
deterministic verifier
        ↓
PASS / FAIL
```

## Try it in 2 minutes — no provider account required

This smoke test uses a fake coding-agent executable inside Docker. It does not call Codex, Claude, or Cursor services, and it spends no provider credits. Docker is required. The point is to verify that AgentDocsBench itself is working.

```bash
python -m pip install -e .

docker build \
  -t agentdocs-docker-smoke:local \
  examples/docker-smoke

agentdocs test \
  --config examples/docker-smoke/agentdocs.yaml \
  --runtime-config examples/docker-smoke/runtime.yaml
```

Expected result: `docker_ok` PASS, and the process exits 0.

## Why AgentDocsBench

Documentation can be clear to a person and still be hard for a coding agent to follow. AgentDocsBench checks that gap with executable tasks: the agent gets the docs and a starter project, edits the project, and a verifier checks the result.

Each task runs in its own temporary workspace. A finished run is saved as auditable artifacts. Schema and implementation notes live in [TECHNICAL.md](TECHNICAL.md).

## Documentation experiments

A documentation experiment reruns the same starter, task, verifier, agent, model, and runtime. Only the docs change. AgentDocsBench then reports the observed task transitions.

```text
Reference docs:
pagination     FAIL

Candidate docs:
pagination     PASS

Observed transition:
pagination     newly_passing
```

One run per documentation variant is an observation. It does not establish a causal effect, statistical significance, or which documentation is best.

```bash
agentdocs experiment \
  --config agentdocs.yaml \
  --experiment docs-experiment.yaml
```

A no-credit version of that comparison is in [examples/docs-experiment-smoke/](examples/docs-experiment-smoke/). Commands for it are in the full section below.

## Install

Python 3.12 or newer is required. AgentDocsBench 0.1.0 is not published to PyPI. Install from a checkout:

```bash
python -m pip install -e .
```

That install provides the `agentdocs` command.

## Create your first benchmark

```bash
agentdocs init my-benchmark --agent cursor
```

Replace `cursor` with `codex` or `claude` when that is the CLI you want the benchmark to call. The command writes a small valid benchmark. It refuses a directory that already contains files.

The step-by-step guide is [docs/first-benchmark.md](docs/first-benchmark.md).

## Check your setup

```bash
agentdocs doctor --config agentdocs.yaml
```

Doctor checks Python, the benchmark file, docs, starter, and verifier paths, and whether the provider executable or the Docker images are available. It does not run the coding agent, run the verifier, log in, or read credentials. Exit 0 means no failing check. Exit 1 means at least one check failed. A warning does not force exit 1.

## Run a benchmark

`examples/simple/` is a two-task benchmark that calls a real coding agent. `agentdocs.yaml` selects Codex, so this command needs an installed, authenticated `codex` CLI and can consume provider quota:

```bash
agentdocs test --config examples/simple/agentdocs.yaml
```

PASS on a task means that task's verifier exited 0. The table also shows the agent exit code. Those are different facts.

The same starter, docs, prompts, and verifiers are available for the other agents:

```bash
agentdocs test --config examples/simple/agentdocs.claude.yaml
agentdocs test --config examples/simple/agentdocs.cursor.yaml
```

Start with the Docker smoke test above when you do not want to use a provider account yet.

## Benchmark configuration

A benchmark file names the docs, the starter project, the coding agent, and the tasks. Relative paths resolve from the directory that contains the file.

```yaml
version: 1

docs: ./docs
starter: ./benchmark/starter

agent:
  type: codex

tasks:
  - id: create_user
    prompt: |
      Using the provided documentation,
      create a user named Alice.

    verify:
      path: ./benchmark/verifiers/create_user
      command: python3 check.py
```


| Field            | Role                                                                         |
| ---------------- | ---------------------------------------------------------------------------- |
| `docs`           | Documentation tree copied into the workspace for the agent                   |
| `starter`        | Project copied into a fresh workspace for each task                          |
| `agent.type`     | `codex`, `claude`, or `cursor`                                               |
| `agent.model`    | Optional model id. Omit it to keep the provider default                      |
| `tasks`          | Task id, prompt, and verifier, in run order                                  |
| `verify.path`    | Verifier directory, copied separately from the agent                         |
| `verify.command` | Argv such as `python3 check.py`. Pipes and shell redirects are not supported |


The verifier process receives `AGENTDOCS_PROJECT_DIR`. It passes only when its exit code is 0.

## Supported agents


| Config               | CLI AgentDocsBench runs                                  |
| -------------------- | -------------------------------------------------------- |
| `agent.type: codex`  | `codex`                                                  |
| `agent.type: claude` | `claude`                                                 |
| `agent.type: cursor` | `agent`, or `cursor-agent` when `agent` is not on `PATH` |


AgentDocsBench does not install these CLIs and does not log in to them. Authentication belongs to the provider CLI. Which models work depends on that CLI and that account. The unit tests do not need provider credentials.

## Model selection

Leave `agent.model` unset and the provider CLI keeps its default. The run header then says `Model: provider default`.

Set a model for one run without editing the file:

```bash
agentdocs test \
  --config examples/simple/agentdocs.yaml \
  --model <model-id>
```

Precedence is `--model`, then `agent.model`, then the provider default. A rejected model is not retried.

```bash
agentdocs models
agentdocs models --agent cursor
```

Discovery uses the installed CLI. Codex can dump a bundled catalog. Claude Code has no non-interactive list command. Cursor can list models for the logged-in account. A missing list does not block `--model`. AgentDocsBench does not invent a catalog or call provider HTTP APIs.

If the provider reports a resolved model, that id is stored. The requested id is not treated as confirmation, so `resolved_model` stays empty when the CLI does not report one.

## Artifacts

A completed run is written next to the benchmark file:

```text
.agentdocs/runs/<run-id>/
├── result.json
├── summary.md
├── benchmark.json
└── tasks/<nnn-task-id>/
    ├── changes.json
    ├── agent.stdout.log
    ├── agent.stderr.log
    ├── verifier.stdout.log
    └── verifier.stderr.log
```

`result.json` is the machine-readable result. `summary.md` is the human-readable summary. `benchmark.json` is the benchmark fingerprint manifest. `changes.json` lists project entries the agent added, modified, or deleted, without file contents.

The four log files are raw process output. They may contain source code, documentation text, provider messages, or other sensitive material. Review them before sharing a run. Structured artifacts omit environment values and credentials. A private image name from a Docker runtime file can still appear as `requested_image`.

```bash
agentdocs test --no-artifacts
agentdocs test --artifacts-dir ./benchmark-results
```

Those two flags cannot be combined. `.agentdocs/` is gitignored.

## Benchmark fingerprinting

`agentdocs fingerprint` hashes the benchmark definition. It does not run an agent.

```bash
agentdocs fingerprint --config examples/simple/agentdocs.yaml
```

The fingerprint covers docs, starter, tasks, and verifiers. It does not cover the coding agent, the model, or the runtime. The same inputs produce the same SHA-256 in another directory. The hash is computed when the run starts. It does not stop another process from editing those files during the run.

`agentdocs compare` reads saved runs and reports whether their fingerprints match. It does not re-run the benchmark. Schema and hashing details are in [TECHNICAL.md](TECHNICAL.md).

## Matrix runs

A matrix runs one benchmark against an explicit list of agent and model targets. Docs, starter, prompts, and verifiers stay fixed.

```yaml
version: 1
targets:
  - id: cursor-default
    agent: cursor
  - id: codex-default
    agent: codex
```

```bash
agentdocs matrix \
  --config examples/stress/agentdocs.yaml \
  --matrix examples/stress/matrix.example.yaml
```

Each completed target is a normal run. The matrix report is under `.agentdocs/matrices/<matrix-run-id>/`. The report lists what happened for each target. It does not rank targets, name a winner, or assign a score.

## Docker isolation

Local execution is the default. Pass `--runtime-config` to run the coding agent and the verifier in separate containers:

```bash
agentdocs test \
  --config examples/simple/agentdocs.yaml \
  --runtime-config runtime.yaml
```

The agent container receives the temporary project read-write at `/workspace/project` and the docs read-only at `/workspace/docs`. The verifier container runs later, with network `none`, without the docs tree, and without the agent's environment. The Docker socket is not mounted. You supply images that already contain the provider CLI and the verifier command. AgentDocsBench does not pull images or install those CLIs.

Docker is stronger isolation than a host process. It is not a perfect security boundary. The daemon is trusted, and anything you mount or pass through the environment is visible to the agent container.

`examples/docker-smoke/` is the no-provider infrastructure check. Build and run commands are in [examples/docker-smoke/README.md](examples/docker-smoke/README.md).

## GitHub Actions

`--github-actions` appends a job summary to `GITHUB_STEP_SUMMARY` and step outputs to `GITHUB_OUTPUT`. It does not call the GitHub API, upload artifacts, or manage provider credentials. The flag is opt-in: `GITHUB_ACTIONS=true` alone does not turn it on.

```yaml
- uses: actions/checkout@v7
- uses: actions/setup-python@v7
  with:
    python-version: "3.12"
- name: Install AgentDocsBench
  run: python -m pip install -e /path/to/agentdocsbench
- run: |
    agentdocs test \
      --config path/to/agentdocs.yaml \
      --runtime-config path/to/runtime.yaml \
      --github-actions
```

AgentDocsBench 0.1.0 is not on PyPI, so that install path has to be a checkout or another location you control. [examples/github-actions/benchmark.yml](examples/github-actions/benchmark.yml) is a template. It is not registered under `.github/workflows/` here.

This repository's [CI workflow](.github/workflows/ci.yml) runs the unit tests and two fake Docker jobs. Those jobs use no provider credentials. Upload structured artifacts yourself if you want them retained, and leave the raw log files out of that upload.

## Documentation experiments

`agentdocs experiment` runs one benchmark against two or more documentation directories. The coding agent, requested model, runtime, starter, tasks, prompts, and verifiers stay constant. Only `docs` changes.

```yaml
version: 1

reference: current

variants:
  - id: current
    docs: ./docs/current

  - id: candidate
    docs: ./docs/candidate
```

```bash
agentdocs experiment \
  --config path/to/agentdocs.yaml \
  --experiment path/to/docs-experiment.yaml
```

`reference` is the comparison variant. It is not an approved baseline. Variants run in file order. Each other variant is compared only with the reference.

If the reference run records `pagination` as FAIL and the candidate records it as PASS, the observed transition is `newly_passing`.


| Reference                                       | Candidate | Transition       |
| ----------------------------------------------- | --------- | ---------------- |
| PASS                                            | PASS      | `unchanged_pass` |
| FAIL                                            | FAIL      | `unchanged_fail` |
| FAIL                                            | PASS      | `newly_passing`  |
| PASS                                            | FAIL      | `newly_failing`  |
| missing, incomplete, or blocking on either side |           | `not_comparable` |


A blocking provider failure is `not_comparable`. It is not recorded as newly failing. The docs change list names added, removed, and modified paths. It does not include file contents, and it does not say that a docs edit caused a task result.

One run per variant is an observation. It is not a statistical test and it is not proof that the documentation caused the outcome. There is no regression gate: a completed experiment exits 0 even when a task is newly failing.

A no-provider Docker example uses a fake agent. It does not call Codex, Claude, or Cursor, and it spends no provider credits:

```bash
docker build \
  -t agentdocs-docs-experiment-smoke:local \
  examples/docs-experiment-smoke

agentdocs experiment \
  --config examples/docs-experiment-smoke/agentdocs.yaml \
  --experiment examples/docs-experiment-smoke/docs-experiment.yaml \
  --runtime-config examples/docs-experiment-smoke/runtime.yaml
```

Expected result: reference `FAIL`, candidate `PASS`, `newly_passing` for `docs_task`, and exit 0. Details are in [examples/docs-experiment-smoke/README.md](examples/docs-experiment-smoke/README.md).

## Exit codes

`agentdocs test`:


| Code | Meaning                                                   |
| ---- | --------------------------------------------------------- |
| 0    | The suite completed and every verifier passed             |
| 1    | The suite completed and at least one verifier failed      |
| 2    | A blocking provider failure, or an infrastructure failure |


`agentdocs matrix` uses the same three codes across targets: 0 when every target completed and passed, 1 when every target completed and a verifier failed, and 2 when a target had a blocking provider failure or an infrastructure error.

`agentdocs experiment`:


| Code | Meaning                                                                              |
| ---- | ------------------------------------------------------------------------------------ |
| 0    | Every variant completed, including when verifiers failed or a task was newly failing |
| 2    | A variant had a blocking provider failure or an infrastructure error                 |


`agentdocs experiment` does not use exit 1. `agentdocs compare` and `agentdocs fingerprint` exit 0 when the report is printed and 2 when an input cannot be read.

`agentdocs doctor` is a diagnostic command, not a benchmark run:


| Code | Meaning                                      |
| ---- | -------------------------------------------- |
| 0    | No check failed. Warnings do not change this |
| 1    | One or more checks failed                    |


`agentdocs init` exits 2 when it refuses the target directory or the agent settings are invalid. It does not use the benchmark PASS/FAIL codes.

## Security and privacy

AgentDocsBench does not manage provider credentials. Authentication stays with the provider CLI or with mounts and environment names you configure yourself.

Structured artifacts do not store environment values or credential contents. Documentation-experiment artifacts do not store docs file contents or task prompts.

These logs can contain source code, provider messages, file contents, and other sensitive output:

- `agent.stdout.log`
- `agent.stderr.log`
- `verifier.stdout.log`
- `verifier.stderr.log`

Local execution is not a strong sandbox. Docker mounts are explicit, the Docker socket is not mounted, and the verifier network is `none`. Docker is still not a perfect security boundary. More guidance is in [SECURITY.md](SECURITY.md).

## Limitations

- Coding agents and providers can be nondeterministic.
- One experiment run is not statistical proof and not a causal claim.
- Local execution is weaker isolation than Docker.
- A provider-default model may never report which model actually ran.
- A benchmark fingerprint is not a filesystem transaction.
- There are no automatic retries, provider fallback, or model fallback.
- There is no token or cost accounting.
- Matrix and experiment reports do not rank, score, or name a winner.
- Repeated trials and regression gating are not part of this release.

## Development

```bash
python -m pip install -e .
pytest
```

The unit tests do not launch Codex, Claude, or Cursor, and they do not require a Docker daemon. The fake Docker jobs in CI are separate from `pytest`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The release checklist is in [RELEASE.md](RELEASE.md).

## License

AgentDocsBench is released under the [MIT License](LICENSE).