# Codex Debug Prompt

Debug the existing workflow.

Focus on:
- Playwright MCP connection issues
- Playwright CAPTCHA detection or anti-bot failures
- Playwright manual-solve wait timing, polling, and browser-state checks
- cases where Playwright should have kept polling the existing browser state before returning `query_failed` or `blocked`
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
