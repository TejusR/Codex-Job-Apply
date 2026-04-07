# MCP Setup Notes

This workflow expects Playwright MCP to be available to Codex.

## Expectations

The agent should have browser automation tools that can:
- open pages
- click
- type
- upload files
- wait for elements
- extract text
- navigate backward and forward

## Recommended Inputs

Keep these local files or secrets available in the repository root:
- `.env` for structured applicant profile data
- `applicant.md` for truthful application notes that do not fit in env vars
- resume path
- cover letter path
- email
- phone
- LinkedIn URL
- GitHub URL
- portfolio URL

## Operational Notes

- prefer deterministic selectors
- use explicit waits for dynamic pages
- handle multi-step forms
- capture screenshots on failure when possible
- validate `.env` with `python -m job_apply_bot validate-profile` before attempting submissions
