# Security

AgentDocsBench is an open-source benchmark harness. It is not a secure sandbox.

A benchmark run can execute:

- an external coding-agent CLI
- code that the agent writes, through the verifier
- optional Docker containers

## Local execution

The default runtime starts the coding agent and the verifier as host processes. Copying the starter and docs into a temporary directory does not confine those processes. Do not run an untrusted benchmark, an untrusted provider CLI, or untrusted generated code on a machine that has sensitive files, credentials, or network access you cannot afford to expose.

## Docker

`--runtime-config` runs the agent and the verifier in separate containers. That is stronger isolation than a host process. It is not a perfect security boundary. The Docker daemon is trusted. Kernel and runtime vulnerabilities are out of scope. Anything mounted into the agent container, and any environment value passed through, is visible there. Agent network access can send that data out.

Do not mount:

- the Docker socket
- your whole home directory
- credential directories you do not intend the agent to read

The filesystem root and the Docker socket are rejected by the runtime loader. A large explicit mount still weakens the boundary. The verifier container uses network `none` and does not receive the agent's environment. AgentDocsBench does not mount the Docker socket into either container.

## Doctor

`agentdocs doctor` checks configuration, paths, and whether a provider executable or Docker is available. It does not inspect credentials, provider secrets, environment values, or Docker auth config. It does not log in, and it does not run a benchmark.

## Logs and artifacts

Structured artifacts are written to omit environment values and credential contents. Raw logs may still contain source code, documentation, provider messages, or other sensitive output:

- `agent.stdout.log`
- `agent.stderr.log`
- `verifier.stdout.log`
- `verifier.stderr.log`

Review those files before sharing a run. Documentation-experiment artifacts do not store documentation file contents or task prompt text.

## Reporting a vulnerability

This repository does not list a security contact. If the GitHub repository has private vulnerability reporting enabled, use that. Otherwise open a private report through whatever channel the maintainers have configured. Do not send credentials or raw logs in a public issue.
