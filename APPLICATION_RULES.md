# Application Rules

## Requirements

The automation must:

- apply to jobs one by one
- apply exhaustively until every discovered eligible job has been attempted or explicitly recorded as blocked
- never submit the same application twice
- stop only when no jobs remain
- record success and failure
- record structured findings for blocked, incomplete, and failed outcomes
- preserve enough metadata for auditability

## Freshness Rules

- prefer jobs with verifiable freshness in the last 24 hours
- if a job appears in the search scope but its freshness cannot be verified after reasonable extraction attempts, do not skip it solely for that reason
- record when a job moved forward with unverified freshness

## Form Rules

- only fill fields with truthful stored data
- never fabricate years of experience, education, title, sponsorship status, or work authorization
- when a supporting detail is missing, make a reasonable assumption from the applicant profile, resume, and `applicant.md` rather than skipping the job for that reason alone
- use profile-based assumptions for fields such as salary expectations, earliest start date, notice period, and concise summary responses
- do not answer free-response questions with claims that conflict with the documented applicant profile
- only mark an application incomplete when the missing information cannot be reasonably inferred from the applicant materials or when the site requires a hard fact that is unavailable

## Resume / Cover Letter

If local files are configured:
- upload them when the page requests them

If no document is available:
- do not fake upload success
- mark as incomplete if upload is required

## Retry Policy

- do not endlessly retry a broken page
- allow at most one retry for transient page issues
- record the error message
- classify permanent blockers as `blocked`
- classify missing truthful hard facts or required files as `incomplete`
- classify transient or unverifiable submission problems as `failed`
- only `failed` is retryable later, and only if the same job is rediscovered in a future run
- do not auto-retry failed jobs within the same run

## Findings Capture

When an application cannot complete cleanly:
- record one or more structured findings with `application_status`, `stage`, `category`, `summary`, `detail`, and `page_url`
- use findings to preserve enough detail to improve the workflow later
- do not rely only on free-form `error_message` text when a blocker or failure can be categorized

## Confirmation Capture

After submission, try to store:
- confirmation URL
- visible confirmation message
- timestamp
