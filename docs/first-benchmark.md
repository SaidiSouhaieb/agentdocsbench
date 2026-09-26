# Build your first AgentDocsBench benchmark

This guide creates a small benchmark, checks the machine, and runs it. Internals such as artifact schemas and Docker implementation live in [TECHNICAL.md](../TECHNICAL.md).

A benchmark looks like this:

```text
my-benchmark/
├── agentdocs.yaml
├── docs/
├── benchmark/
│   ├── starter/
│   └── verifiers/
```

`docs/` is what the coding agent is expected to read. `benchmark/starter/` is the project copied into a fresh workspace for every task. `benchmark/verifiers/` decides PASS or FAIL.

## 1. Create the scaffold

From a checkout where `agentdocs` is installed:

```bash
agentdocs init my-benchmark --agent cursor
```

Use `codex` or `claude` instead of `cursor` when that is the provider CLI you want. Add `--model <model-id>` only when you want a specific model. Omit it to keep the provider default.

The command refuses a directory that already contains files. It does not overwrite them.

## 2. Look at the generated files

- `agentdocs.yaml` names the docs, the starter, the agent, and one task.
- `docs/getting-started.md` tells the agent to create `result.txt` containing `HELLO_AGENTDOCS`.
- `benchmark/starter/README.md` is the starter project. Every task gets a fresh copy.
- `benchmark/verifiers/first_task/check.py` checks that file.
- `README.md` repeats the next two commands.

## 3. Put your documentation in docs/

Replace the generated page with the material the coding agent should use. Paths in `agentdocs.yaml` are relative to that file, so `docs: ./docs` means the `docs` directory next to `agentdocs.yaml`.

## 4. Put starting code in benchmark/starter/

Put the project the agent should edit here. AgentDocsBench copies this directory into a new temporary workspace for each task. Edits from one task do not carry into the next.

## 5. Write a task

The generated task is:

```yaml
tasks:
  - id: first_task
    prompt: |
      Using the provided documentation, complete the requested task.

    verify:
      path: ./benchmark/verifiers/first_task
      command: python3 check.py
```

- `id` is the task name in the report.
- `prompt` is the instruction sent to the coding agent, along with the docs and the starter copy.
- `verify.path` is the verifier directory. It is relative to `agentdocs.yaml`, and it is copied separately from the project the agent edits.
- `verify.command` is the command AgentDocsBench runs inside that directory. `python3 check.py` matches the generated verifier.

## 6. Write a deterministic verifier

The verifier decides PASS or FAIL. The agent saying it is done does not mean PASS.

The generated `check.py` reads `AGENTDOCS_PROJECT_DIR`, requires `result.txt`, and requires the stripped contents to be exactly `HELLO_AGENTDOCS`. Exit 0 is PASS. Any other exit code is FAIL.

Keep verifiers deterministic. The standard library is enough for this starter. Do not call the network from the verifier unless that is the behavior you intend to test.

## 7. Check your environment

```bash
cd my-benchmark
agentdocs doctor --config agentdocs.yaml
```

Doctor reports OK, WARN, FAIL, or SKIP. It checks the config and the directories, and it looks for the provider executable on `PATH`. It does not run the agent, run the verifier, or check whether you are logged in.

Install and authenticate the provider CLI yourself when a real run needs it. `agentdocs doctor` will say that authentication is not checked.

If you later add `--runtime-config`, doctor checks Docker and the local images instead of the host provider executable. It does not pull or build images.

## 8. Run

```bash
agentdocs test --config agentdocs.yaml
```

This calls the provider CLI named in `agentdocs.yaml`. A real provider can consume quota or credits. The Docker smoke test in the [README](../README.md) is the run that does not.

## 9. Read the result

The table shows one row per task.

- PASS means the verifier exited 0.
- FAIL means the verifier exited nonzero. The suite still completed.
- A provider error means the coding agent did not finish in a way AgentDocsBench can treat as a completed attempt.
- An infrastructure error means something outside the task failed, such as a missing executable, a missing Docker image, or a config problem.

`agentdocs test` exits 0 when every verifier passed, 1 when a verifier failed, and 2 for a provider or infrastructure failure.

## 10. Inspect artifacts

A completed run is written under `.agentdocs/runs/`. `result.json` and `summary.md` are the structured result. `changes.json` lists project paths the agent added, modified, or deleted.

These files are raw process output and can contain source code, documentation, or provider messages:

- `agent.stdout.log`
- `agent.stderr.log`
- `verifier.stdout.log`
- `verifier.stderr.log`

Read them before you share a run.

## 11. Compare documentation versions

To ask whether a docs change is associated with a different verifier result, keep the starter, tasks, prompts, verifiers, agent, model, and runtime the same, and vary only the docs directory:

```bash
agentdocs experiment \
  --config agentdocs.yaml \
  --experiment docs-experiment.yaml
```

The report names observed transitions such as `newly_passing` and `newly_failing`. One run per variant is an observation. It is not a causal claim and it is not a statistical test.

The short example and the no-credit Docker experiment are in the [README](../README.md).
