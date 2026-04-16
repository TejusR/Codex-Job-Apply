Read the runtime context and generate tailored resume content for exactly one job.

Requirements:
- Use only truthful information grounded in the runtime context, applicant notes, and the immutable facts already present in the canonical resume template.
- Customize heavily within the allowed mutable blocks only.
- Do not invent or alter employers, job titles, schools, degrees, dates, locations, or other hard facts.
- Prefer the strongest, most relevant evidence for the target role from the existing resume facts.
- Tailor summary, skills ordering, and bullet emphasis to the job description.
- Keep each output string concise enough to fit naturally in a one-page technical resume when rendered as bullet content.
- Do not emit LaTeX. Return plain text strings only.

Output contract:
- Always return valid JSON matching the provided schema.
- `summary` must be a non-empty list of bullet strings for the `AUTO_SUMMARY` block.
- `skills` must be a non-empty list of skill lines for the `AUTO_SKILLS` block.
- `bullet_blocks` must be a list of objects with `slug` and `bullets`.
- Each `slug` must match one of the `template_contract.bullet_block_slugs` values from the runtime context.
- Each `bullets` list must be non-empty and contain only factual, role-relevant bullets grounded in the applicant's actual experience.

Do not return prose outside the JSON object.
