# MCP Setup Notes

This workflow expects Playwright MCP to be available to Codex for the default browser flow and `@camoufox-browser` to be available as a visible manual-solve CAPTCHA fallback for both search discovery and job applications.

## Expectations

Playwright MCP should cover the normal browser automation flow:
- open pages
- click
- type
- upload files
- wait for elements
- extract text
- navigate backward and forward

`@camoufox-browser` should be available when the workflow needs to continue a Google search step or a specific job application that is blocked by a visible CAPTCHA or other anti-bot challenge in Playwright.

## Browser Tool Roles

- Use Playwright MCP for search, extraction, and standard application attempts.
- If Playwright encounters a CAPTCHA during Google discovery, reopen or reuse that same search step in a visible `@camoufox-browser` window and continue discovery there after manual solve.
- If Playwright encounters a CAPTCHA for the current job application, reopen or reuse that same job in a visible `@camoufox-browser` window and continue only that application there after manual solve.
- Manual solve waits may last indefinitely. Poll in bounded increments, but do not impose an overall timeout while waiting for the challenge to clear.
- After the affected step is complete or conclusively blocked, return to Playwright for the rest of the workflow.
- If Camoufox cannot get past the challenge either, record the outcome as `query_failed` for discovery or `blocked` for application, based on the affected workflow step.

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
- treat a visible CAPTCHA, reCAPTCHA iframe, or challenge page in Playwright as the trigger to switch tools for that affected search or application step
- use ASCII-only status text during long manual-wait loops to avoid Windows console encoding issues
