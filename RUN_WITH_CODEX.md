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
10. completes or fails each query deterministically
11. finishes only when `workflow-status` reports `drained=true`

## Legacy Manual Mode

If you still want a single long Codex conversation, open Codex in the repo root and reference `PROMPTS/CODEX_MASTER_PROMPT.md`.

That path is now best treated as a manual fallback for experimentation, not the default production entrypoint.
