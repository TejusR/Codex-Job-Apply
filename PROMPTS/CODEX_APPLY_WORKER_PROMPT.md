Read the workflow documents and attempt exactly one job application for the single job in the runtime context.

Requirements:
- Use the repository files named in the runtime context as source of truth.
- Use browser tooling to open the job page and complete the application flow.
- Use Playwright browser tools only in spawned `codex exec` sessions.
- Use only truthful applicant information.
- Make reasonable profile-based assumptions for supporting answers when allowed by the workflow docs.
- Use the resume and cover-letter paths from the runtime context when uploads are requested.
- If Playwright/browser automation encounters a CAPTCHA or anti-bot challenge, treat it as a manual-solve pause condition, not an immediate terminal outcome.
- Stay in the same Playwright session for the same job, keep the challenge page open for manual solve, emit only ASCII status text while waiting, and poll the browser state every 10 seconds for up to 10 minutes from first detection.
- Each poll must inspect the current page state, not just sleep. Continue only after challenge markers are gone and the normal application UI, form controls, or submit flow are back.
- After the challenge clears, continue the current application in that same Playwright session until you reach a normal terminal result.
- Do not use `blocked` only because a CAPTCHA appeared. Use `blocked` only when the challenge remains a genuine terminal blocker after the 10-minute Playwright wait window expires or another terminal blocker persists.
- If the challenge is still present after 10 minutes, return `blocked` and include at least one finding with category `captcha`.
- Do not edit tracked repository source files.
- You may write only to the failure-bundle paths provided in the runtime context.

You must return one terminal outcome for this single job:
- `submitted`
- `failed`
- `blocked`
- `incomplete`
- `duplicate_skipped`

Output contract:
- Always return valid JSON matching the provided schema.
- Always include `application_status`, `confirmation_text`, `confirmation_url`, `error_message`, and `findings`.
- Include `confirmation_text`, `confirmation_url`, and `error_message` when available, otherwise set them to `null`.
- Include structured `findings` for every `failed`, `blocked`, or `incomplete` outcome.
- Each finding object must include `stage`, `category`, `summary`, `detail`, and `page_url`.
- Set `detail` and `page_url` to `null` when they are unknown.

Before returning any `failed`, `blocked`, or `incomplete` outcome:
- save as many failure artifacts as possible to the provided failure-bundle paths
- prefer these artifacts when available:
  - `playwright_snapshot.md`
  - `playwright_screenshot.png`
  - `page_html.html`
  - `console.json`
  - `network.json`
- if a tool cannot produce one artifact directly, capture the other available artifacts and still return valid JSON

Do not return prose outside the JSON object.
