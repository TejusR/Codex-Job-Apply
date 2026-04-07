# MCP Setup Notes

This workflow expects Playwright MCP to be available to Codex for the default browser flow and `@camoufox-browser` to be available as a per-job CAPTCHA fallback.

## Expectations

Playwright MCP should cover the normal browser automation flow:
- open pages
- click
- type
- upload files
- wait for elements
- extract text
- navigate backward and forward

`@camoufox-browser` should be available when the workflow needs to retry a specific job that is blocked by a visible CAPTCHA or other anti-bot challenge in Playwright.

## Browser Tool Roles

- Use Playwright MCP for search, extraction, and standard application attempts.
- If Playwright encounters a CAPTCHA for the current job, reopen that same job in `@camoufox-browser` and continue only that application there.
- After the affected job is complete or conclusively blocked, return to Playwright for the rest of the workflow.
- If Camoufox cannot get past the challenge either, record the outcome as `blocked` and add a structured finding with category `captcha`.

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
- treat a visible CAPTCHA, reCAPTCHA iframe, or challenge page in Playwright as the trigger to switch tools for that one job
