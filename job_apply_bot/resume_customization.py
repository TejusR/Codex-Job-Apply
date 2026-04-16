from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import shutil
import subprocess


_BEGIN_BLOCK_PATTERN = re.compile(
    r"^\s*% BEGIN AUTO_(SUMMARY|SKILLS|BULLETS:([A-Za-z0-9_-]+))\s*$"
)
_END_BLOCK_PATTERN = re.compile(
    r"^\s*% END AUTO_(SUMMARY|SKILLS|BULLETS:([A-Za-z0-9_-]+))\s*$"
)
_SPECIAL_LATEX_CHARS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


class ResumeCustomizationError(RuntimeError):
    pass


@dataclass(slots=True)
class TemplateBlock:
    block_type: str
    slug: str | None
    content_start: int
    content_end: int
    original_content: str


@dataclass(slots=True)
class ResumeTemplate:
    path: Path
    text: str
    blocks: list[TemplateBlock]

    @property
    def bullet_slugs(self) -> list[str]:
        return [block.slug for block in self.blocks if block.block_type == "bullets" and block.slug]


def parse_resume_template(path: Path) -> ResumeTemplate:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    blocks: list[TemplateBlock] = []
    index = 0
    while index < len(lines):
        begin_match = _BEGIN_BLOCK_PATTERN.match(lines[index])
        if begin_match is None:
            index += 1
            continue

        begin_type = begin_match.group(1)
        begin_slug = begin_match.group(2)
        end_index = index + 1
        while end_index < len(lines):
            end_match = _END_BLOCK_PATTERN.match(lines[end_index])
            if end_match is not None:
                end_type = end_match.group(1)
                end_slug = end_match.group(2)
                if end_type != begin_type or end_slug != begin_slug:
                    raise ResumeCustomizationError(
                        f"Mismatched template block markers in {path}: "
                        f"BEGIN {begin_type} / END {end_type}"
                    )
                break
            end_index += 1

        if end_index >= len(lines):
            raise ResumeCustomizationError(
                f"Unterminated template block marker in {path}: {lines[index].strip()}"
            )

        block_type = "bullets" if begin_type.startswith("BULLETS:") else begin_type.lower()
        content_start = offsets[index + 1]
        content_end = offsets[end_index]
        blocks.append(
            TemplateBlock(
                block_type=block_type,
                slug=begin_slug,
                content_start=content_start,
                content_end=content_end,
                original_content=text[content_start:content_end],
            )
        )
        index = end_index + 1

    if not any(block.block_type == "summary" for block in blocks):
        raise ResumeCustomizationError(f"Template {path} is missing % BEGIN AUTO_SUMMARY.")
    if not any(block.block_type == "skills" for block in blocks):
        raise ResumeCustomizationError(f"Template {path} is missing % BEGIN AUTO_SKILLS.")
    if not any(block.block_type == "bullets" for block in blocks):
        raise ResumeCustomizationError(
            f"Template {path} is missing at least one % BEGIN AUTO_BULLETS:<slug> block."
        )

    return ResumeTemplate(path=path, text=text, blocks=blocks)


def validate_customization_payload(
    payload: object,
    *,
    template: ResumeTemplate,
) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Resume customization worker output must be a JSON object.")

    for field_name in ("summary", "skills", "bullet_blocks"):
        if field_name not in payload:
            raise ValueError(
                f"Resume customization worker output is missing '{field_name}'."
            )

    summary = payload.get("summary")
    if not isinstance(summary, list) or not summary:
        raise ValueError("Field 'summary' must be a non-empty list.")
    for item in summary:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("Summary items must be non-empty strings.")

    skills = payload.get("skills")
    if not isinstance(skills, list) or not skills:
        raise ValueError("Field 'skills' must be a non-empty list.")
    for item in skills:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("Skill lines must be non-empty strings.")

    bullet_blocks = payload.get("bullet_blocks")
    if not isinstance(bullet_blocks, list):
        raise ValueError("Field 'bullet_blocks' must be a list.")

    seen_slugs: set[str] = set()
    template_slugs = set(template.bullet_slugs)
    for item in bullet_blocks:
        if not isinstance(item, dict):
            raise ValueError("Each bullet block must be an object.")
        slug = item.get("slug")
        bullets = item.get("bullets")
        if not isinstance(slug, str) or not slug.strip():
            raise ValueError("Bullet blocks must include a non-empty 'slug'.")
        if slug not in template_slugs:
            raise ValueError(f"Unknown bullet block slug: {slug}")
        if slug in seen_slugs:
            raise ValueError(f"Duplicate bullet block slug: {slug}")
        seen_slugs.add(slug)
        if not isinstance(bullets, list) or not bullets:
            raise ValueError(f"Bullet block '{slug}' must include a non-empty bullets list.")
        for bullet in bullets:
            if not isinstance(bullet, str) or not bullet.strip():
                raise ValueError(f"Bullet block '{slug}' contains an empty bullet.")


def render_customized_resume(
    template: ResumeTemplate,
    payload: dict[str, object],
) -> str:
    bullet_map = {
        str(item["slug"]): [str(bullet).strip() for bullet in item["bullets"]]
        for item in payload["bullet_blocks"]
        if isinstance(item, dict)
    }
    summary = [str(item).strip() for item in payload["summary"]]
    skills = [str(item).strip() for item in payload["skills"]]

    parts: list[str] = []
    cursor = 0
    for block in template.blocks:
        parts.append(template.text[cursor:block.content_start])
        if block.block_type == "summary":
            replacement = _render_bullet_items(summary)
        elif block.block_type == "skills":
            replacement = _render_bullet_items(skills)
        elif block.block_type == "bullets" and block.slug:
            replacement = _render_bullet_items(
                bullet_map.get(block.slug) or _extract_existing_bullets(block.original_content)
            )
        else:
            replacement = block.original_content
        parts.append(replacement)
        cursor = block.content_end
    parts.append(template.text[cursor:])
    return "".join(parts)


def build_preview_content(
    *,
    payload: dict[str, object],
    job_title: str | None,
    company: str | None,
) -> str:
    lines: list[str] = []
    header_bits = [item for item in (job_title, company) if item]
    if header_bits:
        lines.append(f"# Tailored Resume Preview: {' - '.join(header_bits)}")
        lines.append("")

    lines.append("## Summary")
    for item in payload["summary"]:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## Skills")
    for item in payload["skills"]:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## Tailored Experience Highlights")
    for item in payload["bullet_blocks"]:
        slug = str(item["slug"])
        lines.append(f"### {slug}")
        for bullet in item["bullets"]:
            lines.append(f"- {bullet}")
        lines.append("")

    return "\n".join(lines).strip()


def compile_latex_resume(
    *,
    tex_path: Path,
    output_dir: Path,
) -> tuple[Path, str]:
    latexmk_path = shutil.which("latexmk")
    if latexmk_path:
        command = [
            latexmk_path,
            "-pdf",
            "-interaction=nonstopmode",
            "-halt-on-error",
            f"-output-directory={output_dir}",
            str(tex_path),
        ]
        completed = subprocess.run(
            command,
            cwd=output_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode != 0:
            raise ResumeCustomizationError(_latex_error_message("latexmk", completed))
        pdf_path = output_dir / f"{tex_path.stem}.pdf"
        if pdf_path.exists():
            return pdf_path, "latexmk"

    pdflatex_path = shutil.which("pdflatex")
    if pdflatex_path is None:
        raise ResumeCustomizationError("No LaTeX compiler was found in PATH.")

    command = [
        pdflatex_path,
        "-interaction=nonstopmode",
        "-halt-on-error",
        f"-output-directory={output_dir}",
        str(tex_path),
    ]
    last_completed: subprocess.CompletedProcess[str] | None = None
    for _ in range(2):
        last_completed = subprocess.run(
            command,
            cwd=output_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if last_completed.returncode != 0:
            raise ResumeCustomizationError(_latex_error_message("pdflatex", last_completed))

    pdf_path = output_dir / f"{tex_path.stem}.pdf"
    if not pdf_path.exists():
        raise ResumeCustomizationError(
            f"LaTeX compilation finished without producing {pdf_path.name}."
        )
    return pdf_path, "pdflatex"


def job_description_hash(description_text: str | None) -> str:
    normalized = (description_text or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def latex_escape(text: str) -> str:
    escaped_parts: list[str] = []
    for char in text:
        escaped_parts.append(_SPECIAL_LATEX_CHARS.get(char, char))
    escaped = "".join(escaped_parts)
    escaped = escaped.replace("–", "--")
    escaped = escaped.replace("—", "---")
    escaped = escaped.replace("→", r"$\rightarrow$")
    return escaped


def _render_bullet_items(items: list[str]) -> str:
    rendered_lines = [f"\\item {latex_escape(item)}\n" for item in items if item.strip()]
    if not rendered_lines:
        return ""
    return "".join(rendered_lines)


def _extract_existing_bullets(block_content: str) -> list[str]:
    bullets: list[str] = []
    for line in block_content.splitlines():
        stripped = line.strip()
        if stripped.startswith(r"\item "):
            bullets.append(stripped[len(r"\item ") :].strip())
    return bullets


def _latex_error_message(
    compiler_name: str,
    completed: subprocess.CompletedProcess[str],
) -> str:
    streams = [stream.strip() for stream in (completed.stdout, completed.stderr) if stream.strip()]
    detail = "\n".join(streams[-10:]) if streams else "Unknown LaTeX error."
    return f"{compiler_name} failed with exit code {completed.returncode}: {detail}"
