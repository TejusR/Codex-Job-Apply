from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from job_apply_bot.resume_customization import (
    ResumeCustomizationError,
    compile_latex_resume,
    parse_resume_template,
    render_customized_resume,
    validate_customization_payload,
)


class ResumeCustomizationTests(unittest.TestCase):
    def _write_template(self, root: Path) -> Path:
        path = root / "resume-template.tex"
        path.write_text(
            textwrap.dedent(
                r"""
                \documentclass{article}
                \begin{document}
                \section*{Summary}
                \begin{itemize}
                % BEGIN AUTO_SUMMARY
                \item Original summary
                % END AUTO_SUMMARY
                \end{itemize}
                \section*{Skills}
                \begin{itemize}
                % BEGIN AUTO_SKILLS
                \item Original skills
                % END AUTO_SKILLS
                \end{itemize}
                \section*{Experience}
                Company and dates stay immutable.
                \begin{itemize}
                % BEGIN AUTO_BULLETS:experience
                \item Original bullet
                % END AUTO_BULLETS:experience
                \end{itemize}
                \end{document}
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        return path

    def test_template_parser_detects_required_mutable_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            template = parse_resume_template(self._write_template(Path(temp_dir)))

            self.assertEqual(template.bullet_slugs, ["experience"])
            self.assertEqual(len(template.blocks), 3)

    def test_render_customized_resume_replaces_only_mutable_regions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            template = parse_resume_template(self._write_template(Path(temp_dir)))
            payload = {
                "summary": ["Tailored summary"],
                "skills": ["Programming: Java, Python"],
                "bullet_blocks": [
                    {
                        "slug": "experience",
                        "bullets": ["Built resilient backend systems."],
                    }
                ],
            }

            rendered = render_customized_resume(template, payload)

            self.assertIn(r"\item Tailored summary", rendered)
            self.assertIn(r"\item Programming: Java, Python", rendered)
            self.assertIn(r"\item Built resilient backend systems.", rendered)
            self.assertIn("Company and dates stay immutable.", rendered)

    def test_validate_customization_payload_rejects_unknown_bullet_slug(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            template = parse_resume_template(self._write_template(Path(temp_dir)))

            with self.assertRaisesRegex(ValueError, "Unknown bullet block slug"):
                validate_customization_payload(
                    {
                        "summary": ["Tailored summary"],
                        "skills": ["Programming: Java, Python"],
                        "bullet_blocks": [
                            {"slug": "unknown", "bullets": ["Bullet"]},
                        ],
                    },
                    template=template,
                )

    def test_compile_latex_resume_raises_when_compiler_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tex_path = root / "resume.tex"
            tex_path.write_text(r"\documentclass{article}\begin{document}Hi", encoding="utf-8")

            with patch(
                "job_apply_bot.resume_customization.shutil.which",
                side_effect=lambda name: name,
            ), patch(
                "job_apply_bot.resume_customization.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    ["latexmk"],
                    1,
                    "compile failed",
                    "",
                ),
            ):
                with self.assertRaisesRegex(ResumeCustomizationError, "latexmk failed"):
                    compile_latex_resume(tex_path=tex_path, output_dir=root)


if __name__ == "__main__":
    unittest.main()
