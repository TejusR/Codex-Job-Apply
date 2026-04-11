# MCP Setup Notes

This workflow expects Playwright MCP to be available to Codex for all browser work, including manual-solve CAPTCHA recovery in the same browser session.

## Expectations

Playwright MCP should cover the normal browser automation flow:
- open pages
- click
- type
- upload files
- wait for elements
- extract text
- navigate backward and forward
- inspect browser state repeatedly during manual CAPTCHA waits

## Browser Tool Roles

- Use Playwright MCP for search, extraction, and application attempts.
- If Playwright encounters a CAPTCHA during Google discovery, keep that same Playwright page open for manual solve, poll browser state every 10 seconds, and continue discovery only if the normal SERP returns within 10 minutes.
- If Playwright encounters a CAPTCHA for the current job application, keep that same Playwright page open for manual solve, poll browser state every 10 seconds, and continue only that application if the normal application UI returns within 10 minutes.
- Manual solve waits have a hard 10-minute cap from first detection.
- If the 10-minute wait expires first, record the outcome as `query_failed` for discovery or `blocked` for application, based on the affected workflow step.

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
- treat a visible CAPTCHA, reCAPTCHA iframe, or challenge page in Playwright as the trigger to keep polling the same browser session instead of switching tools
- use ASCII-only status text during long manual-wait loops to avoid Windows console encoding issues
