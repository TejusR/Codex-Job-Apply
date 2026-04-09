Read the workflow documents and attempt exactly one job application for the single job in the runtime context.

Requirements:
- Use the repository files named in the runtime context as source of truth.
- Use browser tooling to open the job page and complete the application flow.
- Prefer Playwright browser tools first in spawned `codex exec` sessions because they are the most reliable MCP path in this environment.
- Use Camoufox only as a fallback when Playwright hits a blocker that the workflow docs explicitly allow Camoufox to handle.
- Use only truthful applicant information.
- Make reasonable profile-based assumptions for supporting answers when allowed by the workflow docs.
- Use the resume and cover-letter paths from the runtime context when uploads are requested.
- If Playwright/browser automation encounters a CAPTCHA or anti-bot challenge, follow the workflow guidance in the docs for the available browser tools in this environment.
- Do not edit repository files.

You must return one terminal outcome for this single job:
- `submitted`
- `failed`
- `blocked`
- `incomplete`
- `duplicate_skipped`

Output contract:
- Always return valid JSON matching the provided schema.
- Include `confirmation_text`, `confirmation_url`, and `error_message` when available, otherwise set them to `null`.
- Include structured `findings` for every `failed`, `blocked`, or `incomplete` outcome.
- Each finding must include `stage`, `category`, and `summary`. Add `detail` and `page_url` when helpful.

Do not return prose outside the JSON object.
