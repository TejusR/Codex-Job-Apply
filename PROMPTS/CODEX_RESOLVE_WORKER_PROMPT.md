Read the workflow documents and resolve exactly one discovered search result from the runtime context.

Requirements:
- Use the repository files named in the runtime context as source of truth.
- Use browser tooling to open the exact URL from the runtime context.
- Prefer Playwright browser tools first in spawned `codex exec` sessions because they are the most reliable MCP path in this environment.
- Use Camoufox only as a fallback when Playwright hits a blocker that the workflow docs explicitly allow Camoufox to handle.
- Decide whether the URL is:
  - a direct job page that can be ingested immediately, or
  - a listing page that exposes multiple child job links.
- For a direct job page, extract the best available job metadata and return a single resolved job.
- For a listing page, extract child job URLs in visible page order and return them as normalized child results.
- Do not submit an application during this step.
- If the page is not useful for the workflow, return `skip_result` with a concrete reason.
- If an unrecoverable resolution error happens, return `result_failed` with a concrete error message.
- Do not edit tracked repository source files.

You must return one outcome for this single result:
- `resolved_job`
- `expanded`
- `skip_result`
- `result_failed`

Output contract:
- Always return valid JSON matching the provided schema.
- Always include `outcome`, `job`, `child_results`, `skip_reason`, and `error_message`.
- For `resolved_job`, include the job metadata object and an empty `child_results` list.
- For `expanded`, include `job = null` and at least one child result with `url`, `title`, `snippet`, `visible_date`, `page_number`, and `rank`.
- For `skip_result`, include `job = null`, an empty `child_results` list, a non-empty `skip_reason`, and `error_message = null`.
- For `result_failed`, include `job = null`, an empty `child_results` list, `skip_reason = null`, and a non-empty `error_message`.

Do not return prose outside the JSON object.
