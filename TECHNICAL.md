# AgentDocsBench technical notes

This document records what has been built so far, in the order it was built.

AgentDocsBench tests whether coding agents such as Codex or Claude Code can use a product's documentation to complete programming tasks.

The project currently includes configuration, workspace isolation, Codex, Claude Code, and Cursor Agent adapters, optional model selection, a deterministic verifier, a single-task runner, a suite runner, a CLI, persistent run artifacts, live task progress, explicit benchmark matrices with offline run comparison, a content fingerprint of the benchmark definition, a content-free manifest of the project entries the agent changed before verification, a conservative classification of known provider execution failures, an optional Docker runtime that runs the agent and the verifier in separate containers, GitHub Actions reporting for `agentdocs test`, `agentdocs matrix`, and `agentdocs experiment`, and documentation-version experiments that run one agent, model, and runtime against two or more docs directories. Local execution remains the default. The sections below are the historical build notes for each layer. They describe what was true when that layer was added. Run-history commands are not implemented. Step 18 is the last implemented feature.

## Step 1. Project layout

The repository is a Python package named `agentdocs`, distributed as `agentdocsbench` version `0.1.0`.

```
.
├── agentdocs/
│   ├── __init__.py
│   └── config.py
├── examples/
│   └── simple/
│       └── agentdocs.yaml
├── tests/
│   └── test_config.py
├── pyproject.toml
├── README.md
└── TECHNICAL.md
```

| Path | Role |
| --- | --- |
| `agentdocs/config.py` | Models, loader, and filesystem checks |
| `agentdocs/__init__.py` | Public exports |
| `examples/simple/agentdocs.yaml` | Example project file |
| `tests/test_config.py` | Pytest coverage for this layer |
| `pyproject.toml` | Package metadata, dependencies, pytest settings |
| `README.md` | Short user-facing overview |

## Step 2. Tooling

`pyproject.toml` requires Python 3.12 or newer and uses setuptools.

Runtime and test dependencies for this stage:

- `pydantic>=2` for the config models
- `PyYAML>=6` for `yaml.safe_load`
- `pytest>=8` so `pip install -e .` is enough to run the suite

Pytest is configured to collect `tests/` and to put the repository root on `pythonpath`.

Install and test:

```bash
pip install -e .
pytest
```

The suite currently has 16 passing tests. Every test builds its files under pytest's `tmp_path`. None of them read the example project or any other path outside the test.

## Step 3. The config file

A project is described by one YAML file, `agentdocs.yaml`. The example in `examples/simple/agentdocs.yaml` is:

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
      modify the project so it creates
      a user named Alice.

    verify:
      command: pytest tests/test_user.py

  - id: enable_oauth
    prompt: |
      Using the provided documentation,
      configure Google OAuth.

    verify:
      command: pytest tests/test_oauth.py
```

Field meaning:

| Field | Meaning |
| --- | --- |
| `version` | Config schema version. Only `1` is accepted. |
| `docs` | Directory of product documentation the agent will read later. |
| `starter` | Directory of the starter project the agent will modify later. |
| `agent.type` | Which coding agent will run the tasks. Only `codex` is accepted. |
| `tasks` | One or more tasks. The list cannot be empty. |
| `tasks[].id` | Unique task id. Blank ids are rejected. |
| `tasks[].prompt` | Instructions for that task. Blank prompts are rejected. |
| `tasks[].verify.command` | Command that will later check the task. Blank commands are rejected. The command is stored only. It is not executed. |

Unknown keys are rejected on every model (`extra="forbid"`).

## Step 4. Models

All models live in `agentdocs/config.py`. They are Pydantic `BaseModel`s.

Supported values are module-level sets so a later version or agent is a one-line addition:

```python
SUPPORTED_VERSIONS: frozenset[int] = frozenset({1})
SUPPORTED_AGENT_TYPES: frozenset[str] = frozenset({"codex"})
```

`agent.type` is a `str` checked against `SUPPORTED_AGENT_TYPES`, not a `Literal`. Adding `claude` later means adding `"claude"` to that set.

### `AgentConfig`

- Field: `type: str`
- The value is stripped.
- Anything other than `codex` raises `Unsupported agent type '<value>'. Supported agent types: codex.`

### `VerifyConfig`

- Field: `command: str`
- Stripped. An empty result raises `Verifier command must not be empty.`

### `TaskConfig`

- Fields: `id: str`, `prompt: str`, `verify: VerifyConfig`
- `id` and `prompt` are stripped.
- An empty id raises `Task id must not be empty.`
- An empty prompt raises `Task prompt must not be empty.`

### `AgentDocsConfig`

- Fields: `version: int`, `docs: Path`, `starter: Path`, `agent: AgentConfig`, `tasks: list[TaskConfig]`
- `version` must be in `SUPPORTED_VERSIONS`. Version `2` raises `Unsupported version 2. Supported versions: 1.`
- `tasks` must contain at least one item: `At least one task is required.`
- After each task is valid, a model validator walks the ids. A repeated id raises `Duplicate task id(s): '<id>'. Task ids must be unique.` Ids are compared after stripping, so surrounding whitespace does not create a second id.

These models do not check whether `docs` or `starter` exist on disk.

Blank-string checks share `_require_text()`. It strips the value, rejects it when nothing remains, and stores the stripped text.

## Step 5. Loading

Public function:

```python
def load_config(path: str | Path = "agentdocs.yaml") -> AgentDocsConfig:
```

`load_config()` does five things, in this order:

1. Confirm the path exists. A missing path raises `FileNotFoundError` with `Config file not found: <path>`.
2. Confirm the path is a file. A directory raises `ConfigError` with `Config path is not a file: <path>`.
3. Read the file as UTF-8 and parse it with `yaml.safe_load`.
4. Validate the mapping with `AgentDocsConfig.model_validate`.
5. Resolve `docs` and `starter`, then return a copy of the model with those absolute paths.

YAML reading is `_read_yaml_mapping()`:

- A parse error raises `ConfigError`: `Invalid YAML in <path>: ...`
- An empty file (`safe_load` returns `None`) raises `ConfigError`: `Config file is empty: <path>`
- A list or other non-mapping raises `ConfigError`: `Config file must contain a YAML mapping, not <type>: <path>`

Schema failures are caught as Pydantic `ValidationError` and re-raised as `ConfigError`. `_format_validation_error()` turns each error into `<location>: <message>`, drops Pydantic's `Value error, ` prefix, and includes the config path:

```text
Invalid configuration in /my-project/agentdocs.yaml:
version: Unsupported version 2. Supported versions: 1.
```

`ConfigError` subclasses `ValueError`.

## Step 6. Path resolution

Resolution happens only in `load_config()`, after schema validation. The base directory is `config_path.resolve().parent`: the directory that contains the YAML file.

`_resolve_against_config_dir()`:

- If the path is absolute, return `path.resolve()`.
- If the path is relative, return `(base_dir / path).resolve()`.

The process working directory is not used. This is the behavior that matters:

```text
/my-project/
    agentdocs.yaml
    docs/
    benchmark/
        starter/
```

Loading `/my-project/agentdocs.yaml` turns `docs: ./docs` into `/my-project/docs` and `starter: ./benchmark/starter` into `/my-project/benchmark/starter`, even if the shell is in another directory.

`Path.resolve()` is called with the default `strict=False`, so a relative path is still made absolute when the directory does not exist yet. Existence is a separate step.

## Step 7. Filesystem validation

Public function:

```python
def validate_config_paths(config: AgentDocsConfig) -> None:
```

It checks `config.docs` and then `config.starter` through `_require_directory()`:

| Condition | Exception | Message |
| --- | --- | --- |
| Path does not exist | `FileNotFoundError` | `<label> directory does not exist: <path>` |
| Path exists and is not a directory | `NotADirectoryError` | `<label> path is not a directory: <path>` |

`label` is `docs` or `starter`. `load_config()` does not call this function. A config can be loaded before the directories are created. Call `validate_config_paths()` when they must already be present.

## Step 8. Public API

`agentdocs/__init__.py` exports:

- `AgentConfig`
- `VerifyConfig`
- `TaskConfig`
- `AgentDocsConfig`
- `ConfigError`
- `load_config`
- `validate_config_paths`

Typical use:

```python
from agentdocs import load_config, validate_config_paths

config = load_config("/my-project/agentdocs.yaml")
validate_config_paths(config)
```

`SUPPORTED_VERSIONS` and `SUPPORTED_AGENT_TYPES` stay in `agentdocs.config`. They are the extension points, and they are not re-exported from the package root.

## Step 9. Tests

`tests/test_config.py` covers:

| Test | What it checks |
| --- | --- |
| `test_valid_config_loads` | Version, agent, both tasks, prompts, commands, resolved paths, and a successful `validate_config_paths()` |
| `test_relative_paths_resolve_against_config_directory` | `docs` and `starter` follow the config file after `chdir` to another directory |
| `test_absolute_paths_stay_absolute` | Absolute `docs` and `starter` stay on those paths |
| `test_missing_config_file` | `FileNotFoundError` |
| `test_invalid_yaml` | `ConfigError` containing `Invalid YAML` |
| `test_unsupported_version` | Version `2` |
| `test_unsupported_agent` | Agent type `claude` |
| `test_empty_tasks` | `tasks: []` |
| `test_duplicate_task_ids` | Two tasks with id `create_user` |
| `test_empty_task_id` | `id: ""` |
| `test_empty_prompt` | `prompt: ""` |
| `test_empty_verifier_command` | `command: ""` |
| `test_missing_docs_directory` | Load succeeds; `validate_config_paths()` raises `FileNotFoundError` |
| `test_missing_starter_directory` | Same split for `starter` |
| `test_docs_path_must_be_a_directory` | A file at `docs` raises `NotADirectoryError` |
| `test_starter_path_must_be_a_directory` | A file at `starter` raises `NotADirectoryError` |

Helpers `_write_config()` and `_quoted()` only write files inside `tmp_path`.

## Step 10. What this layer deliberately leaves out

The verifier command is data. Nothing shells out. Nothing copies `starter`, reads `docs`, calls an agent, or writes a report.

The next layer should start from a loaded `AgentDocsConfig` whose paths have already been validated. That work has not been started.

# Step 2. Workspace layer

Step 2 adds isolated temporary workspaces. It does not run agents, execute verifier commands, or modify the configuration API.

New files:

| Path | Role |
| --- | --- |
| `agentdocs/workspace.py` | `Workspace`, `WorkspaceError`, and `create_workspace()` |
| `tests/test_workspace.py` | Workspace creation, copying, cleanup, and symlink tests |

`agentdocs/__init__.py` exports the new names and still exports every Step 1 name.

## Workspace

`Workspace` is a frozen dataclass:

| Field | Meaning |
| --- | --- |
| `root` | Temporary directory, for example `/tmp/agentdocs-abcd1234` |
| `project` | `root / "project"`, a copy of `config.starter` |
| `docs` | `root / "docs"`, a copy of `config.docs` |

The resulting tree is:

```text
/tmp/agentdocs-xxxx/
├── project/
│   ├── app.py
│   └── requirements.txt
└── docs/
    ├── users.md
    └── auth.md
```

Paths on the dataclass are absolute.

## create_workspace()

```python
from agentdocs import create_workspace, load_config

config = load_config("agentdocs.yaml")

with create_workspace(config) as workspace:
    print(workspace.project)
    print(workspace.docs)
```

`create_workspace()` is a context manager. Its order of work is:

1. Call `validate_config_paths(config)`. A missing or non-directory `docs` or `starter` still raises `FileNotFoundError` or `NotADirectoryError`.
2. Walk `config.starter` and `config.docs` without following links. Any symbolic link, including a symlink at the source root, raises `WorkspaceError`.
3. Create a temporary directory with `tempfile.TemporaryDirectory(prefix="agentdocs-")`.
4. `shutil.copytree()` the starter into `root / "project"`.
5. `shutil.copytree()` the docs into `root / "docs"`.
6. Yield a `Workspace`.
7. Delete the temporary directory when the `with` block ends.

Copying uses `symlinks=False` as a second guard, but the walk rejects links before the copy starts. There is no separate ignore list: nested directories, ordinary files, and hidden files such as `.env.example` and `.gitignore` are copied. Filenames and file contents are preserved. `.git` is not treated specially and Git is not initialized.

An `OSError` from `copytree` propagates. The temporary directory is still removed because cleanup belongs to `TemporaryDirectory`.

The original starter and docs are only read. Editing `workspace.project` or `workspace.docs` does not change them. Two successive `with` blocks get two new copies, so a previous workspace cannot leak edits into the next one.

## Symlink policy

Symbolic links are rejected before a temporary directory is created. The error is:

```text
Workspace source contains symbolic link:
<path>

Symbolic links are not supported in AgentDocsBench workspaces yet.
```

`os.walk(..., followlinks=False)` lists a link and does not descend into a linked directory. The workspace therefore cannot be pointed at files outside the source tree by a symlink. Safe symlink support can be designed later.

`WorkspaceError` subclasses `RuntimeError`.

## Cleanup

Entering the context creates the directory. Leaving it deletes `workspace.root`, whether the block returns or raises. Callers should not expect the directory to exist after the `with` statement.

## Tests added

`tests/test_workspace.py` builds sources under pytest's `tmp_path`. It checks:

- `root`, `project`, and `docs` exist inside the context, with `project` and `docs` as children of `root`
- starter files, including nested files, are copied
- documentation files, including nested files, are copied
- copied bytes match the originals
- `.env.example` and `.gitignore` are copied
- editing the workspace copy leaves the original starter unchanged
- editing the workspace copy leaves the original docs unchanged
- `root`, `project`, and `docs` are absolute
- `root` is gone after a normal exit
- `root` is gone after an exception inside the block
- deleting `starter` or `docs` after config construction makes `create_workspace()` raise `FileNotFoundError`
- a symlink under `starter` or `docs` raises `WorkspaceError` (the test skips if the platform cannot create symlinks)
- a second workspace still contains the original starter contents after the first workspace was modified

## Deliberately not implemented

Step 2 does not execute Codex or Claude, does not run verifier commands, does not build reports, and does not add Docker, a CLI, or Git. The next step is an agent adapter. It is not part of this layer.

# Step 3. Codex adapter

Step 3 launches the real Codex CLI inside `workspace.project` and returns a process result. It does not run `task.verify.command`, does not mark a task pass or fail, and does not add a CLI runner.

New files:

| Path | Role |
| --- | --- |
| `agentdocs/agents/base.py` | `AgentAdapter`, `AgentRunResult`, and agent errors |
| `agentdocs/agents/codex.py` | `CodexAdapter` |
| `agentdocs/agents/__init__.py` | Adapter exports |
| `tests/test_codex_adapter.py` | Mocked command, parsing, and failure tests |

Existing config and workspace exports stay in place.

## AgentAdapter

`AgentAdapter` is a small abstract class with one method:

```python
def run(
    self,
    prompt: str,
    project_dir: Path,
    docs_dir: Path,
) -> AgentRunResult:
```

The adapter receives the temporary workspace paths only. It does not see `config.starter` or `config.docs`. A later Claude adapter can implement the same method. Claude is not implemented.

## AgentRunResult

`AgentRunResult` is a frozen dataclass:

| Field | Meaning |
| --- | --- |
| `agent` | `"codex"` for this adapter |
| `exit_code` | Process return code, including non-zero |
| `events` | JSON objects parsed from stdout, in order |
| `stdout` | Raw stdout |
| `stderr` | Raw stderr |
| `duration_seconds` | Wall time spent in `subprocess.run`, from `time.perf_counter` |

The environment is not copied onto the result. A non-zero Codex exit is still a result. The benchmark runner can record it later. Library exceptions are reserved for AgentDocsBench failures: missing executable, timeout, and unreadable output.

## CodexAdapter

```python
adapter = CodexAdapter(timeout_seconds=600)
result = adapter.run(prompt, workspace.project, workspace.docs)
```

`timeout_seconds` defaults to 600. Zero and negative values raise `ValueError`.

`run()` order:

1. Reject a blank prompt with `ValueError`.
2. Require `project_dir` and `docs_dir` to exist and be directories (`FileNotFoundError`, `NotADirectoryError`).
3. Find `codex` with `shutil.which`. If it is missing, raise `AgentExecutableNotFoundError` and do not install Codex.
4. Build the prompt in `_build_prompt`.
5. Run the argument list with `subprocess.run`. `shell` is `False`. `cwd` is `project_dir`. stdout and stderr are captured as text. The process inherits the current environment, so Codex uses the user's existing CLI login. AgentDocsBench does not read, store, or log API keys.
6. On `subprocess.TimeoutExpired`, raise `AgentTimeoutError` naming Codex, the timeout, and the project path.
7. Parse stdout as JSONL and return `AgentRunResult`.

A non-zero return code does not raise.

## Command

The generated argument list is:

```text
<codex> exec --json --ephemeral --skip-git-repo-check --sandbox workspace-write <prompt>
```

`<codex>` is the path returned by `shutil.which("codex")`. The prompt is the final argument, not a shell string.

`--full-auto` is not used. Current Codex CLI versions replaced that flag with an explicit sandbox, and this adapter sets `--sandbox workspace-write`. `--yolo` and `--dangerously-bypass-approvals-and-sandbox` are not used. The temporary project is not a Git repository, so the command passes `--skip-git-repo-check`. `--ephemeral` asks Codex for a session that is not kept. `--json` asks for JSONL events on stdout.

## Prompt

`_build_prompt` tells Codex that this is an AgentDocsBench evaluation, quotes the task, and gives the absolute `docs_dir` path. It tells Codex to read the docs as needed, modify only the project in the current working directory, not modify the documentation, not leave the workspace, and stop when the task is done.

Docs stay a sibling of the project:

```text
/tmp/agentdocs-xxxx/
├── project/    ← cwd
└── docs/       ← absolute path in the prompt
```

`--add-dir` is not passed. This assumes `workspace-write` can still read that absolute docs path. If a real Codex run cannot read the sibling docs directory, the smallest follow-up is to add `--add-dir <docs_dir>` while keeping `cwd` on the project. That flag was not added in this step because the unit tests do not launch Codex, so the sandbox behavior is unverified. If `--add-dir` makes the docs writable, that limitation should be recorded before relying on it. Docs were not copied into the project to keep them conceptually read-only.

## JSONL

`_parse_jsonl` splits stdout on lines. Blank lines are skipped. Each remaining line is `json.loads`. A decode error raises `AgentOutputError` with the line number and a short whitespace-collapsed preview, capped at 80 characters. A JSON value that is not an object also raises `AgentOutputError`. Valid objects are returned as a tuple.

## Errors

| Exception | When |
| --- | --- |
| `ValueError` | Blank prompt, or `timeout_seconds <= 0` |
| `FileNotFoundError` | `project_dir` or `docs_dir` is missing |
| `NotADirectoryError` | Either path exists and is not a directory |
| `AgentExecutableNotFoundError` | `codex` is not on `PATH` |
| `AgentTimeoutError` | The process exceeds `timeout_seconds` |
| `AgentOutputError` | Stdout is not JSONL objects |

`AgentError` is the base of the three agent exceptions. Codex exiting non-zero is not one of them.

## Tests

`tests/test_codex_adapter.py` patches `shutil.which` and `subprocess.run` inside `agentdocs.agents.codex`. `pytest` does not launch Codex, use the network, or require a login. The tests check the argument list, `cwd`, `shell=False`, prompt text, docs path, JSONL parsing, preserved stdout and stderr, exit code, non-negative duration, non-zero exit as a result, missing executable, timeout, bad JSONL, blank lines, blank prompt, and missing or non-directory project and docs paths.

No opt-in live Codex test is included, so the default suite cannot spend API credits.

## Deliberately not implemented

Step 3 does not execute verifier commands, score tasks, run a multi-task loop, add a CLI, support Claude, initialize Git, or sandbox with Docker. Authentication stays entirely with the installed Codex CLI.

# Step 4. Verifier layer

Step 4 checks whether the edited temporary project actually meets the task. It does not run Codex, loop over tasks, or add a CLI. Codex exit code 0 is not a pass. Only `VerifierResult.passed` is.

## VerifyConfig

`VerifyConfig` now has two fields:

| Field | Meaning |
| --- | --- |
| `path` | Directory of verifier source, outside the starter project |
| `command` | Direct command, stored as text and not executed during loading |

Blank commands are still rejected. Version stays `1`.

`load_config()` resolves `docs`, `starter`, and every `task.verify.path` against the directory that contains `agentdocs.yaml`. Absolute paths stay absolute. Resolution uses the same `model_copy` pattern as `docs` and `starter`: each task is copied with a copied `VerifyConfig` whose `path` is resolved.

`validate_config_paths()` still checks `docs` and `starter`, then checks each `task.verify.path`. A missing directory raises `FileNotFoundError` with the task id. A non-directory raises `NotADirectoryError`. The command is not checked for an executable at config time.

Example layout:

```text
my-product/
├── agentdocs.yaml
├── docs/
└── benchmark/
    ├── starter/
    └── verifiers/
        └── create_user/
            └── check.py
```

The agent workspace receives copies of `starter` and `docs` only. The verifier directory is not copied into `workspace.project` and is not named in the Codex prompt.

## run_verifier()

```python
verification = run_verifier(task.verify, workspace.project, timeout_seconds=60)
```

Order of work:

1. Reject `timeout_seconds <= 0` with `ValueError`. The default is 60.
2. Require `project_dir` to exist and be a directory.
3. Require `verify.path` to exist and be a directory, even if `validate_config_paths()` already did.
4. Parse `verify.command` with `shlex.split`. No arguments, or a parse error from bad quotes, raises `VerifierCommandError`.
5. Reject symbolic links under `verify.path` with `VerifierError`. Links are not followed. Detection is shared with the workspace layer through `find_symlink()` in `agentdocs/_symlinks.py`. The public workspace API is unchanged.
6. Create `tempfile.TemporaryDirectory(prefix="agentdocs-verifier-")`.
7. `shutil.copytree` the verifier source to `<temp>/verifier`.
8. Run the argument list with `cwd` set to that copy.
9. Delete the temporary directory on success, on a non-zero verifier exit, and when this function raises.

The process environment starts as `os.environ.copy()` plus one assignment:

```text
AGENTDOCS_PROJECT_DIR=<absolute workspace.project>
```

That path is the temporary project, not `config.starter`. The environment is not stored on `VerifierResult` and is not logged.

## Subprocess

```text
subprocess.run(
    argv,
    cwd=<temp>/verifier,
    capture_output=True,
    text=True,
    timeout=timeout_seconds,
    env=environment,
    shell=False,
    check=False,
)
```

`shell=True` is not used. The command is not passed to `bash -c`, `sh -c`, or `zsh -c`.

Supported commands are direct executables plus arguments, for example `python3 check.py`, `pytest -q`, and `python3 -m pytest -q`. The first argument must be an executable on `PATH`.

These shell forms are intentionally unsupported: `pytest && echo done`, pipes, `FOO=bar pytest`, and redirects.

## VerifierResult

| Field | Meaning |
| --- | --- |
| `command` | Parsed argv |
| `exit_code` | Process return code |
| `stdout` | Captured stdout |
| `stderr` | Captured stderr |
| `duration_seconds` | Time spent in `subprocess.run` |
| `passed` | `True` only when `exit_code == 0` |

A verifier exit of 1, including a normal pytest failure, returns this result. It does not raise. `passed` is then `False`.

| Situation | Result |
| --- | --- |
| Verifier exits 0 | `passed is True` |
| Verifier exits non-zero | `passed is False` |
| Executable cannot be started | `VerifierExecutableNotFoundError` |
| Process exceeds the timeout | `VerifierTimeoutError` |
| Command cannot be parsed | `VerifierCommandError` |
| Verifier source contains a symlink | `VerifierError` |

Timeout and a missing executable are infrastructure failures, not task failures.

## Pass and fail

```text
Codex exit_code 0 + verifier exit_code 1  →  FAIL
Codex exit_code 0 + verifier exit_code 0  →  PASS
```

The agent saying "Done" is not evidence. Correctness comes only from the verifier process.

## Separation and its limit

Verifier files stay outside the starter and are copied only after `run_verifier()` is called, which is after the agent process has returned. The agent prompt does not include the verifier path.

This is benchmark separation. It is not cryptographic or OS-level hiding. The current Codex sandbox mainly restricts writes, and a real run showed Codex can read filesystem paths outside `workspace.project` when given a path. Do not treat the verifier as secret from the agent. Stronger isolation is later Docker work, and it is not implemented here.

Running from a copy also keeps pytest caches, `__pycache__`, and files the verifier writes out of the owner's verifier source.

## Tests

`tests/test_verifier.py` runs local processes with `sys.executable`. It does not call Codex. Config tests cover relative and absolute `verify.path`, a missing verifier directory, and a verifier path that is a file. Workspace tests now supply a `VerifyConfig.path` because `create_workspace()` validates those directories.

## Deliberately not implemented

Step 4 does not add a multi-task runner, a CLI, reports, Git diffs, Claude, Docker, GitHub Actions, retries, or an LLM judge. The example in `examples/simple/` is one task: docs say to create `user.txt`, and `check.py` passes only when that file's stripped text is `Alice`.

# Step 5. Single-task runner

Step 5 runs one task. It does not loop over `config.tasks`, add a CLI, or save reports.

New files:

| Path | Role |
| --- | --- |
| `agentdocs/runner.py` | `TaskRunResult` and `run_task()` |
| `agentdocs/agents/factory.py` | `create_agent_adapter()` |
| `tests/test_runner.py` | Runner tests with a fake agent |

## TaskRunResult

| Field or property | Meaning |
| --- | --- |
| `task_id` | The task that ran |
| `agent_result` | What the coding-agent process did |
| `verifier_result` | Whether the project meets the task |
| `duration_seconds` | Time from just before workspace creation through verification, measured with `time.perf_counter` |
| `passed` | `verifier_result.passed` |
| `agent_completed_cleanly` | `agent_result.exit_code == 0` |

The workspace paths are not on this result. The workspace is already deleted when `run_task()` returns.

`AgentRunResult` answers what happened when the agent process ran. `VerifierResult` answers whether the project satisfies the benchmark. `TaskRunResult` keeps both.

```text
agent exit 0 + verifier fail  →  passed False, agent_completed_cleanly True
agent exit 1 + verifier pass  →  passed True,  agent_completed_cleanly False
```

## run_task()

```python
result = run_task(config, task)
```

Order:

1. Reject `verifier_timeout_seconds <= 0`.
2. Reject a task id that is not in `config.tasks`. Comparison is by id, not object identity.
3. Start the duration timer.
4. If `agent` is omitted, call `create_agent_adapter(config.agent.type)`.
5. `create_workspace(config)`.
6. `agent.run(task.prompt, workspace.project, workspace.docs)`.
7. After that call returns, `run_verifier(task.verify, workspace.project)`.
8. Build `TaskRunResult` and leave the workspace context, which deletes the temporary directory.
9. Return the result.

A returned `AgentRunResult` with a non-zero exit code does not skip the verifier and does not force `passed=False`. The agent may have written a correct project before exiting.

If `agent.run()` raises, including `AgentExecutableNotFoundError`, `AgentTimeoutError`, or `AgentOutputError`, the exception propagates and the verifier does not run. If `run_verifier()` raises `VerifierTimeoutError`, `VerifierCommandError`, `VerifierExecutableNotFoundError`, or `VerifierError`, that exception propagates too. Those are environment failures, not `passed=False`.

The `with create_workspace` block cleans the workspace on pass, fail, and any exception. `return` inside that block still runs workspace cleanup before the caller receives the result.

## Agent factory

`create_agent_adapter("codex")` returns `CodexAdapter()`. Any other type raises `ValueError`. There is no plugin registry. Passing `agent=` to `run_task()` skips the factory so tests can inject a fake adapter.

## Tests

`tests/test_runner.py` uses the real workspace and the real verifier. The agent is a `FakeAgent` or a `CodexAdapter` subclass whose `run` does not call Codex. `pytest` does not launch the Codex CLI.

## Deliberately not implemented

Step 5 does not run every task in the config, add `agentdocs test`, save stdout files, keep workspace copies, write JSON reports, or compute diffs. A later layer can loop over tasks and call `run_task()` once per task.

# Step 6. Suite runner

Step 6 runs every task in `config.tasks`. It does not add a CLI, reports, filters, or parallel execution.

New files:

| Path | Role |
| --- | --- |
| `agentdocs/suite.py` | `SuiteRunResult` and `run_suite()` |
| `tests/test_suite.py` | Suite tests with fake agents |

`run_suite()` does not call `create_workspace()`, `agent.run()`, or `run_verifier()`. Each task goes through the existing `run_task()`.

## SuiteRunResult

| Field or property | Meaning |
| --- | --- |
| `results` | One `TaskRunResult` per task, in config order |
| `duration_seconds` | Wall-clock time of the whole suite, from `time.perf_counter` |
| `total` | `len(results)` |
| `passed_count` | How many tasks have `passed` true |
| `failed_count` | `total - passed_count` |
| `passed` | True only when every task passed |

Suite duration is measured directly. It is not the sum of task durations.

## run_suite()

```python
result = run_suite(config)
result = run_suite(config, agent_factory=lambda: FakeAgent())
```

Order:

1. Reject `verifier_timeout_seconds <= 0` before any task starts.
2. Start the suite timer.
3. For each task in `config.tasks`, in that order:
   - If `agent_factory` is set, call it once and pass that new adapter to `run_task`.
   - Otherwise call `run_task` with `agent=None`, so `run_task` creates the configured adapter.
4. Keep going when `run_task` returns `passed=False`.
5. Return `SuiteRunResult`.

A normal verifier failure does not stop the suite. An exception from `run_task`, including a missing agent executable, an agent timeout, bad agent output, or a verifier infrastructure error, propagates immediately. Later tasks are not started. No fake `TaskRunResult` is inserted.

Each `run_task` call creates and deletes its own workspace, so a file written by task 1 is not visible to task 2 unless it was in the original starter.

`agent_factory` exists so each task can get a new adapter. One adapter instance is not reused across the suite. There is no plugin registry.

## Aggregate pass

Suite correctness is the aggregate of `TaskRunResult.passed`, which already follows the verifier.

```text
Task A: agent exit 0, verifier pass  →  PASS
Task B: agent exit 0, verifier fail  →  FAIL
Task C: agent exit 1, verifier pass  →  PASS

Suite: 3 total, 2 passed, 1 failed, passed False
```

## Tests

`tests/test_suite.py` uses real `run_task`, real workspaces, and real verifiers. Agents are fakes. One test patches `run_task` only to prove the default path does not build an adapter inside the suite and does not launch Codex.

## Deliberately not implemented

Step 6 does not add `agentdocs test`, task filters, retries, threads, reports, or saved artifacts. The suite returns data. Presentation belongs to a later CLI.

# Step 7. CLI

Step 7 is the terminal entry point. It loads a config, checks paths, calls `run_suite()`, and prints the result. It does not create workspaces, run Codex, run a single task, or run verifiers itself.

New files:

| Path | Role |
| --- | --- |
| `agentdocs/cli.py` | Typer app and Rich rendering |
| `tests/test_cli.py` | CLI tests with `CliRunner` |

`pyproject.toml` registers:

```toml
[project.scripts]
agentdocs = "agentdocs.cli:app"
```

After `pip install -e .`, the commands are `agentdocs --help` and `agentdocs test`.

## agentdocs test

```text
agentdocs test
agentdocs test --config examples/simple/agentdocs.yaml
agentdocs test -c examples/simple/agentdocs.yaml
```

The default config path is `agentdocs.yaml` in the current working directory. `--config` and `-c` select another file. Path resolution inside that file is unchanged: `docs`, `starter`, and verifier paths are still relative to the config file.

Flow:

1. `load_config(config_path)`
2. `validate_config_paths(config)`
3. Print the config path, agent type, and task count.
4. `run_suite(config)`
5. Render a Rich table and a short summary.
6. Exit.

The table columns are Task, Result, Agent Exit, Verifier Exit, and Duration. Result is `PASS` when `TaskRunResult.passed` is true. That follows the verifier, not the agent exit code. An agent exit of 1 with a verifier exit of 0 is shown as `PASS`.

Failed tasks can show a short verifier stderr, or stdout when stderr is empty, capped at 2000 characters. Normal runs do not print Codex JSONL, prompts, or the environment.

There is no live progress. `Running benchmark...` stays on screen until `run_suite()` returns.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | The suite finished and every task passed |
| 1 | The suite finished and at least one verifier failed |
| 2 | The benchmark could not run |

Exit 1 is a normal benchmark result. The CLI prints the table and summary. It does not call that a crash.

Exit 2 covers known configuration and infrastructure failures: missing or invalid config, missing docs, starter, or verifier directories, `AgentError` (missing Codex, timeout, bad JSONL), `VerifierError`, and `WorkspaceError`. The message is `Error: ...` on stderr, without a traceback. Unexpected programming errors are not caught.

## Tests

`tests/test_cli.py` uses Typer's `CliRunner`. Successful and failed runs patch `run_suite` and build real `SuiteRunResult` objects. Config errors use temporary files and the real loader. Pytest does not launch Codex.

## Deliberately not implemented

Step 7 does not add JSON output, saved reports, `--task` filters, `--agent`, timeout flags, Claude, live event streaming, or parallel execution.

# Step 8. Persistent run artifacts

Step 8 saves a completed suite after `run_suite()` returns. `CodexAdapter`, `run_verifier()`, `run_task()`, and `run_suite()` are unchanged. Temporary workspaces are still deleted. The artifact layer only reads the finished `SuiteRunResult`.

New files:

| Path | Role |
| --- | --- |
| `agentdocs/artifacts.py` | `RunArtifacts`, `ArtifactError`, and `write_run_artifacts()` |
| `tests/test_artifacts.py` | Artifact tests using constructed results |

`write_run_artifacts(config, result, output_root=...)` creates one run directory under `output_root`. It does not launch an agent, a verifier, or a workspace, and it does not mutate the suite result.

## Run id and location

A run id looks like `20260924T181900Z-a1b2c3d4`: UTC timestamp plus an 8-character UUID suffix. JSON `created_at_utc` is ISO 8601 UTC, such as `2026-09-24T18:19:00Z`.

The CLI default `output_root` is the config file's directory plus `.agentdocs/runs`. `agentdocs test --config examples/simple/agentdocs.yaml` writes `examples/simple/.agentdocs/runs/<run-id>/`. `--artifacts-dir` replaces that directory and is resolved from the current working directory. `--no-artifacts` skips writing. Using both flags is a usage error.

`.agentdocs/` is gitignored. Existing runs are never deleted or replaced.

## Files

```text
<run-id>/
├── result.json
├── summary.md
└── tasks/
    └── 001-create_user/
        ├── agent.stdout.log
        ├── agent.stderr.log
        ├── verifier.stdout.log
        └── verifier.stderr.log
```

`result.json` uses schema version 1. It stores counts, pass state, durations as numbers, exit codes, `completed_cleanly`, verifier command as a JSON array, `event_count`, and relative POSIX log paths. It does not store parsed JSONL events, workspace paths, environment variables, or a copy of `agentdocs.yaml`.

`summary.md` is plain Markdown. Task ids are escaped so a `|` does not break the table.

Task folder names are `001-<sanitized-id>`. Characters outside `A-Z`, `a-z`, `0-9`, `.`, `_`, and `-` become `_`. `.` and `..` are not used as the name. The folder is checked so it stays under `tasks/`.

Log files are written even when empty, and they contain only the captured string.

## Failure behavior

A suite that finishes with verifier failures is still saved. The CLI then exits 1.

If `run_suite()` raises before a `SuiteRunResult` exists, nothing is written and the CLI still exits 2.

`ArtifactError` covers a bad output root and filesystem write failures. The writer stages files in `.run-<id>.tmp` under `output_root` and renames that directory only after every file succeeds. A failed write removes the staging directory and does not leave a final run directory. The CLI prints the benchmark table first, then `Error: ...`, and exits 2.

## Privacy

Persisted fields are the task id, pass/fail, durations, exit codes, agent name, verifier command, stdout, stderr, and event count. Credentials and the process environment are not collected.

## Tests

`tests/test_artifacts.py` builds real result objects and writes them under `tmp_path`. CLI tests patch `run_suite()` and `write_run_artifacts()`. Pytest does not launch Codex.

## Deliberately not implemented

Step 8 does not add workspace snapshots, Git diffs, `agentdocs report`, JSON stdout mode, a database, Claude, Docker, task filters, retries, or parallel execution.

# Step 9. Multi-agent adapters

Step 9 adds Claude Code and Cursor Agent behind the existing `AgentAdapter` contract. `run_task()`, `run_suite()`, the verifier, the CLI, and artifacts do not grow provider-specific branches. Each adapter returns `AgentRunResult`.

Supported config values are `codex`, `claude`, and `cursor`. `create_agent_adapter()` returns `CodexAdapter`, `ClaudeAdapter`, or `CursorAdapter`. An unknown type still raises `ValueError`.

## Commands

Claude, when `claude` is on PATH:

```text
claude -p --output-format stream-json --permission-mode acceptEdits --add-dir <docs> <prompt>
```

The working directory is `workspace.project`. `--add-dir` exposes the sibling docs directory. `--dangerously-skip-permissions` is not used. `acceptEdits` allows normal file edits without a prompt. Shell-command approval still follows the user's Claude Code settings.

Cursor looks up `agent` first, then `cursor-agent`:

```text
agent -p --output-format stream-json --trust --add-dir <docs> <prompt>
```

`-p` is the documented non-interactive mode and includes write and shell access. Cursor Agent CLI `2026.09.26` refuses a fresh temporary directory until the process passes `--trust`, `--yolo`, or `-f`. AgentDocsBench passes only `--trust`, which accepts the current workspace. `--force` and `--yolo` are not used. `--add-dir` points at the docs copy beside the project. An unrelated program named `agent` would be selected first. AgentDocsBench does not fingerprint the binary.

Both prompts name the task, the absolute docs path, and the current project. They say not to edit the docs, not to leave the workspace, and not to commit or push. They do not include the verifier path.

## Output and timeouts

`agentdocs/agents/_jsonl.py` parses NDJSON for all three adapters. Blank lines are skipped. A nonblank line must be a JSON object. `CodexAdapter` still reports parse errors as `Codex returned invalid JSON...`, so existing Codex tests stay the same.

A missing executable raises `AgentExecutableNotFoundError`. A timeout, default 600 seconds, raises `AgentTimeoutError`. A non-zero process exit is still an `AgentRunResult`. The verifier decides whether the task passed. Quota and authentication failures are not classified in this step.

## Limits

AgentDocsBench identifies the harness, not the model. Model choice stays in each CLI. Credentials stay in that CLI's own login. `result.json` schema version stays 1; `agent_type` is already a string.

The temporary workspace is not an OS sandbox. Docker is not part of this step.

## Tests

`tests/test_claude_adapter.py` and `tests/test_cursor_adapter.py` mock `shutil.which` and `subprocess.run`. Pytest does not launch `claude`, `agent`, `cursor-agent`, or `codex`.

## Deliberately not implemented

Step 9 does not add model selection, provider quota classification, provider retries, separate benchmarks per agent, Docker, or a new artifact schema.

## Step 10. Live progress and CLI diagnostics

`agentdocs test` used to print `Running benchmark...` and then wait until `run_suite()` returned. A Cursor or Codex task can take minutes, so that silence looked like a hang. Step 10 reports the task lifecycle while the suite is running.

### Architecture

`agentdocs/progress.py` defines frozen event dataclasses and `emit_progress()`. `run_suite()` and `run_task()` call that helper when an optional `on_progress` callback is supplied. Neither module imports Rich or prints. `agentdocs/cli_progress.py` owns the terminal text. The CLI constructs one `CliProgressReporter` and passes it as `on_progress`.

A missing callback is a no-op. `run_task(config, task)` and `run_suite(config)` keep their previous behavior. If a callback raises, the exception propagates. The runner does not catch it.

### Callback API

```python
run_task(config, task, *, agent=None, verifier_timeout_seconds=60, on_progress=None)
run_suite(config, *, agent_factory=None, verifier_timeout_seconds=60, on_progress=None)
```

`run_suite()` passes the same callback into every `run_task()`.

### Event order

For a suite that finishes normally:

1. `SuiteStarted`
2. For each task: `TaskStarted`, `AgentStarted`, `AgentFinished`, `VerifierStarted`, `VerifierFinished`, `TaskFinished`
3. `SuiteFinished`

`run_suite()` owns suite events and `TaskStarted`. `run_task()` owns the agent and verifier events and `TaskFinished`. Each event is emitted once.

`AgentFinished` holds the `AgentRunResult`. `VerifierFinished` holds the `VerifierResult`. The events do not copy log text into extra fields.

### Failure behavior

A verifier exit other than 0 still produces `VerifierFinished` and `TaskFinished(passed=False)`. The suite continues.

If the agent raises, `run_task()` emits `AgentFailed` and re-raises the original exception. The verifier does not run. `TaskFinished` and `SuiteFinished` are not emitted.

If the verifier raises, `run_task()` emits `VerifierFailed` and re-raises the original exception. That exception does not become a benchmark FAIL.

An agent exit code of 1 is still `AgentFinished`. The task passes when the verifier exits 0.

### CLI modes

| Mode | What it shows |
| --- | --- |
| default | Task index, agent start, agent exit and duration, verifier start, PASS or FAIL |
| `--verbose` / `-v` | Default lines plus event count, stdout line count, and whether stderr is present |
| `--debug` | Verbose details plus delimited agent and verifier stdout and stderr |
| `--quiet` / `-q` | No live lines and no "Running benchmark..." line. The final table remains |

`--quiet` with `--verbose`, and `--quiet` with `--debug`, raise `typer.BadParameter` before the suite starts. `--verbose` and `--debug` can be used together.

When `console.is_terminal` is false, the reporter prints one line per event and does not start a Rich status spinner. A terminal also gets a status line while the agent or verifier is running. The final Rich table is unchanged.

### Privacy

Default and verbose output do not print the task prompt, `os.environ`, or credential files. `--debug` prints captured process output as-is. That text can contain file contents and provider diagnostics. It is opt-in. This step does not redact it, because a partial redaction would look safer than it is. Artifacts still store the same logs.

### Tests

`tests/test_progress.py` checks event order, a failed verifier, a non-zero agent exit that still passes, agent and verifier exceptions, a missing callback, and non-TTY reporter lines. `tests/test_cli.py` checks default, quiet, verbose, and debug output, plus the rejected flag combinations. Those tests use fake agents and a fake `run_suite`. They do not launch `codex`, `claude`, `agent`, or `cursor-agent`.

### Deliberately not implemented

Step 10 does not stream provider tokens, convert adapters to `subprocess.Popen`, persist progress events, or change `result.json`. Retries, Docker, and parallel tasks remain out of scope. Model selection is Step 11.

## Step 11. Model selection and discovery

The harness and the model are different. `agent.type` still selects Codex, Claude, or Cursor. `agent.model` optionally names the model that harness should use. Omitting it leaves the provider CLI default in place.

### Selection

`AgentConfig.model` is `str | None`. Whitespace is stripped. A blank string is rejected. Any other string is accepted. AgentDocsBench does not compare it with a catalog, because availability depends on the account, CLI version, and provider policy.

Precedence for one `agentdocs test` run:

1. `--model` / `-m`
2. `agent.model` in the YAML
3. the provider CLI default

`with_agent_model()` returns a copied config. The file on disk is not edited, and the loaded model object is not mutated in place.

`create_agent_adapter(agent_type, model=...)` passes that id into `CodexAdapter`, `ClaudeAdapter`, or `CursorAdapter`. `run_task()` uses `config.agent.model` when it builds the default adapter. No model means the previous argv is unchanged and `--model` is absent.

When a model is set, each command inserts `--model` and the id as its own argv element, with `shell=False`:

```text
codex exec --model <model> --json --ephemeral --skip-git-repo-check --sandbox workspace-write <prompt>
claude -p --model <model> --output-format stream-json --permission-mode acceptEdits --add-dir <docs> <prompt>
agent -p --model <model> --output-format stream-json --trust --add-dir <docs> <prompt>
```

The Cursor executable is still `agent` when it is on `PATH`, otherwise `cursor-agent`. `--force` and `--yolo` stay off. AgentDocsBench does not write `~/.codex/config.toml`, Claude settings, or Cursor settings, and it does not retry with another model when the provider rejects the id. A non-zero agent exit is still an `AgentRunResult`. The verifier still decides PASS or FAIL.

### Result fields

`AgentRunResult.requested_model` is the id AgentDocsBench asked for, or `None` when it asked for the CLI default. `resolved_model` would be the model the provider reported. These adapters do not infer that from the request, and they do not parse event payloads for it, so it stays `None`. A later provider that prints a stable model field can fill it without changing the request field.

Live progress includes the choice on the agent-started line: `cursor, provider default` or `cursor, <model>`. The CLI header prints `Model: provider default` or `Model: <model>`.

### Artifacts

`result.json` is schema version 2. The run records `agent_type` and `requested_model`. Each task agent records `requested_model` and `resolved_model`. `summary.md` prints the requested model, or `provider default`, and `Resolved model: not reported` unless every task in the run carries the same non-null resolved model. Run directory names stay timestamp plus a random suffix. Model ids are not placed in paths.

### Discovery

`agentdocs models` and `agentdocs models --agent <type>` call `agentdocs/models.py`. Each provider returns its own status: `available`, `unsupported`, `executable_not_found`, or `failed`. One provider failing does not hide the others. `ModelDiscoveryError` is separate from `AgentError`.

On this machine:

| CLI | Version | Listing |
| --- | --- | --- |
| Codex | 0.156.1 | `codex debug models --bundled` returns JSON `{"models": [{"slug", "display_name", "visibility", ...}]}`. AgentDocsBench keeps visible rows in catalog order and labels the source `codex-local-catalog`. `--bundled` skips the CLI refresh, so this is the binary's catalog, not the account list. |
| Claude Code | 2.1.283 | `--model` exists. No list subcommand exists. Discovery status is `unsupported`. The hint is to run `claude` and use `/model`. |
| Cursor | 2026.09.26 | `agent models` and `cursor-agent models` print `id - display` rows, including a default row. Discovery parses that text. Source is `cursor-account`. |

Interactive `/model` pickers are documented and not automated. AgentDocsBench does not scrape terminal UIs, read `~/.codex/models_cache.json`, call the OpenAI, Anthropic, or Cursor HTTP APIs, or ship a hardcoded model list. A model that discovery did not list can still be passed to `agentdocs test --model`.

### Tests

Config tests cover an omitted model, a trimmed model, a blank model, and the existing agent types. Adapter tests mock `subprocess.run` and check that `--model` is absent by default and is one argv element when set. Factory tests check that the model reaches each adapter. CLI tests check header text, the YAML override, and a mixed `agentdocs models` report. Discovery tests mock the provider process. Pytest does not launch `codex`, `claude`, `agent`, or `cursor-agent`.

### Deliberately not implemented

Step 11 does not add rankings, cost or token accounting, automatic fallback, provider SDKs, or direct HTTP discovery. Docker, parallel tasks, and Git diffs stay out of scope. Explicit multi-target runs are Step 12.

## Step 12. Benchmark matrix and comparison

Comparing agents used to mean running `agentdocs test` several times by hand. Step 12 runs one benchmark against an explicit list of agent and model targets, saves each run on its own, and prints the measurements. It does not rank targets or name a winner.

### Matrix config

`matrix.yaml` is separate from `agentdocs.yaml`. Schema version is 1.

```yaml
version: 1
targets:
  - id: cursor-default
    agent: cursor
  - id: cursor-named
    agent: cursor
    model: some-model-id
```

`load_matrix_config()` rejects a missing file, a directory, empty or non-mapping YAML, a blank id, a blank model, an unsupported agent, duplicate ids, an empty target list, and unknown keys. Model ids are not checked against `agentdocs models`. The same agent and model may appear twice when the target ids differ. `examples/stress/matrix.example.yaml` lists provider defaults and is not executed by the tests.

### Execution

`run_matrix()` copies the loaded benchmark with `with_agent()` and calls `run_suite()` once per target, in file order. The copy replaces `agent.type` and `agent.model`. Docs, starter, tasks, prompts, and verifiers stay on the copy. The original config object is not mutated. Workspaces stay per task inside `run_suite()`.

A suite that finishes with verifier failures is a completed target. The next target still runs. `AgentError`, `VerifierError`, `WorkspaceError`, `ConfigError`, `FileNotFoundError`, and `NotADirectoryError` are stored as that target's infrastructure error, and the matrix continues. Any other exception propagates. There is no retry and no parallelism.

Provider quota, credit, and authentication failures are still whatever the adapter already returns. If the provider process exits with a result, the suite completes and the verifier may fail every task. This step does not classify stderr.

### Progress and CLI

`agentdocs matrix --config <benchmark> --matrix <matrix>` prints the benchmark path, target count, and tasks per target. `MatrixCliReporter` prints `Target [i/n]`, the agent, and the model before that target's existing task progress. `--verbose`, `--debug`, and `--quiet` use the same task reporter as `agentdocs test`. `--model` is not a matrix option.

Exit codes: 0 when every target completed and every task passed; 1 when every target completed and at least one task failed; 2 when configuration is invalid, an artifact write fails, or any target has an infrastructure error. Infrastructure errors take priority over benchmark failures.

### Artifacts

Each completed target is passed to the existing `write_run_artifacts()`. Those runs stay schema version 2 under `<benchmark>/.agentdocs/runs/<run-id>/`. An errored target does not get a run id.

The matrix report is a different artifact, schema version 1, at `<benchmark>/.agentdocs/matrices/<matrix-run-id>/matrix.json` and `summary.md`. It stores target identity, agent, requested model, status, counts, task pass or fail, durations, child run ids, and the infrastructure error type and message. It does not copy stdout, stderr, prompts, or environment variables. The matrix id is a UTC timestamp plus a random suffix. Writes use a staging directory and do not replace an existing matrix directory. A later target error does not delete an earlier child run.

`summary.md` has a target table and a task comparison table using `PASS`, `FAIL`, `ERROR`, and `—`.

### Compare

`agentdocs compare` reads two or more `result.json` files, or directories that contain one. It does not launch a provider, run a verifier, or create a workspace. Schema 2 supplies `requested_model`. Null means provider default. Schema 1 is accepted and shown as `not recorded`, because the file does not say whether the provider was configured outside AgentDocsBench.

When ordered task ids match, the command says the artifacts have no benchmark fingerprint, so identical docs, starter, prompts, and verifiers cannot be proven. A matrix run is comparable because every target used the same loaded config. When task ids differ, the table uses the union, `—` marks a task absent from a run, and a warning is printed. Exit 0 means the table was printed, including when a saved task failed. Exit 2 means an input was missing or invalid.

### Tests

Matrix config, runner, artifact, compare parser, and CLI tests use fake suites or saved JSON. Pytest does not launch `codex`, `claude`, `agent`, or `cursor-agent`.

### Deliberately not implemented

Step 12 does not run every discovered model, rank results, compute a score, count tokens or cost, retry targets, run targets in parallel, fingerprint the benchmark, classify provider quota, or add Git diffs, Docker, or GitHub Actions.

## Step 13. Benchmark fingerprint and change manifest

`agentdocs compare` could see that two saved runs listed the same task ids and the same PASS/FAIL cells. It could not show that those runs used the same docs, starter, prompts, and verifiers. Step 13 gives every run a content hash of the benchmark definition, and compare uses that hash.

The fingerprint describes the benchmark input. It does not describe files the agent wrote. Git is not used. An agent-workspace diff can still be added later.

### What is hashed

`compute_benchmark_fingerprint()` in `agentdocs/fingerprint.py` reads:

- `config.docs`
- `config.starter`
- each task id, prompt, and verifier command, in config order
- each `task.verify.path` tree, still associated with that task id

It does not read agent type, model, run id, timestamps, durations, provider output, PASS/FAIL, credentials, environment variables, or matrix target id. Cursor versus Codex, and model A versus model B, keep the same fingerprint when the benchmark inputs are the same.

### Algorithm

Fingerprint schema version is 1. The algorithm name is `sha256`. This version is separate from `result.json` schema version 3 and `matrix.json` schema version 2.

File bytes are hashed with `hashlib.sha256` in 1 MiB chunks. The bytes are not decoded, and line endings are not normalized. A symlink is rejected with `BenchmarkFingerprintError` through the existing `find_symlink()` helper. Sockets, device files, and FIFOs raise the same error. Directories and regular files are supported. Hidden files are included.

Paths in the manifest are relative POSIX paths from the tree root. Absolute paths, modification times, ownership, and Unix permission bits are omitted. An executable-bit-only change therefore does not change fingerprint schema 1. Empty directories are listed, so adding an empty directory changes the tree digest. A rename is an added path plus a removed path. The manifest does not infer that those two paths are the same file.

Each tree digest is SHA-256 of canonical JSON for the sorted directory list and the sorted file list. Each file record is `path`, `sha256`, and `size_bytes`. Canonical JSON is UTF-8, `sort_keys=True`, `separators=(",", ":")`, and `ensure_ascii=False`. Task order is kept. File order is sorted by relative path.

Task prompts and verifier commands are hashed as the UTF-8 bytes of the strings already stored on the loaded config. The prompt text is not copied into `benchmark.json`. Two tasks may point at the same verifier directory. Both associations are stored. The component digest lists `task_id` plus that tree digest, in task order.

The component digests are `docs_sha256`, `starter_sha256`, `tasks_sha256`, and `verifiers_sha256`. The overall digest is SHA-256 of canonical JSON containing fingerprint schema version 1 and those four digests.

### Artifacts

`agentdocs test` and `agentdocs matrix` compute the fingerprint after path validation and before `run_suite()` or `run_matrix()`. The same object is passed into `write_run_artifacts()`. A caller that omits it gets a fingerprint computed at write time. The pre-run value is the one the CLI stores, including when a source file changes after selection and before the write.

`result.json` is schema version 3. It adds a `benchmark` object with the schema, algorithm, overall digest, four component digests, and `manifest_path` of `benchmark.json`. The file list stays in `benchmark.json` beside `result.json` and `summary.md`. `summary.md` prints `Benchmark fingerprint: \`sha256:...\`` and `Fingerprint schema: 1`.

`matrix.json` is schema version 2. It stores the same summary object, not one manifest per target. The matrix directory also contains one `benchmark.json`. Every completed child run from that invocation receives the same `overall_sha256`. `with_agent()` does not change it.

`benchmark.json` can contain relative filenames, hashes, sizes, and task ids. It does not contain file contents, prompts, environment variables, credentials, or absolute source paths.

The fingerprint is not a filesystem transaction. Normal execution edits a temporary workspace copy, not the source benchmark. Another process can still change the source tree while a run is in progress. This step does not snapshot the inputs.

### Compare and the fingerprint command

`agentdocs compare` still accepts schema 1, 2, and 3. Schema 3 requires the benchmark summary. A missing summary is an invalid artifact and exits 2.

Identity is one of:

- `VERIFIED MATCH` when every run is schema 3 and every `overall_sha256` is the same
- `DIFFERENT` when every run has a fingerprint and more than one distinct value appears
- `UNVERIFIED` when any run has no fingerprint

Absence is not treated as equal or different. For two schema 3 runs with different fingerprints, compare loads each `benchmark.json` and prints added, removed, and modified paths, directory changes, file-versus-directory changes, task additions, removals, reordering, prompt digest changes, and verifier command digest changes. It does not print prompt text. Three or more distinct fingerprints are grouped by hash. The path-level manifest is printed only for a two-run comparison. A missing or unreadable `benchmark.json` still leaves the identity line and the task table in place.

`agentdocs fingerprint --config <file>` loads the config, validates the directories, prints the component digests and the overall digest, and exits. `--json` prints the manifest document. It does not launch a provider, run a verifier, create a workspace, or write a run directory. Exit 0 means the fingerprint was computed. Exit 2 means the config, a path, or the fingerprint failed.

### Tests

`tests/test_fingerprint.py` covers identical trees, a copied absolute path, mtime and mode bits, agent type, model, byte edits, added and removed files, renames, hidden and binary files, sorted paths, empty directories, task prompt, id, order, and command changes, verifier association, a shared verifier directory, symlink rejection, CRLF versus LF, and the change manifest. CLI tests cover `agentdocs fingerprint` and compare states `VERIFIED MATCH`, `DIFFERENT`, and `UNVERIFIED`, including schema 1 and 2 fixtures left as old artifacts. Pytest does not launch `codex`, `claude`, `agent`, or `cursor-agent`.

### Deliberately not implemented

Step 13 does not add an agent-workspace diff, Git, docs-version experiments, automatic reruns, token or cost accounting, quota classification, retries, parallel matrix execution, content normalization, rename inference, ranking, Docker, or GitHub Actions. Immutable copies of the benchmark inputs can be a later step.

## Step 14. Agent workspace change manifest

Step 13 answers which benchmark definition went into a run. It hashes docs, starter, task prompts, verifier commands, and verifier source. It does not answer which project files the coding agent changed. The temporary workspace is deleted after each task, so that evidence has to be captured before cleanup.

The workspace manifest is the agent output record. The benchmark fingerprint remains the input record. They use the same SHA-256 helpers and they are stored separately.

### Timing

`run_task()` creates the workspace, snapshots `workspace.project`, runs the agent, snapshots the project again, compares those snapshots, then runs the verifier, builds `TaskRunResult`, and deletes the workspace. The manifest therefore describes the project as observed immediately after the agent process returned and before verification began. It is not an operating-system filesystem transaction. A background process that keeps writing after the parent CLI returns is outside this record.

A non-zero `AgentRunResult.exit_code` still captures the after snapshot, still computes the manifest, and still runs the verifier. Pass and fail follow the verifier. If `agent.run()` raises, including `AgentExecutableNotFoundError`, `AgentTimeoutError`, and `AgentOutputError`, the exception propagates, the verifier does not run, and no `TaskRunResult` is produced. Partial edits from a raised agent exception are not persisted.

If snapshotting a regular file fails, `WorkspaceChangeError` is raised. It subclasses `WorkspaceError`, so matrix execution records it as a target infrastructure error through the existing handler. Arbitrary programming errors are not caught, and matrix mode does not add `except Exception`.

### Snapshot

`agentdocs/workspace_changes.py` walks `workspace.project` with `os.walk(followlinks=False)` and `lstat`. Paths are relative POSIX paths. The snapshot digest is SHA-256 of canonical JSON for the sorted entries. It omits modification times, ownership, inodes, absolute roots, and file bytes.

Regular files store `type: file`, a raw-byte SHA-256, and `size_bytes`. Hashing is the shared `hash_file()` in `agentdocs/content_hash.py`, which reads 1 MiB chunks. Text, JSON, Python, and Markdown are not normalized. A one-byte change is a modification. An mtime-only change is not.

Directories are entries, including empty ones. Hidden files and directories are included. There is no ignore list for `__pycache__`, `.pytest_cache`, `node_modules`, or generated files. If the agent created them before returning, they are part of the record.

An agent-created symlink is `type: symlink` with `target_sha256` of the link target string. The raw target path is not stored. The target file is not read, and the link is not followed. A symlink directory is removed from the walk so its contents are not listed. FIFO, socket, character device, block device, and other special entries are classified by type and are not opened.

A path that disappears and another path that appears with the same bytes is a deletion plus an addition. Renames are not inferred. A path that changes type, such as file to directory, is a type change and is not also listed as a deleted file and an added directory.

The pre-agent digest is recorded. It is not required to equal the benchmark starter digest, because the source tree could change between fingerprinting and workspace creation.

### Results and artifacts

`TaskRunResult.workspace_changes` holds the manifest. The field defaults to an empty manifest so older test constructors still build. Production `run_task()` sets the compared manifest. Workspace paths are not stored on the result.

`result.json` is schema version 4. Each task has a `changes` object with `manifest_file`, `before_sha256`, `after_sha256`, and the file, directory, and type-change counts. The full manifest stays in `tasks/<task>/changes.json`, schema version 1, algorithm `sha256`. `benchmark.json` stays fingerprint schema 1. `matrix.json` stays schema version 2. Completed matrix child runs are schema 4 through the existing `write_run_artifacts()` path, and they share the parent benchmark fingerprint. One target's edits cannot appear in another target because each task still gets a fresh workspace.

`summary.md` adds a Changes column using `+N ~M -D` and a legend: `+ added, ~ modified, - deleted`. The CLI table uses the same counts. `--verbose` adds directory counts, type-change counts, and up to 20 changed paths, then says how many were omitted. `--quiet` still hides live lines. `--debug` prints captured process output and does not print file bodies from the manifest.

Progress emits `WorkspaceChangesCaptured` after `AgentFinished` and before `VerifierStarted`. The event carries counts and the capped path list, not the full manifest.

`agentdocs compare` accepts schema 1, 2, 3, and 4. Identity depends on whether a benchmark fingerprint is present. Schema 3 and schema 4 with the same fingerprint are `VERIFIED MATCH`. A schema 2 run compared with schema 4 is `UNVERIFIED`. Compare does not read `changes.json`. Older artifacts that have no change metadata are left as written. Absence is not reported as zero changes.

`changes.json` may contain relative paths, hashes, sizes, and entry types. It does not contain file contents, environment variables, credentials, absolute project paths, prompts, or shell history.

### Hashing reuse

`content_hash.py` holds the 1 MiB file hasher, byte hasher, and canonical JSON hasher. `fingerprint.py` calls those helpers and keeps its own tree walk, which still rejects symlinks and special files. Workspace snapshots use a separate walk that classifies those entries. Fingerprint schema 1 payloads are unchanged.

### Tests

`tests/test_workspace_changes.py` covers identical trees, adds, edits, deletes, path order, binary and hidden files, empty directories, type changes, rename-as-delete-plus-add, raw bytes, mtime, relative paths, digest stability, unfollowed symlinks, and FIFOs. Runner tests cover snapshot order, verifier-only files, nonzero agent exits that still pass, infrastructure exceptions, cleanup, and isolation between tasks. Artifact, compare, matrix, progress, and CLI tests cover schema 4, `changes.json`, and display. Pytest does not launch `codex`, `claude`, `agent`, or `cursor-agent`.

### Deliberately not implemented

Step 14 does not add unified diffs, patch files, before/after project copies, Git, rename inference, workspace retention, documentation write detection, token or cost accounting, retries, parallel execution, Docker, or GitHub Actions.

## Step 15. Provider failure classification

A provider CLI can exit non-zero because authentication, quota, credit, a rate limit, an invalid model, or provider availability stopped the run. Before this step that result still went through the verifier, so a quota outage could look like 0/15 benchmark FAIL. Verifier correctness and provider execution health are now separate.

`TaskRunResult.passed` is still the verifier result. A returned `AgentRunResult` with a non-zero exit still gets the after-agent workspace snapshot, the change manifest, and the verifier. Raised agent errors such as a missing executable or a timeout still propagate, skip the verifier, and produce no task result.

### Taxonomy

`agentdocs/agents/failures.py` defines failure schema version 1. The kinds are `authentication`, `quota_or_credit`, `rate_limit`, `invalid_model`, `provider_unavailable`, and `unknown_agent_failure`. The first five are blocking. `unknown_agent_failure` is not. Exit 0 is never a failure, including when stderr contains a warning.

Each classification stores `schema_version`, `kind`, `blocking`, `rule_id`, and `source`. It does not store the matched provider text. The existing stdout and stderr logs keep that text.

Rule priority, first match wins, is authentication, then invalid model, then quota or credit, then rate limit, then provider unavailable. Equal priority keeps the earlier rule in the table.

### Evidence

Adapters call `classify_agent_failure()` before returning `AgentRunResult`. The runner does not branch on provider name.

Structured events are preferred, and only explicit fields are read. The observed Codex usage-limit JSONL is `type: error` with a top-level `message`, and `type: turn.failed` with `error.message`. The rule `codex.usage_limit` matches the phrase `hit your usage limit` in those two strings only. Nested assistant text, item payloads, and raw stdout are not scanned. The captured message uses a curly apostrophe in `You've`; the rule matches the ASCII phrase that follows it.

The observed Claude failure is stderr containing `Credit balance is too low`. The rule `claude.credit_balance_low` is case-insensitive and Claude-specific. The same sentence on Codex or Cursor stderr stays unknown. The same sentence in Claude stdout stays unknown, because this repository does not contain a captured Claude stdout event for that failure.

Cursor has no captured provider-failure fixture, so Cursor has no production text rule. An unmatched non-zero Cursor exit is `unknown_agent_failure`.

Synthetic stderr sentinels, named `synthetic.*`, cover authentication, invalid model, rate limit, and provider unavailable so the taxonomy and precedence can be tested. Those phrases are not claimed as observed provider output. Bare `401` or `429`, and vague words such as `error` or `limit`, do not classify. Exit code alone never selects a known kind.

Classification is not part of the benchmark fingerprint.

### Suite, CLI, and artifacts

`SuiteRunResult.passed` remains the verifier aggregate. `execution_complete` is false when any task has `failure.blocking`. `blocking_agent_failure_count` and `unknown_agent_failure_count` are separate.

`agentdocs test` exits 2 when any blocking failure was returned, after printing results and writing artifacts. It exits 1 when execution is complete and a verifier failed. It exits 0 when execution is complete and every verifier passed. An unknown non-zero exit follows the verifier.

The live line appends a short label such as `quota/credit` or `unknown agent failure`. `--verbose` prints kind, blocking, rule id, and source. `--debug` still prints the captured process output and does not add a second copy of the matched text. `--quiet` still hides live lines. The final summary reports execution health.

`result.json` is schema version 5. It adds an `execution` object and `agent.failure`, which is null after a clean exit. `benchmark.json` stays schema 1. `changes.json` stays schema 1.

### Matrix and compare

`matrix.json` is schema version 3. A finished suite with a blocking provider failure has status `provider_error`, keeps `run_id`, and stores execution counts plus per-kind `provider_failures`. The child run artifact is written. Later targets still run. Infrastructure exceptions stay status `error` and still have no child suite. Human status labels are `PASS`, `FAIL`, `PROVIDER_ERROR`, and `ERROR`. Matrix exit 2 covers infrastructure errors and provider errors. Exit 1 is an execution-complete verifier failure.

`agentdocs compare` accepts schemas 1 through 5. Fingerprint identity is unchanged: schema 3, 4, and 5 runs with the same fingerprint are `VERIFIED MATCH`. Schema 1 and 2 stay `UNVERIFIED` against a fingerprinted run. Schema 5 compare output includes execution health. Older schemas display `not recorded`. Compare does not read stderr logs and does not reclassify old artifacts.

### Tests

`tests/test_agent_failure_classification.py` covers exit 0, unknown exits, the observed Claude and Codex fixtures, synthetic blocking kinds, vague text, nested payloads, precedence, privacy, and provider isolation. Adapter, runner, suite, CLI, artifact, matrix, and compare tests use fakes. Pytest does not launch `codex`, `claude`, `agent`, or `cursor-agent`.

### Deliberately not implemented

Step 15 does not retry, switch models or providers, call billing or usage APIs, read credentials, count tokens or cost, or add Docker, GitHub Actions, parallel matrix execution, or an LLM classifier.

## Step 16. Optional Docker execution runtime

The temporary workspace is a directory boundary. The coding-agent CLI and the verifier still ran as host processes, so an agent could read paths outside that directory when the provider CLI's own sandbox allowed it, and a verifier that imports agent-written code ran with the host's access. Step 16 can place both processes in containers. Local execution stays the default, and a normal `agentdocs test` or `agentdocs matrix` does not look up `docker`, contact the daemon, inspect an image, or create a container.

### Threat model and limits

Docker is a stronger isolation boundary than a host process. It is not a perfect security boundary. The Docker daemon is trusted. Kernel and container-runtime vulnerabilities are out of scope. Anything mounted into the agent container is visible to the agent. Anything passed through the environment is visible to the agent. Agent network access means that mounted data could be sent off the machine. Docker Desktop filesystem behavior differs from native Linux. Background processes started by a provider CLI still have to be cleaned up with the container. Running a container does not prove that the generated code is harmless.

### Runtime config schema version 1

Docker settings live in a separate file, loaded with `--runtime-config`. They are not fields in `agentdocs.yaml`. `agentdocs/runtime_config.py` accepts schema version 1, backend `docker` only, and rejects unknown keys. Each used agent type may have `image`, `network` (`bridge` or `none`, default `bridge`), `env_passthrough` (names only), and `mounts`. `verifier.image` is required. A Cursor-only run does not need `codex` or `claude` entries. There is no `env: {KEY: value}` map, no `host` network, and no arbitrary Docker network name.

Relative mount sources resolve against the runtime file's directory. `~` is expanded. The source must exist and be a regular file or directory, not a socket, device, or FIFO. The resolved source is kept in memory for the mount and is not written to artifacts. The container target must be an absolute POSIX path. `/`, `/workspace`, `/workspace/project`, `/workspace/docs`, `/workspace/verifier`, anything under `/workspace/`, and `/var/run/docker.sock` are rejected. A source of `/` is rejected. A source whose name is `docker.sock` is rejected. Duplicate targets are rejected. A listed environment name that is unset raises `RuntimeConfigError` before the provider process starts.

### Runtime abstraction

`agentdocs/execution/` holds `LocalExecutionRuntime` and `DockerExecutionRuntime`. Adapters, the verifier, the runner, the suite, and the matrix ask the runtime for visible paths and for process execution. They do not grow a Docker branch of their own. The local runtime keeps host `shutil.which`, inherited environment, host working directories, and the existing argv. The Docker runtime does not require `codex`, `claude`, `agent`, or `cursor-agent` on the host PATH. Cursor still prefers `agent` and falls back to `cursor-agent`, by starting each name inside the image with `--version` and no workspace mounts. The first name that exists is cached on the runtime object for that invocation.

Visible paths on the host are the temporary project and docs directories. Visible paths in Docker are `/workspace/project` and `/workspace/docs`. Codex prompts and Cursor or Claude `--add-dir` use those visible paths.

### Containers

The agent container bind-mounts the host project read-write at `/workspace/project` and the host docs read-only at `/workspace/docs`. It does not mount the verifier, the Docker socket, the host home, or provider credentials. Extra mounts and `env_passthrough` names come only from that agent's runtime entry. `HOME` is `/tmp/agentdocs-home`, and `/tmp` is a tmpfs mounted `rw,nosuid,nodev`. The container root filesystem stays writable so provider CLIs can run. It is not a read-only rootfs. Flags are `--cap-drop ALL` and `--security-opt no-new-privileges`. `--privileged` is not used. On POSIX, `--user <uid>:<gid>` is passed. Platforms without `os.getuid` omit that flag. Docker Desktop may still present different ownership than native Linux.

The verifier runs in a second container after the host has snapshotted the project. It mounts the project read-write and the temporary verifier copy read-write at `/workspace/verifier`, with working directory `/workspace/verifier` and `AGENTDOCS_PROJECT_DIR=/workspace/project`. It does not mount docs, agent mounts, agent environment, the host home, or the Docker socket. Its network is always `none` and is not configurable in this step. Verifier writes such as `__pycache__` can land in the project after the Step 14 snapshot, so they stay out of `changes.json`.

Network for the agent is `--network bridge` or `--network none`. The selected mode is stored as provenance.

### Lifecycle, timeouts, and errors

Containers use a random `agentdocs-<role>-<id>` name. The sequence is `docker create`, `docker start -a`, `docker inspect`, and `docker rm -f` in a `finally` block. A timeout kills the container, removes it, and raises the existing `AgentTimeoutError` or `VerifierTimeoutError`. Cleanup also runs after exit 0, a non-zero provider exit, a verifier failure, a start failure, an inspect failure, and an exception raised while the container exists.

`DockerExecutableNotFoundError`, `DockerDaemonUnavailableError`, `DockerImageNotFoundError`, and `DockerContainerError` subclass `ExecutionRuntimeError`. They are infrastructure failures. They are not passed through Step 15 classification and are not `provider_unavailable`. A missing image tells the user that AgentDocsBench does not pull or build images. Image ids are inspected once per runtime object and cached in memory for that invocation only.

If `docker inspect` reports `OOMKilled` or a non-empty `State.Error`, that is a container infrastructure failure. `executable file not found` is `AgentExecutableNotFoundError` or `VerifierExecutableNotFoundError`, not `unknown_agent_failure`. A provider CLI that actually starts and returns an ordinary exit code is an `AgentRunResult`. Step 15 then classifies it. A quota error inside the container still gets a workspace manifest, a verifier run, a schema 6 artifact, and CLI exit 2 when the failure is blocking.

`docker` is invoked as argv with `shell=False`. Provider and verifier commands stay argv. The Docker CLI is located with `shutil.which("docker")`. The Docker Python SDK is not used.

### Provenance and artifacts

Runtime info schema version 1 is `backend: local`, or `backend: docker` with agent and verifier `requested_image`, `image_id`, and `network`. Repo digests are not required and are not stored. Mount sources, environment values, credential paths, and the runtime config path are not stored. A private registry name in the image string can appear in the local artifact.

`result.json` is schema version 6 and adds top-level `runtime`. The benchmark summary, execution health, task results, `agent.failure`, and `changes` keep their Step 15 meanings. `benchmark.json` stays schema 1. `changes.json` stays schema 1. Failure classification stays schema 1. `matrix.json` is schema version 4. Each completed target stores that runtime object. An infrastructure error target stores the invocation backend without image ids. One invocation is all local or all Docker. A missing agent entry or a missing image is `ERROR` for that target, not `PROVIDER_ERROR`, and later targets continue. A classified provider failure inside Docker remains `PROVIDER_ERROR`.

`agentdocs compare` accepts schemas 1 through 6. Fingerprint identity is unchanged: schema 3, 4, 5, and 6 runs with the same fingerprint are `VERIFIED MATCH`. Schema 2 compared with schema 6 is `UNVERIFIED`. Runtime differences do not change that status. Compare prints the recorded runtime and, when two recorded runtimes differ, the sentence `Execution runtime differs between artifacts.` Schemas 1 through 5 display `Runtime: not recorded`. Old logs are not reclassified. Execution health is still shown for schemas 5 and 6 only.

The header prints `Runtime: local` or `Runtime: docker` plus the requested image names. `--verbose` can add image ids and network modes. It does not print container ids, host mount paths, or environment values. `--quiet` does not add those lines. `--debug` still prints captured stdout and stderr, not a full `docker inspect` document.

### Smoke fixture and tests

`examples/docker-smoke/` is a manual fixture. Its image provides a fake `agent` and `python3`. The fake agent writes `DOCKER_OK`, emits one JSON object, and records an error marker if the verifier path is visible or the docs mount is writable. The verifier checks `DOCKER_OK`, that marker, and that `/workspace/docs` is absent. Pytest mocks `docker` argv and does not require a daemon, a network, or a provider CLI.

### Deliberately not implemented

Step 16 does not add Kubernetes, Firecracker, gVisor, Kata Containers, remote workers, SSH execution, official provider images, automatic CLI installation, automatic `docker pull` or `docker build`, registry login, Docker Compose, arbitrary Docker CLI flags, privileged containers, Docker socket mounts, host networking, configurable verifier networking, automatic credential discovery, a secrets manager, retries, provider or model fallback, token or cost accounting, parallel matrix execution, GitHub Actions, a docs-version regression workflow, workspace retention, or unified diffs.

## Step 17. GitHub Actions and CI reporting

GitHub Actions is another way to launch the benchmark that already exists. `agentdocs test --github-actions` and `agentdocs matrix --github-actions` call the same `run_suite()`, `run_matrix()`, artifact writers, provider classification, Docker runtime, verifiers, and fingerprint code as a local run. The flag only adds CI presentation. `GITHUB_ACTIONS=true` does not enable it. A command without the flag does not read `GITHUB_ACTIONS`, `GITHUB_STEP_SUMMARY`, or `GITHUB_OUTPUT`.

### Motivation

A workflow needs a human-readable summary and stable values for later steps, including the artifact directory `actions/upload-artifact` should collect. Those are not benchmark results. Persisted schemas stay where Step 16 left them: `result.json` 6, `matrix.json` 4, `benchmark.json` 1, `changes.json` 1, runtime info 1, and failure classification 1. No GitHub field was added to `TaskRunResult`, `SuiteRunResult`, `AgentRunResult`, `MatrixRunResult`, or `VerifierResult`.

### Environment and errors

`agentdocs/github_actions.py` validates the job, appends the summary, appends step outputs, and derives status from the result objects. Runners, the suite, the matrix, artifact writers, adapters, and `agentdocs/execution/` do not import it.

When the flag is set, `GITHUB_ACTIONS` must be the string `true`, and `GITHUB_STEP_SUMMARY` and `GITHUB_OUTPUT` must be non-empty. `GitHubActionsError` covers a missing variable and a failed append. It is CLI infrastructure. It is not `provider_error`, `AgentFailureClassification`, `VerifierError`, or a Docker error. The command exits 2. It does not create substitute files in an invented location. There is no GitHub REST or GraphQL call, no `gh`, no check run, and no `::error::`, `::warning::`, or `::notice::` workflow command.

### Status

For one suite, `pass` means execution completed and every verifier passed (exit 0). `fail` means execution completed and a verifier failed (exit 1). `provider_error` means a blocking provider failure is present (exit 2). `error` means no completed suite because configuration, runtime, verifier setup, artifacts, or CI reporting failed (exit 2). `fail` and `provider_error` stay distinct.

For a matrix, the same four words use the existing target status. Precedence is `error`, then `provider_error`, then `fail`, then `pass`. The process exit code is still `matrix_exit_code()`: 2 when any target is incomplete or a blocking provider failure is present, 1 when every target completed and a verifier failed, otherwise 0. Classification is not recomputed from logs. The summary does not rank targets, name a winner, or add a score.

### Summary and outputs

The job summary is Markdown appended to `GITHUB_STEP_SUMMARY`. A completed suite shows status, agent, model, runtime, fingerprint, task counts, verifier result, agent exit, provider failure kind, and `+added ~modified -deleted` counts. A provider failure says that provider execution was incomplete and still shows the verifier result. An infrastructure failure before a suite exists says the benchmark could not complete and points at the step log. The exception text is not copied into the summary.

A matrix summary lists each target in file order with agent, model, status, passed/total tasks, and duration. Provider-failure counts are totaled underneath. Child run ids are listed. Absolute artifact paths are not.

`GITHUB_OUTPUT` uses GitHub's multiline environment-file form, `key<<delimiter`, for every value. The delimiter is a random token regenerated when it collides with a line in the value. Keys are fixed constants. Empty values are written as an empty body, not the string `null`. Spaces, `%`, newlines, and non-ASCII characters are preserved in the file. Deprecated `::set-output` is not used.

Suite keys are `status`, `exit_code`, `run_id`, `artifact_path`, `benchmark_sha256`, `suite_passed`, `execution_complete`, and `runtime_backend`. Matrix keys add `matrix_run_id`, `target_count`, `pass_targets`, `fail_targets`, `provider_error_targets`, and `error_targets`. `suite_passed` and `execution_complete` are `true` or `false` when a suite exists. When no suite exists they are empty, including on an infrastructure error, where `status=error` and `exit_code=2` are still written if the GitHub files can be appended. `artifact_path` may be absolute because the upload action needs a filesystem path. It is omitted from the summary and from `result.json`. `--no-artifacts` leaves `run_id` and `artifact_path` empty and still writes the summary.

User-controlled ids are escaped with the shared `markdown_table_cell()` helper, which artifact summaries also use. The helper escapes backslash and pipe, turns newlines and control characters into spaces, turns backticks into apostrophes, and writes angle brackets as HTML entities. Prompts, stdout, stderr, environment values, and verifier output are not interpolated.

### Order and reporting failure

The command runs the benchmark, writes the normal artifacts, writes the GitHub summary and outputs, then exits with the existing code. A blocking provider result follows that same order and exits 2. If artifact writing fails, the GitHub status is `error` and the exit code is 2, including when every verifier passed. A configuration or runtime error attempts the minimal error summary, prints the existing `Error:` line, and exits 2. GitHub reporting does not delete artifacts that were already written.

If the benchmark finished and appending `GITHUB_STEP_SUMMARY` or `GITHUB_OUTPUT` fails, the process exits 2. The summary is appended first. A failure there leaves the output file unwritten. A failure on the output file can leave a summary that already describes the benchmark result. The process exit code is the integration result in that case.

A rejected `--quiet` combination is still Typer's usage error. It is reported before the GitHub files are validated, so that usage error does not write a job summary.

### Privacy

The summary may show task ids, target ids, pass or fail, provider failure category, change counts, the fingerprint, agent and model names, runtime backend, requested image names, network mode, and run ids. It does not show prompts, raw model output, stdout, stderr, environment values, credentials, host mount sources, changed file contents, verifier output, or a GitHub token. A private image name can appear because Step 16 already records the requested image. Registry credentials are not recorded.

### Repository CI

`.github/workflows/ci.yml` runs on `push` and `pull_request` with `contents: read`. It requests no write, package, or token permission and no repository secret. The unit-test job uses an Ubuntu runner, Python 3.12, `actions/checkout@v7`, `actions/setup-python@v7`, an editable install, and `python -m pytest -q`. It does not start Docker or a provider CLI.

The Docker smoke job builds `agentdocs-docker-smoke:local` from `examples/docker-smoke` and runs `agentdocs test` with that fixture's config, runtime file, and `--github-actions`. The image contains a fake `agent`. The job does not call Codex, Claude, or Cursor and does not use provider credentials. A later step, marked `if: always()`, fails if any container name starts with `agentdocs-`, the prefix Step 16 uses. Another `if: always()` step uploads `result.json`, `summary.md`, `benchmark.json`, and `tasks/**/changes.json` with `actions/upload-artifact@v7` and `retention-days: 7`. Agent and verifier stdout and stderr logs are not in that upload. Action versions are current major tags. This repository has no commit-SHA pin policy, and no SHAs were invented.

`examples/github-actions/benchmark.yml` is a template for someone else's repository. It is not registered under `.github/workflows/` here, so it does not run. The user copies it to `.github/workflows/agentdocs.yml`, supplies the image and runtime file, and passes secrets with a workflow `env:` block. `runtime.yaml` lists `env_passthrough` names only. Fork pull requests do not receive normal repository secrets. The template and the README tell the user not to use `pull_request_target` to run benchmark code with secrets. Running a provider CLI on the runner, instead of in an image the user built, remains the user's authentication and security decision. AgentDocsBench does not log in to a provider.

### Tests

`tests/test_github_actions.py` covers environment validation, append failures, preserved existing file content, safe output values, suite and matrix summaries, status precedence, Markdown escaping, unchanged schema constants, and the absence of a GitHub import in the core modules. CLI tests cover the flag, the unchanged no-flag path, exit codes, `--no-artifacts`, artifact-write failure, report-write failure, and quiet, verbose, and debug. Pytest writes temporary files for the two GitHub paths. It does not call the GitHub API, start a Docker daemon, or launch `codex`, `claude`, `agent`, or `cursor-agent`.

### Deliberately not implemented

Step 17 does not add a docs-version regression gate, a baseline comparison, a pass-rate threshold, ranking, scoring, token or cost accounting, retries, provider or model fallback, parallel tasks, parallel matrix execution, a run-history database, a dashboard, pull-request comments, issue creation, badges, a GitHub App, official provider images, registry publishing, or package publishing. The process exit codes 0, 1, and 2 remain the CI signal for `agentdocs test` and `agentdocs matrix`.

## Step 18. Documentation-version experiments

The question this step answers is: when only the documentation input changes, which task outcomes were observed to stay the same, become passing, become failing, or become non-comparable?

One suite per variant does not establish that the docs caused the result. Coding agents can be nondeterministic. The report says what was observed while AgentDocsBench kept the benchmark's non-documentation inputs constant. It uses "observed transition", "newly passing relative to reference", and "newly failing relative to reference". It does not say the docs caused a result, that a candidate is better or worse, that a difference is statistically significant, or that a variant won.

### Experiment config schema 1

Variants are not fields of `agentdocs.yaml`. `agentdocs/experiment_config.py` loads a separate file, usually `docs-experiment.yaml`. Schema version is 1. Unknown keys are rejected. At least two variants are required. Variant ids are trimmed, must be non-blank, and must be unique. `reference` is trimmed and must match exactly one variant id. Each `docs` path is required and must already be a directory. Two variants may point at the same directory, which is a control. `DocsVariantConfig` holds `id` and `docs`. `DocsExperimentConfig` holds `version`, `reference`, and `variants`.

Relative docs paths resolve against the directory that contains the experiment file. They do not resolve against the shell directory or the benchmark file unless those directories are the same. Absolute docs paths are supported. The original `agentdocs.yaml` docs path is not the experiment's docs path. Loading the benchmark does not require that unused path to exist. Each variant is validated after its docs path is substituted. Starter and verifier paths still have to be valid when the suite runs.

`with_docs(config, docs_path)` returns a new `AgentDocsConfig` via `model_copy`. The caller's config object is unchanged. Only `docs` differs.

### Docs-only invariant

Before any variant suite, the runner fingerprints the reference docs tree. Each variant, in file order, is copied with `with_docs`, fingerprinted, and checked against that reference fingerprint. `docs_sha256` and `overall_sha256` may differ. `starter_sha256`, `tasks_sha256`, and `verifiers_sha256` must match. A mismatch raises `ExperimentInvariantError` before `run_suite` for that variant. The reference does not have to be first, and it is not executed early just to obtain the fingerprint.

Fingerprinting is not a filesystem transaction. Another process can change starter, docs, or verifiers after the digest is computed. The invariant records what AgentDocsBench observed at fingerprint time. Step 18 does not snapshot those trees immutably. Fingerprint schema 1 is unchanged.

### Runner

`run_docs_experiment()` in `agentdocs/experiment.py` calls `run_suite()` once per variant. It does not reimplement workspaces, agent execution, provider classification, snapshots, verifiers, or suite aggregation. The same runtime object, agent factory, and verifier timeout are used for every variant. There is no docs-by-agent, docs-by-model, or docs-by-runtime cross product, and variants are not run in parallel.

Known infrastructure exceptions (`AgentError`, `VerifierError`, `WorkspaceError`, `ConfigError`, `ExecutionRuntimeError`, `BenchmarkFingerprintError`, `FileNotFoundError`, `NotADirectoryError`) are stored on that variant and the loop continues. `ExperimentInvariantError` and any other exception propagate. There is no `except Exception`.

Progress events are `ExperimentStarted`, `VariantStarted`, `VariantFinished`, `VariantError`, and `ExperimentFinished`. Suite and task events still fire inside each variant. The runner does not import Rich.

### Status and transitions

A variant is `pass` when its suite completed and every verifier passed, `fail` when the suite completed and a verifier failed, `provider_error` when the suite exists and a blocking provider failure occurred, and `error` when no completed suite exists. The parent status is `error`, then `provider_error`, then `complete`. Variant pass and fail stay visible separately.

A task is comparable when both sides have a task result and neither task has `agent_result.failure.blocking == true`. Otherwise the transition is `not_comparable`. The five transitions are `unchanged_pass`, `unchanged_fail`, `newly_passing`, `newly_failing`, and `not_comparable`. A blocking provider failure is never `newly_failing`, even if that verifier exited non-zero. A non-blocking `unknown_agent_failure` with a verifier pass is still a pass, matching Step 15. If the reference variant has no suite, or a task on the reference is blocking, comparisons that need that result are `not_comparable`, and later variants still run. Every known task id is listed. The reference is not compared with itself. Extra candidates are compared only with the reference.

Docs deltas reuse `compare_benchmark_fingerprints().docs`: added, removed, and modified files, added and removed directories, and file/directory type changes. There is no rename inference, file content, or textual patch. The delta and the transitions are shown together as observations.

### Artifacts

Completed variants are normal runs under `<benchmark>/.agentdocs/runs/<run-id>/` with result schema 6, benchmark schema 1, changes schema 1, and the existing logs. The parent is `<benchmark>/.agentdocs/experiments/<experiment-run-id>/` with `experiment.json` schema 1 and `summary.md`. Child logs are not copied. The run id is a UTC timestamp plus a random suffix. `experiment.json` stores variant ids, positions, statuses, docs and overall fingerprints, controlled starter/tasks/verifiers hashes, child run ids, transition counts, task transitions, the docs change manifest, and error type plus message for an infrastructure variant. It does not store docs contents, prompts, raw stdout or stderr, environment values, credentials, absolute docs paths, the runtime-config path, or a GitHub token. The parent write uses a staging directory and `os.rename`. A failed parent write raises `ExperimentArtifactError` (an `ArtifactError`) and leaves earlier child runs in place.

### CLI and exit codes

`agentdocs experiment --config agentdocs.yaml --experiment docs-experiment.yaml` accepts `-c`, `-e`, `--model` / `-m`, `--runtime-config`, `--github-actions`, `--no-artifacts`, `--verbose`, `--debug`, and `--quiet`. Model precedence is the CLI flag, then `agent.model`, then the provider default, and that choice is shared. Quiet hides live progress and still prints the final report. Verbose may show fingerprints, capped path lists, and child run ids. Debug keeps the existing child-run behavior and does not dump docs contents, the experiment file, or the environment.

This command is observational. Exit 0 means every variant produced a completed suite and there was no blocking provider failure. Verifier failures and newly failing tasks do not change that. Exit 2 means at least one variant had an infrastructure error or a blocking provider failure, including a parent artifact write failure. There is no exit 1 and no `--fail-on-newly-failing`.

### Comparability warnings

The report separates benchmark control from the execution environment. Starter, tasks, and verifiers matching is something the hashes can show. If completed variants report different non-null resolved models, the report says "Resolved model differed between documentation variants" and still shows the transitions. Null resolved models are reported as not reported. When `requested_model` is null, the report says provider default: AgentDocsBench kept the request constant and cannot prove the provider selected the same model unless the provider reports one. Provider-default experiments are allowed. If completed Docker runs report different image ids, the report says so. Local execution does not fingerprint the host provider binary.

`agentdocs compare` is unchanged. Comparing two variant `result.json` files reports a different benchmark identity because the docs hash differs. That is expected. The experiment report is the place that records the controlled non-doc hashes.

### GitHub Actions

`--github-actions` reuses Step 17. Status is `complete`, `provider_error`, or `error`. Newly failing tasks stay `complete`. Outputs are `status`, `exit_code`, `experiment_run_id`, `artifact_path`, `reference_variant`, `variant_count`, `runtime_backend`, `reference_benchmark_sha256`, `newly_passing`, `newly_failing`, `unchanged_pass`, `unchanged_fail`, and `not_comparable`. Transition counts are the sum across every non-reference comparison, so the same task can be counted once per candidate. There is no single overall benchmark fingerprint. `--no-artifacts` leaves `experiment_run_id` and `artifact_path` empty. The job summary reuses `markdown_table_cell()` and the shared experiment markdown. Test and matrix GitHub behavior is unchanged. `result.json` stays schema 6, `matrix.json` stays schema 4, `benchmark.json` stays schema 1, `changes.json` stays schema 1, provider failure schema stays 1, runtime info schema stays 1, and runtime config schema stays 1.

### Fake Docker smoke and tests

`examples/docs-experiment-smoke/` builds `agentdocs-docs-experiment-smoke:local`. The fake `agent` reads `/workspace/docs/instruction.txt`. Reference docs say `WRITE OLD_RESULT`. Candidate docs say `WRITE EXPECTED_RESULT`. The verifier expects `EXPECTED_RESULT`, so the observed transition is `newly_passing` for `docs_task` and the experiment exits 0. Docs stay read-only. The verifier tree is not mounted for the agent. Pytest does not start that image.

`.github/workflows/ci.yml` adds a `docs-experiment-smoke` job: checkout, Python 3.12, install, build the fake image, run `agentdocs experiment` with `--github-actions`, fail if an `agentdocs-` container remains, and upload `experiment.json`, experiment `summary.md`, and child `result.json`, `summary.md`, `benchmark.json`, and `changes.json`. Agent and verifier logs are not uploaded. No provider credentials are used.

Pytest covers the experiment schema, `with_docs`, fingerprint control, the invariant error before any suite, variant order, all five transitions, blocking and unknown failures, infrastructure continuation, multiple candidates, content-free docs deltas, parent and child artifacts, CLI flags and exit codes, GitHub outputs, mocked Docker mounts, and resolved-model and image-id warnings. The suite does not launch `codex`, `claude`, `agent`, or `cursor-agent`, and it does not require a Docker daemon.

### Deliberately not implemented

Step 18 does not add repeated trials, statistical significance, confidence intervals, pass-rate thresholds, weighted scores, documentation ranking, winner selection, automatic regression gating, `--fail-on-newly-failing`, a baseline registry or promotion, Git branch, tag, commit, or pull-request docs checkout, a docs-by-agent or docs-by-model or docs-by-runtime matrix, parallel variants, parallel tasks, automatic retries, provider or model fallback, token or cost accounting, LLM judging, semantic docs diffs, unified text patches, a dashboard, a database, or remote workers.
