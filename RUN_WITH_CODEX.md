# How to Run This with Codex

## Best Starting Point

Run the supervised workflow from the repository root:

```bash
python -m job_apply_bot run-workflow
```

That is now the normal primary entry point.

## What to Tell Codex

The runner now launches bounded `codex exec` workers internally.

Use `PROMPTS/CODEX_MASTER_PROMPT.md` only as a legacy/manual fallback when you explicitly want one long Codex-driven run instead of the supervised Python loop.

## Supervised Execution Style

`run-workflow` now does the following:

1. validates `.env`, `applicant.md`, and file paths
2. creates or resumes a run in SQLite
3. requeues stale `applying` jobs when resuming
4. drains backlog with `next-job --mark-applying`
5. claims queries with `claim-query`
6. launches one fresh Codex query worker at a time through `codex exec`
7. ingests each discovered candidate immediately
8. launches one fresh Codex apply worker per `ready_to_apply` job
9. records application outcomes and structured findings in SQLite
10. checkpoints query progress after each processed discovery result and completes or fails each query deterministically
11. finishes only when `workflow-status` reports `drained=true`

The spawned Codex workers run in Codex's non-interactive bypass mode so Playwright/Camoufox MCP tools remain callable from those child sessions.
If a search or application CAPTCHA appears, a worker may wait indefinitely in a visible Camoufox session for manual solve before continuing the same step.

## Failure Recovery

Unsuccessful apply attempts now write raw local failure bundles under:

```text
data/codex_worker_artifacts/run-<id>/apply/
```

Those bundles may include screenshots, HTML, browser logs, runtime context, and filled personal info.

If a job failed because of an internal worker problem after discovery, recover it in the same run with:

```bash
python -m job_apply_bot requeue-runner-failures --run-id <id>
python -m job_apply_bot run-workflow --run-id <id>
```

If the same run was interrupted mid-query, `run-workflow --run-id <id>` resumes that `in_progress` query from its persisted `results_seen` and `jobs_ingested` counters.

## Legacy Manual Mode

If you still want a single long Codex conversation, open Codex in the repo root and reference `PROMPTS/CODEX_MASTER_PROMPT.md`.

That path is now best treated as a manual fallback for experimentation, not the default production entrypoint.
