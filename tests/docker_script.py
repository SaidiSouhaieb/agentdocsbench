"""In-memory Docker CLI for unit tests. This never talks to a daemon."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path


class DockerScript:
    """Record ``docker`` argv and return scripted create/start/inspect/rm results."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.images: dict[str, str] = {
            "agentdocs-codex:local": "sha256:aaa111",
            "agentdocs-claude:local": "sha256:bbb222",
            "agentdocs-cursor:local": "sha256:ccc333",
            "agentdocs-verifier:local": "sha256:ddd444",
        }
        self.missing_executables: set[str] = set()
        self.agent_stdout = '{"type":"thread.started"}\n'
        self.agent_stderr = ""
        self.agent_exit = 0
        self.verifier_exit = 0
        self.verifier_stdout = ""
        self.verifier_stderr = ""
        self.oom = False
        self.fail_start = False
        self.fail_inspect = False
        self.invalid_inspect = False
        self.daemon_down = False
        self.timeout = False
        self.fail_rm = False
        self.on_agent_start: Callable[[Path], None] | None = None
        self.on_verifier_start: Callable[[Path], None] | None = None
        self.created: list[str] = []
        self.removed: list[str] = []
        self.killed: list[str] = []
        self._containers: dict[str, dict[str, object]] = {}

    def __call__(
        self,
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if kwargs.get("shell") is not False:
            raise AssertionError("docker invocation must set shell=False")
        recorded = list(args)
        self.calls.append(recorded)
        if self.daemon_down and recorded[1] not in {"rm", "kill"}:
            return subprocess.CompletedProcess(
                recorded,
                1,
                "",
                "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
                "Is the docker daemon running?",
            )
        command = recorded[1]
        if command == "image":
            return self._inspect_image(recorded)
        if command == "create":
            return self._create(recorded)
        if command == "start":
            return self._start(recorded, kwargs)
        if command == "inspect":
            return self._inspect_container(recorded)
        if command == "rm":
            name = recorded[-1]
            self.removed.append(name)
            if self.fail_rm:
                return subprocess.CompletedProcess(recorded, 1, "", "rm failed")
            return subprocess.CompletedProcess(recorded, 0, name, "")
        if command == "kill":
            self.killed.append(recorded[-1])
            return subprocess.CompletedProcess(recorded, 0, recorded[-1], "")
        raise AssertionError(f"unexpected docker command: {recorded}")

    def assert_cleaned(self) -> None:
        assert self.created
        assert sorted(self.removed) == sorted(self.created)

    def creates_for(self, target: str) -> list[list[str]]:
        return [
            args
            for args in self.calls
            if len(args) > 2 and args[1] == "create" and any(target in item for item in args)
        ]

    def _inspect_image(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        image = args[3]
        image_id = self.images.get(image)
        if image_id is None:
            return subprocess.CompletedProcess(
                args,
                1,
                "",
                f"Error: No such image: {image}",
            )
        return subprocess.CompletedProcess(args, 0, json.dumps([{"Id": image_id}]), "")

    def _create(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        name = args[args.index("--name") + 1]
        entrypoint = args[args.index("--entrypoint") + 1]
        self.created.append(name)
        self._containers[name] = {"args": args, "entrypoint": entrypoint}
        return subprocess.CompletedProcess(args, 0, name + "\n", "")

    def _start(
        self,
        args: list[str],
        kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        name = args[-1]
        info = self._containers[name]
        create_args = info["args"]
        assert isinstance(create_args, list)
        role = _role(create_args)
        entrypoint = str(info["entrypoint"])
        if self.timeout and role == "agent":
            timeout = kwargs.get("timeout") if isinstance(kwargs, dict) else 1
            raise subprocess.TimeoutExpired(cmd=args, timeout=timeout or 1)
        if entrypoint in self.missing_executables:
            message = f'exec: "{entrypoint}": executable file not found in $PATH'
            info["error"] = message
            info["exit"] = 127
            return subprocess.CompletedProcess(args, 127, "", message)
        if role == "probe":
            info["error"] = ""
            info["exit"] = 0
            return subprocess.CompletedProcess(args, 0, "ok\n", "")
        if self.fail_start:
            info["error"] = "OCI runtime create failed"
            info["exit"] = 127
            return subprocess.CompletedProcess(args, 127, "", "OCI runtime create failed")
        if role == "agent" and self.oom:
            info["oom"] = True
            info["error"] = ""
            info["exit"] = 137
            return subprocess.CompletedProcess(args, 137, "", "")
        if role == "agent":
            project = _mount_source(create_args, "/workspace/project")
            if self.on_agent_start is not None and project is not None:
                self.on_agent_start(project)
            info["error"] = ""
            info["exit"] = self.agent_exit
            return subprocess.CompletedProcess(
                args,
                self.agent_exit,
                self.agent_stdout,
                self.agent_stderr,
            )
        project = _mount_source(create_args, "/workspace/project")
        if self.on_verifier_start is not None and project is not None:
            self.on_verifier_start(project)
        info["error"] = ""
        info["exit"] = self.verifier_exit
        return subprocess.CompletedProcess(
            args,
            self.verifier_exit,
            self.verifier_stdout,
            self.verifier_stderr,
        )

    def _inspect_container(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        if self.fail_inspect:
            return subprocess.CompletedProcess(args, 1, "", "Error: inspect failed")
        if self.invalid_inspect:
            return subprocess.CompletedProcess(args, 0, "not-json", "")
        info = self._containers[args[2]]
        state = {
            "ExitCode": info.get("exit", 0),
            "Error": info.get("error", ""),
            "OOMKilled": info.get("oom", False),
        }
        return subprocess.CompletedProcess(args, 0, json.dumps([{"State": state}]), "")


def _role(args: list[str]) -> str:
    joined = " ".join(args)
    if "target=/workspace/verifier" in joined:
        return "verifier"
    if "target=/workspace/project" in joined:
        return "agent"
    return "probe"


def _mount_source(args: list[str], target: str) -> Path | None:
    for item in args:
        if not item.startswith("type=bind") or f"target={target}" not in item:
            continue
        for part in item.split(","):
            if part.startswith("source="):
                return Path(part.removeprefix("source="))
    return None
