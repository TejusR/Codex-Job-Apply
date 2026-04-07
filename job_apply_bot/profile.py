from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .search import (
    SEARCH_SITE_ALIASES,
    SUPPORTED_SEARCH_SITES,
    build_google_queries,
    supported_search_sites_text,
)

UNKNOWN_TOKENS = {"", "unknown", "todo", "tbd", "n/a", "na", "fill-me"}

REQUIRED_ENV_KEYS = (
    "APPLICANT_FULL_NAME",
    "APPLICANT_EMAIL",
    "APPLICANT_PHONE",
    "APPLICANT_LOCATION",
    "APPLICANT_RESUME_PATH",
    "APPLICANT_US_WORK_AUTHORIZED",
    "APPLICANT_REQUIRES_VISA_SPONSORSHIP",
)

OPTIONAL_ENV_KEYS = (
    "APPLICANT_OPEN_TO_RELOCATION",
    "APPLICANT_LINKEDIN_URL",
    "APPLICANT_GITHUB_URL",
    "APPLICANT_PORTFOLIO_URL",
    "APPLICANT_COVER_LETTER_PATH",
    "APPLICANT_CURRENT_VISA_STATUS",
    "APPLICANT_TARGET_ROLE_KEYWORDS",
    "APPLICANT_ALLOWED_LOCATIONS",
    "APPLICANT_REMOTE_PREFERENCE",
    "APPLICANT_ENABLED_SEARCH_SITES",
)

SECTION_PATTERN = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def normalize_value(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip()
    if value.lower() in UNKNOWN_TOKENS:
        return None
    return value


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(path)

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def parse_bool(value: str | None) -> bool | None:
    normalized = normalize_value(value)
    if normalized is None:
        return None
    lowered = normalized.lower()
    if lowered in {"true", "1", "yes", "y"}:
        return True
    if lowered in {"false", "0", "no", "n"}:
        return False
    return None


def parse_csv(value: str | None) -> list[str]:
    normalized = normalize_value(value)
    if normalized is None:
        return []
    return [item.strip() for item in normalized.split(",") if item.strip()]


def parse_search_sites(value: str | None) -> list[str]:
    normalized = normalize_value(value)
    if normalized is None:
        return list(SUPPORTED_SEARCH_SITES)

    enabled_sites: list[str] = []
    seen_sites: set[str] = set()
    for item in normalized.split(","):
        token = item.strip().lower()
        if not token:
            continue
        canonical_site = SEARCH_SITE_ALIASES.get(token)
        if canonical_site is None or canonical_site in seen_sites:
            continue
        enabled_sites.append(canonical_site)
        seen_sites.add(canonical_site)
    return enabled_sites


def invalid_search_sites(value: str | None) -> list[str]:
    normalized = normalize_value(value)
    if normalized is None:
        return []

    invalid_sites: list[str] = []
    for item in normalized.split(","):
        token = item.strip()
        if not token:
            continue
        if token.lower() not in SEARCH_SITE_ALIASES:
            invalid_sites.append(token)
    return invalid_sites


def resolve_profile_path(root: Path, value: str | None) -> Path | None:
    normalized = normalize_value(value)
    if normalized is None:
        return None
    candidate = Path(normalized)
    if not candidate.is_absolute():
        candidate = (root / candidate).resolve()
    return candidate


@dataclass(slots=True)
class ApplicantNotes:
    path: Path
    sections: dict[str, str]


def parse_applicant_markdown(path: Path) -> ApplicantNotes:
    if not path.exists():
        raise FileNotFoundError(path)

    sections: dict[str, list[str]] = {}
    current_section = "document"
    sections[current_section] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        match = SECTION_PATTERN.match(line)
        if match and len(match.group(1)) <= 2:
            current_section = match.group(2).strip().lower()
            sections.setdefault(current_section, [])
            continue
        sections.setdefault(current_section, []).append(line.rstrip())

    normalized_sections = {
        key: "\n".join(value).strip()
        for key, value in sections.items()
        if "\n".join(value).strip()
    }
    return ApplicantNotes(path=path, sections=normalized_sections)


@dataclass(slots=True)
class ProfileValidationResult:
    env_path: Path
    applicant_md_path: Path
    env_values: dict[str, str]
    applicant_sections: dict[str, str]
    missing_required_fields: list[str]
    missing_optional_fields: list[str]
    missing_required_files: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.missing_required_fields and not self.missing_required_files

    def to_dict(self) -> dict[str, object]:
        resume_path = resolve_profile_path(
            self.env_path.parent, self.env_values.get("APPLICANT_RESUME_PATH")
        )
        cover_letter_path = resolve_profile_path(
            self.env_path.parent, self.env_values.get("APPLICANT_COVER_LETTER_PATH")
        )
        target_role_keywords = parse_csv(self.env_values.get("APPLICANT_TARGET_ROLE_KEYWORDS"))
        enabled_search_sites = parse_search_sites(
            self.env_values.get("APPLICANT_ENABLED_SEARCH_SITES")
        )
        return {
            "ok": self.ok,
            "env_path": str(self.env_path),
            "applicant_md_path": str(self.applicant_md_path),
            "missing_required_fields": self.missing_required_fields,
            "missing_optional_fields": self.missing_optional_fields,
            "missing_required_files": self.missing_required_files,
            "warnings": self.warnings,
            "google_search_queries": build_google_queries(
                enabled_search_sites, target_role_keywords
            ),
            "profile": {
                "full_name": normalize_value(self.env_values.get("APPLICANT_FULL_NAME")),
                "email": normalize_value(self.env_values.get("APPLICANT_EMAIL")),
                "phone": normalize_value(self.env_values.get("APPLICANT_PHONE")),
                "location": normalize_value(self.env_values.get("APPLICANT_LOCATION")),
                "open_to_relocation": parse_bool(
                    self.env_values.get("APPLICANT_OPEN_TO_RELOCATION")
                ),
                "linkedin_url": normalize_value(
                    self.env_values.get("APPLICANT_LINKEDIN_URL")
                ),
                "github_url": normalize_value(self.env_values.get("APPLICANT_GITHUB_URL")),
                "portfolio_url": normalize_value(
                    self.env_values.get("APPLICANT_PORTFOLIO_URL")
                ),
                "resume_path": str(resume_path) if resume_path else None,
                "cover_letter_path": str(cover_letter_path) if cover_letter_path else None,
                "us_work_authorized": parse_bool(
                    self.env_values.get("APPLICANT_US_WORK_AUTHORIZED")
                ),
                "requires_visa_sponsorship": parse_bool(
                    self.env_values.get("APPLICANT_REQUIRES_VISA_SPONSORSHIP")
                ),
                "current_visa_status": normalize_value(
                    self.env_values.get("APPLICANT_CURRENT_VISA_STATUS")
                ),
                "target_role_keywords": target_role_keywords,
                "allowed_locations": parse_csv(
                    self.env_values.get("APPLICANT_ALLOWED_LOCATIONS")
                ),
                "remote_preference": normalize_value(
                    self.env_values.get("APPLICANT_REMOTE_PREFERENCE")
                ),
                "enabled_search_sites": enabled_search_sites,
            },
            "applicant_markdown_sections": sorted(self.applicant_sections),
        }


def validate_profile(root: Path) -> ProfileValidationResult:
    env_path = root / ".env"
    applicant_md_path = root / "applicant.md"

    env_values: dict[str, str] = {}
    missing_required_fields: list[str] = []
    missing_optional_fields: list[str] = []
    missing_required_files: list[str] = []
    warnings: list[str] = []

    if env_path.exists():
        env_values = parse_env_file(env_path)
        for key in REQUIRED_ENV_KEYS:
            if normalize_value(env_values.get(key)) is None:
                missing_required_fields.append(key)
        for key in OPTIONAL_ENV_KEYS:
            if normalize_value(env_values.get(key)) is None:
                missing_optional_fields.append(key)
        resume_path = resolve_profile_path(root, env_values.get("APPLICANT_RESUME_PATH"))
        if resume_path is None or not resume_path.exists():
            missing_required_files.append("APPLICANT_RESUME_PATH")
        cover_letter_path = resolve_profile_path(
            root, env_values.get("APPLICANT_COVER_LETTER_PATH")
        )
        if cover_letter_path is not None and not cover_letter_path.exists():
            warnings.append("APPLICANT_COVER_LETTER_PATH points to a missing file.")
        invalid_sites = invalid_search_sites(
            env_values.get("APPLICANT_ENABLED_SEARCH_SITES")
        )
        if invalid_sites:
            warnings.append(
                "APPLICANT_ENABLED_SEARCH_SITES includes unsupported values: "
                + ", ".join(invalid_sites)
                + ". Supported values: "
                + supported_search_sites_text()
                + "."
            )
        raw_search_sites = normalize_value(env_values.get("APPLICANT_ENABLED_SEARCH_SITES"))
        if raw_search_sites is not None and not parse_search_sites(raw_search_sites):
            warnings.append(
                "APPLICANT_ENABLED_SEARCH_SITES does not enable any supported sites."
            )
    else:
        missing_required_files.append(".env")

    applicant_sections: dict[str, str] = {}
    if applicant_md_path.exists():
        applicant_sections = parse_applicant_markdown(applicant_md_path).sections
    else:
        warnings.append("applicant.md was not found.")

    if applicant_sections:
        if "work authorization notes" not in applicant_sections:
            warnings.append(
                "applicant.md is missing a 'Work Authorization Notes' section."
            )
        if "reusable highlights" not in applicant_sections:
            warnings.append("applicant.md is missing a 'Reusable Highlights' section.")

    return ProfileValidationResult(
        env_path=env_path,
        applicant_md_path=applicant_md_path,
        env_values=env_values,
        applicant_sections=applicant_sections,
        missing_required_fields=missing_required_fields,
        missing_optional_fields=missing_optional_fields,
        missing_required_files=missing_required_files,
        warnings=warnings,
    )
