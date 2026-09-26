"""Build the Codex CLI argument list."""


def _codex_command(executable: str, prompt: str, model: str | None = None) -> list[str]:
    command = [executable, "exec"]
    if model is not None:
        command.extend(["--model", model])
    command.extend(
        [
            "--json",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            prompt,
        ]
    )
    return command
