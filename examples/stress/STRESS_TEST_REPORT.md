# AgentDocsBench Stress Test

This file records one local Codex run. It is an observation from that run, not a ranking or a product score.

## Benchmark

Product: Nimbus SDK, a local fictional Python SDK. It does not use the network.

Agent: Codex

Number of tasks: 15

Config: `examples/stress/agentdocs.yaml`

Run: `examples/stress/.agentdocs/runs/20260926T094439Z-b2a7b0fe`

Command:

```bash
.venv/bin/agentdocs test --config examples/stress/agentdocs.yaml
```

## Results

| Task | Difficulty | Result | Agent Exit | Verifier Exit | Duration |
| --- | --- | --- | ---: | ---: | ---: |
| client_initialization | medium | FAIL | 1 | 1 | 5.5s |
| user_creation | medium | FAIL | 1 | 1 | 4.9s |
| organization_membership | medium | FAIL | 1 | 1 | 4.7s |
| pagination | medium-hard | FAIL | 1 | 1 | 5.5s |
| error_mapping | hard | FAIL | 1 | 1 | 4.8s |
| retry_policy | hard | FAIL | 1 | 1 | 4.8s |
| webhook_verification | hard | FAIL | 1 | 1 | 5.1s |
| idempotent_request | hard | FAIL | 1 | 1 | 4.7s |
| cache_integration | hard | FAIL | 1 | 1 | 5.1s |
| feature_flag | hard | FAIL | 1 | 1 | 18.9s |
| plugin_registration | hard | FAIL | 1 | 1 | 4.8s |
| config_precedence | very hard | FAIL | 1 | 1 | 4.8s |
| cli_command | very hard | FAIL | 1 | 1 | 5.8s |
| api_migration | very hard | FAIL | 1 | 1 | 4.6s |
| organization_sync | very hard | FAIL | 1 | 1 | 5.0s |

Durations in this table are the CLI values. Metrics below use `result.json`.

## Metrics

From `result.json` for run `20260926T094439Z-b2a7b0fe`:

- total tasks: 15
- passed: 0
- failed: 15
- pass rate: 0%
- total duration: 89.31s
- average duration: 5.95s
- median duration: 4.88s
- agent non-zero exits: 15
- verifier failures: 15

AgentDocsBench infrastructure failures: 0. The suite finished, wrote artifacts, and exited 1.

## Failed task analysis

Every task failed the same way. Codex returned this turn error and did not edit the starter:

```text
You've hit your usage limit. ... try again at 8:35 PM.
```

The verifier then ran against the untouched starter and failed. That is the expected baseline, not a wrong implementation. Primary category for all 15: the agent process could not run. This is not an AgentDocsBench harness bug. The harness recorded the non-zero agent exit, ran the verifier, continued to the next task, and saved logs.

### client_initialization

Category: AGENT_FAILURE

Codex did not attempt an edit. The docs require `app.client.build_client` to load the four JSON fields into `NimbusConfig`. The verifier still saw `NotImplementedError` in `app/client.py`.

Recommended next action: Re-run after the Codex quota resets. Do not change the task.

### user_creation

Category: AGENT_FAILURE

No edit. Docs require `client.users.create` and a dictionary with id, name, email, and metadata. The verifier still saw the starter `NotImplementedError`.

Recommended next action: Re-run later.

### organization_membership

Category: AGENT_FAILURE

No edit. Docs require creating the organization, then the user, then `add_member` with the requested role. The starter function was still unimplemented.

Recommended next action: Re-run later.

### pagination

Category: AGENT_FAILURE

No edit. Docs require `page_size=2` and following `next_cursor`. The verifier seeds five users so a hardcoded list cannot pass. The starter still raised `NotImplementedError`.

Recommended next action: Re-run later.

### error_mapping

Category: AGENT_FAILURE

No edit. Docs map auth, not-found, rate-limit, and other `NimbusError` values, and require `TypeError` for anything else. The starter mapper was still unimplemented.

Recommended next action: Re-run later.

### retry_policy

Category: AGENT_FAILURE

No edit. Docs require retrying 429, 502, and 503 only, stopping at 3 attempts, and calling `policy.sleeper` instead of sleeping. The verifier reported `count=0` because `call_with_retry` raised before calling the operation.

Recommended next action: Re-run later.

### webhook_verification

Category: AGENT_FAILURE

No edit. Docs require `WebhookVerifier`, status 401 for a bad signature, and status 400 for an unsupported event. The starter still raised `NotImplementedError`.

Recommended next action: Re-run later.

### idempotent_request

Category: AGENT_FAILURE

No edit. `users.md` and `idempotency.md` require the key to be passed to `client.users.create`. The starter helper was still unimplemented.

Recommended next action: Re-run later.

### cache_integration

Category: AGENT_FAILURE

No edit. Docs require key `user:{id}`, TTL 60, and a refresh when the injected clock expires the entry. The starter still raised `NotImplementedError`.

Recommended next action: Re-run later.

### feature_flag

Category: AGENT_FAILURE

No edit. This task took 18.9s, but `agent.stdout.log` is the same usage-limit error as the others. Docs require `new_dashboard` with default false, returning `new` or `classic`. The starter function was still unimplemented.

Recommended next action: Re-run later.

### plugin_registration

Category: AGENT_FAILURE

No edit. Docs require `AuditPlugin` registered through `PluginRegistry`, with `user.id` recorded only when the registry notifies plugins. The starter still raised `NotImplementedError`.

Recommended next action: Re-run later.

### config_precedence

Category: AGENT_FAILURE

No edit. Docs require explicit, then the env mapping, then the JSON file, then defaults, and `NimbusConfigError` when `api_key` is missing. The loader was still the stub.

Recommended next action: Re-run later.

### cli_command

Category: AGENT_FAILURE

No edit. Docs require `python -m app.cli users list --directory` to seed a client and print `{id} {email}` for every page. The command still raised `NotImplementedError`.

Recommended next action: Re-run later.

### api_migration

Category: AGENT_FAILURE

No edit. The starter still calls deprecated `client.create_user`. The verifier reported `provision_user still references create_user`.

Recommended next action: Re-run later.

### organization_sync

Category: AGENT_FAILURE

No edit. This task asks for pagination, retries, an auth `AppError` before creating an organization, member role `member`, and cache invalidation. The starter import function was still unimplemented.

Recommended next action: Re-run later. This is the task that combines four documented concepts.

## Success analysis

This run has no passing task, so there is no successful implementation to inspect.

An earlier run on 24 September, `20260924T184623Z-9e227b8d`, did complete 10 tasks before the same class of quota error. Those passes are not part of this run's score. They do show the fixture is solvable: cache, webhook, and retry tasks read the matching docs, edited one application module, and passed behavioral checks for hits and expiry, valid and invalid signatures, and retry versus permanent errors.

## Interpretation

0/15 on this run does not mean the tasks are impossible. Codex refused every turn before editing. AgentDocsBench still isolated each task, ran the deterministic verifier, and stored the logs. The score should be read as "the agent could not start," not as "the documentation was insufficient."
