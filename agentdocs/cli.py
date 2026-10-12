"""Terminal entry point for AgentDocsBench."""

import json
import re
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console
from rich.table import Table

from agentdocs.agents.base import AgentError
from agentdocs.execution import DockerExecutionRuntime, LocalExecutionRuntime
from agentdocs.execution.base import ExecutionRuntime
from agentdocs.execution.errors import ExecutionRuntimeError
from agentdocs.runtime_config import load_runtime_config
from agentdocs.artifacts import ArtifactError, write_run_artifacts
from agentdocs.cli_experiment import ExperimentCliReporter, render_experiment_report
from agentdocs.cli_hints import format_error_hint
from agentdocs.cli_matrix import (
    MatrixCliReporter,
    matrix_exit_code,
    render_compare,
    render_matrix_report,
)
from agentdocs.cli_progress import CliProgressReporter
from agentdocs.compare import CompareError, load_comparable_run
from agentdocs.fingerprint import (
    BenchmarkFingerprintError,
    compute_benchmark_fingerprint,
    fingerprint_manifest_document,
)
from agentdocs.github_actions import (
    GitHubActionsEnvironment,
    GitHubActionsError,
    load_github_actions_environment,
    publish_experiment_infrastructure_error,
    publish_experiment_report,
    publish_matrix_infrastructure_error,
    publish_matrix_report,
    publish_suite_infrastructure_error,
    publish_suite_report,
)
from agentdocs.config import (
    SUPPORTED_AGENT_TYPES,
    ConfigError,
    load_config,
    validate_config_paths,
    AgentDocsConfig,
    with_agent,
    with_agent_model,
)
from agentdocs.experiment import (
    DocsVariantRunResult,
    ExperimentInvariantError,
    experiment_exit_code,
    run_docs_experiment,
)
from agentdocs.experiment_artifacts import write_experiment_artifacts
from agentdocs.experiment_config import load_experiment_config
from agentdocs.matrix import run_matrix
from agentdocs.matrix_artifacts import write_matrix_artifacts
from agentdocs.matrix_config import load_matrix_config
from agentdocs.models import (
    ModelDiscoveryResult,
    ModelInfo,
    discover_all_models,
    discover_models,
)
from agentdocs.doctor import diagnose, doctor_exit_code
from agentdocs.scaffold import InitProjectError, initialize_project
from agentdocs.suite import SuiteRunResult, run_suite
from agentdocs.workspace_changes import file_change_label
from agentdocs.verifier import VerifierError
from agentdocs.workspace import WorkspaceError

app = typer.Typer(
    name="agentdocs",
    help="Run documentation-driven coding-agent benchmarks.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()
error_console = Console(stderr=True, soft_wrap=True)
plain_console = Console(soft_wrap=True)
_FAILURE_DETAIL_LIMIT = 2000
_AGENT_TITLES = {"codex": "Codex", "claude": "Claude", "cursor": "Cursor"}
_FAMILY_PREFIXES = (
    ("cursor-grok", "Grok"),
    ("grok", "Grok"),
    ("gpt", "GPT"),
    ("claude", "Claude"),
    ("composer", "Composer"),
    ("gemini", "Gemini"),
    ("muse", "Muse"),
    ("kimi", "Kimi"),
    ("glm", "GLM"),
)
_INVISIBLE_CHARS = re.compile(r"[\u200b\u200c\u200d\ufeff\u2060]")
_DEFAULT_NOTE = re.compile(r"\s*\((?:current,\s*)?default\)", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


@app.callback()
def main() -> None:
    """Run documentation-driven coding-agent benchmarks."""


@app.command()
def test(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="Configuration file.",
        ),
    ] = Path("agentdocs.yaml"),
    artifacts_dir: Annotated[
        Path | None,
        typer.Option(
            "--artifacts-dir",
            help="Directory that receives run directories.",
        ),
    ] = None,
    no_artifacts: Annotated[
        bool,
        typer.Option(
            "--no-artifacts",
            help="Run the benchmark without writing artifacts.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Show additional agent execution details.",
        ),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug",
            help="Show captured agent and verifier stdout/stderr.",
        ),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option(
            "--quiet",
            "-q",
            help="Suppress live progress output.",
        ),
    ] = False,
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            "-m",
            help="Model id for this run. Overrides agent.model in the config file.",
        ),
    ] = None,
    runtime_config: Annotated[
        Path | None,
        typer.Option(
            "--runtime-config",
            help="Optional Docker runtime file. Omit it to run on the host.",
        ),
    ] = None,
    github_actions: Annotated[
        bool,
        typer.Option(
            "--github-actions",
            help="Append a GitHub Actions job summary and step outputs. Requires a GitHub Actions job.",
        ),
    ] = False,
) -> None:
    """Run a benchmark."""
    if no_artifacts and artifacts_dir is not None:
        raise typer.BadParameter(
            "--no-artifacts cannot be combined with --artifacts-dir."
        )
    _reject_quiet_conflicts(quiet, verbose, debug)
    github = _prepare_github(github_actions)
    benchmark_sha256 = ""
    runtime_backend = ""
    try:
        loaded = load_config(config)
        validate_config_paths(loaded)
    except (ConfigError, FileNotFoundError, NotADirectoryError, WorkspaceError) as exc:
        _fail_with_github(
            github,
            exc,
            report="suite",
            benchmark_sha256=benchmark_sha256,
            runtime_backend=runtime_backend,
        )
    if model is not None:
        try:
            loaded = with_agent_model(loaded, model)
        except ValueError as exc:
            _fail_with_github(
                github,
                exc,
                report="suite",
                benchmark_sha256=benchmark_sha256,
                runtime_backend=runtime_backend,
            )
    try:
        fingerprint = compute_benchmark_fingerprint(loaded)
    except BenchmarkFingerprintError as exc:
        _fail_with_github(
            github,
            exc,
            report="suite",
            benchmark_sha256=benchmark_sha256,
            runtime_backend=runtime_backend,
        )
    benchmark_sha256 = fingerprint.overall_sha256

    try:
        runtime = _load_runtime(runtime_config)
    except (ExecutionRuntimeError, FileNotFoundError) as exc:
        _fail_with_github(
            github,
            exc,
            report="suite",
            benchmark_sha256=benchmark_sha256,
            runtime_backend=runtime_backend,
        )
    runtime_backend = runtime.backend

    console.print("AgentDocsBench")
    _plain(f"Config: {config.resolve()}")
    console.print(f"Agent: {loaded.agent.type}")
    console.print(f"Model: {_model_label(loaded.agent.model)}")
    console.print(f"Tasks: {len(loaded.tasks)}")
    if not quiet:
        try:
            status_lines = runtime.status_lines(loaded.agent.type, verbose=verbose)
        except ExecutionRuntimeError as exc:
            _fail_with_github(
                github,
                exc,
                report="suite",
                benchmark_sha256=benchmark_sha256,
                runtime_backend=runtime_backend,
            )
        for line in status_lines:
            console.print(line)
    console.print()
    if not quiet:
        console.print("Running benchmark...")
        console.print()

    reporter = CliProgressReporter(
        console,
        verbose=verbose,
        debug=debug,
        quiet=quiet,
    )
    try:
        result = run_suite(loaded, on_progress=reporter, runtime=runtime)
    except (
        ConfigError,
        FileNotFoundError,
        NotADirectoryError,
        WorkspaceError,
        AgentError,
        VerifierError,
        ExecutionRuntimeError,
    ) as exc:
        _fail_with_github(
            github,
            exc,
            report="suite",
            benchmark_sha256=benchmark_sha256,
            runtime_backend=runtime_backend,
        )

    _render_results(result)
    _render_failure_details(result)
    _render_summary(result)
    _render_execution(result)
    run_id = ""
    artifact_path = ""
    if not no_artifacts:
        try:
            artifacts = write_run_artifacts(
                loaded,
                result,
                output_root=_artifact_output_root(config, artifacts_dir),
                benchmark_fingerprint=fingerprint,
            )
        except ArtifactError as exc:
            _fail_with_github(
                github,
                exc,
                report="suite",
                benchmark_sha256=benchmark_sha256,
                runtime_backend=runtime_backend,
            )
        run_id = artifacts.run_id
        artifact_path = str(artifacts.root)
        console.print()
        _plain(f"Artifacts: {artifacts.root}")
    exit_code = _suite_exit_code(result)
    if github is not None:
        try:
            publish_suite_report(
                github,
                result,
                agent_type=loaded.agent.type,
                model=loaded.agent.model,
                benchmark_sha256=benchmark_sha256,
                exit_code=exit_code,
                run_id=run_id,
                artifact_path=artifact_path,
            )
        except GitHubActionsError as exc:
            _exit_with_error(exc)
    raise typer.Exit(code=exit_code)


@app.command()
def models(
    agent: Annotated[
        str | None,
        typer.Option(
            "--agent",
            help="Limit the listing to one agent: codex, claude, or cursor.",
        ),
    ] = None,
) -> None:
    """Show provider model discovery information."""
    if agent is None:
        results = discover_all_models()
    else:
        selected = agent.strip()
        if selected not in SUPPORTED_AGENT_TYPES:
            supported = ", ".join(sorted(SUPPORTED_AGENT_TYPES))
            raise typer.BadParameter(
                f"Unsupported agent type {selected!r}. Supported agent types: {supported}."
            )
        results = (discover_models(selected),)
    console.print("AgentDocsBench Models")
    console.print()
    for index, result in enumerate(results):
        if index:
            console.print()
        _render_model_discovery(result)


@app.command()
def fingerprint(
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Configuration file."),
    ] = Path("agentdocs.yaml"),
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Print the fingerprint manifest as JSON."),
    ] = False,
) -> None:
    """Compute the benchmark content fingerprint."""
    try:
        loaded = load_config(config)
        validate_config_paths(loaded)
        identity = compute_benchmark_fingerprint(loaded)
    except (
        ConfigError,
        FileNotFoundError,
        NotADirectoryError,
        BenchmarkFingerprintError,
    ) as exc:
        _exit_with_error(exc)
    if as_json:
        document = json.dumps(
            fingerprint_manifest_document(identity),
            indent=2,
            ensure_ascii=False,
        )
        typer.echo(document)
        return
    _plain("AgentDocsBench Benchmark Fingerprint")
    _plain("")
    _plain(f"Config: {config.resolve()}")
    _plain(f"Algorithm: {identity.algorithm}")
    _plain(f"Schema: {identity.schema_version}")
    _plain("")
    rows = (
        ("Docs:", identity.docs_sha256),
        ("Starter:", identity.starter_sha256),
        ("Tasks:", identity.tasks_sha256),
        ("Verifiers:", identity.verifiers_sha256),
    )
    width = max(len(label) for label, _digest in rows)
    for label, digest in rows:
        _plain(f"{label.ljust(width)} {digest}")
    _plain("")
    _plain("Overall:")
    _plain(identity.overall_sha256)


@app.command()
def matrix(
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Benchmark configuration file."),
    ] = Path("agentdocs.yaml"),
    matrix_path: Annotated[
        Path,
        typer.Option("--matrix", "-M", help="Matrix file of explicit agent and model targets."),
    ] = Path("matrix.yaml"),
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show additional agent execution details."),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Show captured agent and verifier stdout/stderr."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress live progress output."),
    ] = False,
    runtime_config: Annotated[
        Path | None,
        typer.Option(
            "--runtime-config",
            help="Optional Docker runtime file. Omit it to run every target on the host.",
        ),
    ] = None,
    github_actions: Annotated[
        bool,
        typer.Option(
            "--github-actions",
            help="Append a GitHub Actions job summary and step outputs. Requires a GitHub Actions job.",
        ),
    ] = False,
) -> None:
    """Run a benchmark against multiple agent/model targets."""
    _reject_quiet_conflicts(quiet, verbose, debug)
    github = _prepare_github(github_actions)
    benchmark_sha256 = ""
    runtime_backend = ""
    try:
        loaded = load_config(config)
        validate_config_paths(loaded)
        loaded_matrix = load_matrix_config(matrix_path)
        fingerprint = compute_benchmark_fingerprint(loaded)
    except (
        ConfigError,
        FileNotFoundError,
        NotADirectoryError,
        WorkspaceError,
        BenchmarkFingerprintError,
    ) as exc:
        _fail_with_github(
            github,
            exc,
            report="matrix",
            benchmark_sha256=benchmark_sha256,
            runtime_backend=runtime_backend,
        )
    benchmark_sha256 = fingerprint.overall_sha256

    try:
        runtime = _load_runtime(runtime_config)
    except (ExecutionRuntimeError, FileNotFoundError) as exc:
        _fail_with_github(
            github,
            exc,
            report="matrix",
            benchmark_sha256=benchmark_sha256,
            runtime_backend=runtime_backend,
        )
    runtime_backend = runtime.backend

    console.print("AgentDocsBench Matrix")
    _plain(f"Benchmark: {config.resolve()}")
    console.print(f"Targets: {len(loaded_matrix.targets)}")
    console.print(f"Tasks per target: {len(loaded.tasks)}")
    if not quiet:
        agent_types = tuple(target.agent for target in loaded_matrix.targets)
        for line in _matrix_runtime_lines(runtime, agent_types, verbose=verbose):
            console.print(line)
    console.print()
    reporter = CliProgressReporter(console, verbose=verbose, debug=debug, quiet=quiet)
    matrix_reporter = MatrixCliReporter(console, quiet=quiet)
    try:
        result = run_matrix(
            loaded,
            loaded_matrix,
            on_progress=reporter,
            on_matrix_progress=matrix_reporter,
            runtime=runtime,
        )
    except (
        ConfigError,
        FileNotFoundError,
        NotADirectoryError,
        WorkspaceError,
        AgentError,
        VerifierError,
        ExecutionRuntimeError,
    ) as exc:
        _fail_with_github(
            github,
            exc,
            report="matrix",
            benchmark_sha256=benchmark_sha256,
            runtime_backend=runtime_backend,
        )

    task_ids = tuple(task.id for task in loaded.tasks)
    render_matrix_report(console, result, task_ids)
    runs_root = config.resolve().parent / ".agentdocs" / "runs"
    matrices_root = config.resolve().parent / ".agentdocs" / "matrices"
    run_ids: dict[str, str] = {}
    try:
        for target in result.targets:
            if target.suite_result is None:
                continue
            target_config = with_agent(
                loaded,
                agent_type=target.agent_type,
                model=target.requested_model,
            )
            artifacts = write_run_artifacts(
                target_config,
                target.suite_result,
                output_root=runs_root,
                benchmark_fingerprint=fingerprint,
            )
            run_ids[target.target_id] = artifacts.run_id
        matrix_artifacts = write_matrix_artifacts(
            result,
            task_ids=task_ids,
            run_ids=run_ids,
            output_root=matrices_root,
            benchmark_fingerprint=fingerprint,
        )
    except ArtifactError as exc:
        _fail_with_github(
            github,
            exc,
            report="matrix",
            benchmark_sha256=benchmark_sha256,
            runtime_backend=runtime_backend,
        )
    console.print()
    for target in result.targets:
        run_id = run_ids.get(target.target_id)
        if run_id is None:
            continue
        _plain(f"Run {target.target_id}: {runs_root / run_id}")
    _plain(f"Matrix: {matrix_artifacts.root}")
    exit_code = matrix_exit_code(result)
    if github is not None:
        try:
            publish_matrix_report(
                github,
                result,
                benchmark_sha256=benchmark_sha256,
                exit_code=exit_code,
                matrix_run_id=matrix_artifacts.matrix_run_id,
                artifact_path=str(matrix_artifacts.root),
                run_ids=run_ids,
            )
        except GitHubActionsError as exc:
            _exit_with_error(exc)
    raise typer.Exit(code=exit_code)


@app.command()
def experiment(
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Benchmark configuration file."),
    ] = Path("agentdocs.yaml"),
    experiment_path: Annotated[
        Path,
        typer.Option(
            "--experiment",
            "-e",
            help="Documentation experiment file. Variants replace only the docs path.",
        ),
    ] = Path("docs-experiment.yaml"),
    no_artifacts: Annotated[
        bool,
        typer.Option("--no-artifacts", help="Run the experiment without writing artifacts."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show fingerprints, docs changes, and run ids."),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Show captured agent and verifier stdout/stderr."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress live progress output."),
    ] = False,
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            "-m",
            help="Model id for every variant. Overrides agent.model in the benchmark file.",
        ),
    ] = None,
    runtime_config: Annotated[
        Path | None,
        typer.Option(
            "--runtime-config",
            help="Optional Docker runtime file. The same runtime is used for every variant.",
        ),
    ] = None,
    github_actions: Annotated[
        bool,
        typer.Option(
            "--github-actions",
            help="Append a GitHub Actions job summary and step outputs. Requires a GitHub Actions job.",
        ),
    ] = False,
) -> None:
    """Run the same benchmark against multiple documentation variants."""
    _reject_quiet_conflicts(quiet, verbose, debug)
    github = _prepare_github(github_actions)
    reference_variant = ""
    variant_count = ""
    runtime_backend = ""
    try:
        loaded = load_config(config)
        loaded_experiment = load_experiment_config(experiment_path)
    except (
        ConfigError,
        FileNotFoundError,
        NotADirectoryError,
        BenchmarkFingerprintError,
    ) as exc:
        _fail_experiment(
            github,
            exc,
            reference_variant=reference_variant,
            variant_count=variant_count,
            runtime_backend=runtime_backend,
        )
    reference_variant = loaded_experiment.reference
    variant_count = str(len(loaded_experiment.variants))
    if model is not None:
        try:
            loaded = with_agent_model(loaded, model)
        except ValueError as exc:
            _fail_experiment(
                github,
                exc,
                reference_variant=reference_variant,
                variant_count=variant_count,
                runtime_backend=runtime_backend,
            )
    try:
        runtime = _load_runtime(runtime_config)
    except (ExecutionRuntimeError, FileNotFoundError) as exc:
        _fail_experiment(
            github,
            exc,
            reference_variant=reference_variant,
            variant_count=variant_count,
            runtime_backend=runtime_backend,
        )
    runtime_backend = runtime.backend
    console.print("AgentDocsBench Documentation Experiment")
    _plain(f"Benchmark: {config.resolve()}")
    _plain(f"Experiment: {experiment_path.resolve()}")
    console.print(f"Reference: {loaded_experiment.reference}")
    console.print(f"Variants: {len(loaded_experiment.variants)}")
    console.print(f"Agent: {loaded.agent.type}")
    console.print(f"Model: {_model_label(loaded.agent.model)}")
    if not quiet:
        try:
            status_lines = runtime.status_lines(loaded.agent.type, verbose=verbose)
        except ExecutionRuntimeError as exc:
            _fail_experiment(
                github,
                exc,
                reference_variant=reference_variant,
                variant_count=variant_count,
                runtime_backend=runtime_backend,
            )
        for line in status_lines:
            console.print(line)
    console.print()
    reporter = CliProgressReporter(console, verbose=verbose, debug=debug, quiet=quiet)
    experiment_reporter = ExperimentCliReporter(console, quiet=quiet)
    runs_root = config.resolve().parent / ".agentdocs" / "runs"
    experiments_root = config.resolve().parent / ".agentdocs" / "experiments"
    run_ids: dict[str, str] = {}

    def _write_child(variant_result: DocsVariantRunResult, variant_config: AgentDocsConfig) -> None:
        if variant_result.suite_result is None or variant_result.fingerprint is None:
            return
        artifacts = write_run_artifacts(
            variant_config,
            variant_result.suite_result,
            output_root=runs_root,
            benchmark_fingerprint=variant_result.fingerprint,
        )
        run_ids[variant_result.variant_id] = artifacts.run_id

    try:
        result = run_docs_experiment(
            loaded,
            loaded_experiment,
            on_progress=reporter,
            on_experiment_progress=experiment_reporter,
            runtime=runtime,
            on_variant_complete=None if no_artifacts else _write_child,
        )
    except (
        ConfigError,
        FileNotFoundError,
        NotADirectoryError,
        WorkspaceError,
        AgentError,
        VerifierError,
        ExecutionRuntimeError,
        BenchmarkFingerprintError,
        ExperimentInvariantError,
        ArtifactError,
    ) as exc:
        _fail_experiment(
            github,
            exc,
            reference_variant=reference_variant,
            variant_count=variant_count,
            runtime_backend=runtime_backend,
        )
    console.print()
    render_experiment_report(console, result, run_ids, verbose=verbose)
    experiment_artifacts = None
    if not no_artifacts:
        try:
            experiment_artifacts = write_experiment_artifacts(
                result,
                run_ids=run_ids,
                output_root=experiments_root,
            )
        except ArtifactError as exc:
            _fail_experiment(
                github,
                exc,
                reference_variant=reference_variant,
                variant_count=variant_count,
                runtime_backend=runtime_backend,
            )
        console.print()
        for variant in result.variants:
            run_id = run_ids.get(variant.variant_id)
            if run_id is None:
                continue
            _plain(f"Run {variant.variant_id}: {runs_root / run_id}")
        _plain(f"Experiment: {experiment_artifacts.root}")
    exit_code = experiment_exit_code(result)
    if github is not None:
        try:
            publish_experiment_report(
                github,
                result,
                exit_code=exit_code,
                experiment_run_id="" if experiment_artifacts is None else experiment_artifacts.experiment_run_id,
                artifact_path="" if experiment_artifacts is None else str(experiment_artifacts.root),
                run_ids=run_ids,
            )
        except GitHubActionsError as exc:
            _exit_with_error(exc)
    raise typer.Exit(code=exit_code)


@app.command()
def compare(
    runs: Annotated[
        list[Path],
        typer.Argument(help="Run directories or result.json files. At least two."),
    ],
) -> None:
    """Compare saved benchmark runs."""
    if len(runs) < 2:
        raise typer.BadParameter("Provide at least two run artifacts to compare.")
    loaded = []
    for path in runs:
        try:
            loaded.append(load_comparable_run(path))
        except CompareError as exc:
            _exit_with_error(exc)
    render_compare(console, tuple(loaded))
    raise typer.Exit(code=0)


@app.command()
def init(
    directory: Annotated[
        Path,
        typer.Argument(
            help="New or empty directory. Existing files are not overwritten.",
        ),
    ],
    agent: Annotated[
        str,
        typer.Option(
            "--agent",
            help="Coding agent for the benchmark: codex, claude, or cursor.",
        ),
    ],
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            help="Optional model id written to agent.model. Omit it to keep the provider default.",
        ),
    ] = None,
) -> None:
    """Create a starter AgentDocsBench benchmark.

    Writes a new benchmark into DIRECTORY. Refuses a directory that already
    contains files, and does not overwrite anything.
    """
    try:
        created = initialize_project(directory, agent_type=agent, model=model)
    except InitProjectError as exc:
        _exit_with_error(exc)
    resolved = created.resolve()
    _plain("Created AgentDocsBench benchmark:")
    _plain("")
    _plain(f"  {resolved}")
    _plain("")
    _plain("Next steps:")
    _plain("")
    _plain(f"  cd {resolved}")
    _plain("  agentdocs doctor --config agentdocs.yaml")
    _plain("  agentdocs test --config agentdocs.yaml")
    _plain("")
    _plain(f"The benchmark currently uses {agent.strip()}.")
    _plain(
        "Make sure that provider CLI is installed and authenticated "
        "before running a real benchmark."
    )


@app.command()
def doctor(
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Benchmark configuration file."),
    ] = Path("agentdocs.yaml"),
    runtime_config: Annotated[
        Path | None,
        typer.Option(
            "--runtime-config",
            help="Optional Docker runtime file. Omit it to check local execution.",
        ),
    ] = None,
    experiment_path: Annotated[
        Path | None,
        typer.Option(
            "--experiment",
            "-e",
            help="Optional documentation experiment file to validate.",
        ),
    ] = None,
) -> None:
    """Check whether a benchmark environment is ready.

    Checks configuration, paths, provider or Docker availability, and an
    optional experiment file. Does not run a benchmark or a provider CLI.
    """
    report = diagnose(
        config,
        runtime_config=runtime_config,
        experiment_path=experiment_path,
    )
    _plain("AgentDocsBench Doctor")
    _plain("")
    width = max(len(item.name) for item in report.checks)
    for item in report.checks:
        detail = f"  {item.detail}" if item.detail else ""
        _plain(f"{item.name.ljust(width)}  {item.status:<4}{detail}")
    _plain("")
    raise typer.Exit(code=doctor_exit_code(report))


def _load_runtime(runtime_config: Path | None) -> ExecutionRuntime:
    if runtime_config is None:
        return LocalExecutionRuntime()
    return DockerExecutionRuntime(load_runtime_config(runtime_config))


def _matrix_runtime_lines(
    runtime: ExecutionRuntime,
    agent_types: tuple[str, ...],
    *,
    verbose: bool,
) -> list[str]:
    if not isinstance(runtime, DockerExecutionRuntime):
        lines = ["Runtime: local"]
        if verbose:
            lines.append("Runtime backend: local")
        return lines
    lines = [
        "Runtime: docker",
        f"Verifier image: {runtime.config.verifier.image}",
    ]
    seen: list[str] = []
    for agent_type in agent_types:
        if agent_type in seen:
            continue
        seen.append(agent_type)
        try:
            spec = runtime.config.agents.for_agent(agent_type)
        except ExecutionRuntimeError:
            lines.append(f"Agent image ({agent_type}): not configured")
            continue
        lines.append(f"Agent image ({agent_type}): {spec.image}")
        if verbose:
            lines.append(f"Agent network ({agent_type}): {spec.network}")
    if verbose:
        lines.append("Verifier network: none")
    return lines


def _reject_quiet_conflicts(quiet: bool, verbose: bool, debug: bool) -> None:
    if quiet and verbose:
        raise typer.BadParameter("--quiet cannot be combined with --verbose.")
    if quiet and debug:
        raise typer.BadParameter("--quiet cannot be combined with --debug.")


def _model_label(model: str | None) -> str:
    if model is None:
        return "provider default"
    return model


def _render_model_discovery(result: ModelDiscoveryResult) -> None:
    _plain(f"{_AGENT_TITLES[result.agent]}")
    if result.status == "executable_not_found":
        _plain("  CLI: not installed")
        if result.message:
            _plain(f"  {result.message}")
        _plain(
            "  Hint: Install the provider CLI and make sure it is on PATH. "
            "Run `agentdocs doctor` to check the environment."
        )
        return
    _plain("  CLI: installed")
    if result.status == "available":
        _plain("  Discovery: available")
        if result.message:
            _plain(f"  {result.message}")
        _render_model_rows(result.models)
        return
    if result.status == "unsupported":
        _plain("  Discovery: unsupported by installed CLI")
    else:
        _plain("  Discovery: failed")
    if result.message:
        _plain(f"  {result.message}")
    if result.status == "failed":
        _plain(
            "  Hint: Check the provider CLI installation and authentication, "
            "then retry `agentdocs models`."
        )


def _render_model_rows(models: tuple[ModelInfo, ...]) -> None:
    if not models:
        _plain("  No models were returned.")
        return
    defaults = [item.id for item in models if item.is_default]
    count = f"{len(models)} model" if len(models) == 1 else f"{len(models)} models"
    if defaults:
        count = f"{count}. Default: {', '.join(defaults)}"
    _plain(f"  {count}")
    grouped: dict[str, list[ModelInfo]] = {}
    for item in models:
        grouped.setdefault(_model_family(item.id), []).append(item)
    show_families = len(grouped) > 1
    width = max(len(item.id) for item in models)
    for family, items in grouped.items():
        _plain("")
        if show_families:
            _plain(f"  {family}")
        for item in items:
            name = _clean_display_name(item.display_name)
            mark = "  default" if item.is_default else ""
            gap = "  " if name else ""
            _plain(f"    {item.id.ljust(width)}{gap}{name}{mark}".rstrip())


def _model_family(model_id: str) -> str:
    """Group a listed id by its product prefix. Unknown ids stay visible."""
    if model_id == "auto":
        return "Auto"
    lowered = model_id.lower()
    for prefix, label in _FAMILY_PREFIXES:
        if lowered == prefix or lowered.startswith(prefix + "-"):
            return label
    return "Other"


def _clean_display_name(name: str | None) -> str:
    if not name:
        return ""
    cleaned = _INVISIBLE_CHARS.sub("", name)
    cleaned = _DEFAULT_NOTE.sub("", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    return cleaned


def _plain(text: str) -> None:
    plain_console.print(text, markup=False, highlight=False)


def _artifact_output_root(config_path: Path, artifacts_dir: Path | None) -> Path:
    if artifacts_dir is not None:
        return artifacts_dir.resolve()
    return config_path.resolve().parent / ".agentdocs" / "runs"


def _fail_experiment(
    github: GitHubActionsEnvironment | None,
    exc: BaseException,
    *,
    reference_variant: str,
    variant_count: str,
    runtime_backend: str,
) -> NoReturn:
    if github is not None:
        try:
            publish_experiment_infrastructure_error(
                github,
                reference_variant=reference_variant,
                variant_count=variant_count,
                runtime_backend=runtime_backend,
            )
        except GitHubActionsError as report_exc:
            _print_error(exc)
            _print_error(report_exc)
            raise typer.Exit(code=2) from report_exc
    _exit_with_error(exc)


def _prepare_github(enabled: bool) -> GitHubActionsEnvironment | None:
    if not enabled:
        return None
    try:
        return load_github_actions_environment()
    except GitHubActionsError as exc:
        _exit_with_error(exc)


def _fail_with_github(
    github: GitHubActionsEnvironment | None,
    exc: BaseException,
    *,
    report: str,
    benchmark_sha256: str,
    runtime_backend: str,
) -> NoReturn:
    if github is not None:
        try:
            if report == "matrix":
                publish_matrix_infrastructure_error(
                    github,
                    benchmark_sha256=benchmark_sha256,
                    runtime_backend=runtime_backend,
                )
            else:
                publish_suite_infrastructure_error(
                    github,
                    benchmark_sha256=benchmark_sha256,
                    runtime_backend=runtime_backend,
                )
        except GitHubActionsError as report_exc:
            _print_error(exc)
            _print_error(report_exc)
            raise typer.Exit(code=2) from report_exc
    _exit_with_error(exc)


def _print_error(exc: BaseException) -> None:
    error_console.print(f"Error: {exc}", markup=False, highlight=False)
    hint = format_error_hint(exc)
    if hint is not None:
        error_console.print(f"Hint: {hint}", markup=False, highlight=False)


def _exit_with_error(exc: BaseException) -> NoReturn:
    _print_error(exc)
    raise typer.Exit(code=2)


def _render_results(result: SuiteRunResult) -> None:
    table = Table(show_header=True, header_style="bold")
    table.add_column("Task")
    table.add_column("Result")
    table.add_column("Agent Exit", justify="right")
    table.add_column("Verifier Exit", justify="right")
    table.add_column("Changes")
    table.add_column("Duration", justify="right")
    for task in result.results:
        table.add_row(
            task.task_id,
            "PASS" if task.passed else "FAIL",
            str(task.agent_result.exit_code),
            str(task.verifier_result.exit_code),
            file_change_label(task.workspace_changes),
            _format_duration(task.duration_seconds),
        )
    console.print(table)
    console.print()


def _render_failure_details(result: SuiteRunResult) -> None:
    failures = [task for task in result.results if not task.passed]
    if not failures:
        return
    console.print("Failure details:")
    console.print()
    for task in failures:
        detail = task.verifier_result.stderr.strip() or task.verifier_result.stdout.strip()
        if len(detail) > _FAILURE_DETAIL_LIMIT:
            detail = detail[: _FAILURE_DETAIL_LIMIT - 3] + "..."
        console.print(f"{task.task_id}:")
        if detail:
            console.print(f"  {detail}")
        else:
            console.print("  Verifier exited with a non-zero status.")
    console.print()


def _render_summary(result: SuiteRunResult) -> None:
    console.print(f"{result.passed_count} passed, {result.failed_count} failed")
    console.print(f"Total: {_format_duration(result.duration_seconds)}")


def _render_execution(result: SuiteRunResult) -> None:
    if result.execution_complete and result.unknown_agent_failure_count == 0:
        console.print("Provider execution: complete")
        return
    state = "complete" if result.execution_complete else "incomplete"
    if not result.execution_complete:
        state = "benchmark execution incomplete"
    console.print("Provider execution:")
    console.print(f"  {result.blocking_agent_failure_count} blocking failures")
    console.print(f"  {result.unknown_agent_failure_count} unknown failures")
    console.print(f"  {state}")
    for task in result.results:
        failure = task.agent_result.failure
        if failure is None:
            continue
        console.print(f"{task.task_id}: {failure.kind.value}")


def _suite_exit_code(result: SuiteRunResult) -> int:
    """Return 2 for a blocking provider failure, 1 for verifier failure, else 0."""
    if not result.execution_complete:
        return 2
    return 0 if result.passed else 1


def _format_duration(seconds: float) -> str:
    return f"{seconds:.1f}s"
