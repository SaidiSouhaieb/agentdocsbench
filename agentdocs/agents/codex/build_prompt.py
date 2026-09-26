"""Build the prompt sent to Codex."""


def _build_prompt(task: str, docs_path: str) -> str:
    return (
        "You are being evaluated by AgentDocsBench on whether you can complete "
        "a programming task using the provided product documentation.\n"
        "\n"
        "Task:\n"
        "\n"
        f"{task}\n"
        "\n"
        "Documentation is available at:\n"
        "\n"
        f"{docs_path}\n"
        "\n"
        "Requirements:\n"
        "- Read the documentation as needed.\n"
        "- Modify only the project in your current working directory.\n"
        "- Do not modify the documentation.\n"
        "- Do not access files outside the AgentDocsBench workspace.\n"
        "- Complete the task and stop when finished.\n"
    )
