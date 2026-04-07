# Codex Debug Prompt

Debug the existing workflow.

Focus on:
- Playwright MCP connection issues
- Playwright CAPTCHA detection or anti-bot failures
- `@camoufox-browser` fallback availability and handoff for a single blocked job
- cases where Playwright failed on CAPTCHA but Camoufox should have been attempted before marking the job blocked
- `.env` / `applicant.md` loading or validation issues
- Google result extraction
- Greenhouse and Ashby date parsing
- SQLite schema or locking issues
- duplicate detection bugs
- failed application submissions

Deliver:
- root cause
- minimal fix
- updated code
- rerun summary
