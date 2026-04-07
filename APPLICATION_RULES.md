# Application Rules

## Requirements

The automation must:

- apply to jobs one by one
- never submit the same application twice
- stop only when no jobs remain
- record success and failure
- preserve enough metadata for auditability

## Form Rules

- only fill fields with truthful stored data
- never fabricate years of experience, education, title, sponsorship status, or work authorization
- do not answer free-response questions with invented claims
- if required info is missing, mark application as incomplete and skip submission

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

## Confirmation Capture

After submission, try to store:
- confirmation URL
- visible confirmation message
- timestamp
