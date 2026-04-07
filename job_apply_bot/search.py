from __future__ import annotations

SUPPORTED_SEARCH_SITES = (
    "jobright",
    "greenhouse",
    "ashby",
    "workable",
    "jobvite",
    "jazz",
    "adp",
    "lever",
    "bamboohr",
    "paylocity",
    "smartrecruiters",
    "gem",
    "dover",
)

SEARCH_SITE_DOMAINS = {
    "jobright": "jobright.ai",
    "greenhouse": "boards.greenhouse.io",
    "ashby": "jobs.ashbyhq.com",
    "workable": "apply.workable.com",
    "jobvite": "jobs.jobvite.com",
    "jazz": "app.jazz.co",
    "adp": "recruiting.adp.com",
    "lever": "jobs.lever.co",
    "bamboohr": "bamboohr.com",
    "paylocity": "recruiting.paylocity.com",
    "smartrecruiters": "jobs.smartrecruiters.com",
    "gem": "jobs.gem.com",
    "dover": "app.dover.com",
}

SEARCH_SITE_ALIASES = {
    "jobright": "jobright",
    "jobright.ai": "jobright",
    "greenhouse": "greenhouse",
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "ashby": "ashby",
    "ashbyhq": "ashby",
    "jobs.ashbyhq.com": "ashby",
    "workable": "workable",
    "apply.workable.com": "workable",
    "jobvite": "jobvite",
    "jobs.jobvite.com": "jobvite",
    "jazz": "jazz",
    "app.jazz.co": "jazz",
    "adp": "adp",
    "recruiting.adp.com": "adp",
    "lever": "lever",
    "jobs.lever.co": "lever",
    "bamboohr": "bamboohr",
    "bamboohr.com": "bamboohr",
    "paylocity": "paylocity",
    "recruiting.paylocity.com": "paylocity",
    "smartrecruiters": "smartrecruiters",
    "jobs.smartrecruiters.com": "smartrecruiters",
    "gem": "gem",
    "jobs.gem.com": "gem",
    "dover": "dover",
    "app.dover.com": "dover",
}

SEARCH_SITE_HOST_MATCHES = {
    "jobright": ("jobright.ai",),
    "greenhouse": ("greenhouse.io", "greenhouse"),
    "ashby": ("ashbyhq.com", "ashbyhq"),
    "workable": ("apply.workable.com",),
    "jobvite": ("jobs.jobvite.com",),
    "jazz": ("app.jazz.co",),
    "adp": ("recruiting.adp.com",),
    "lever": ("jobs.lever.co",),
    "bamboohr": ("bamboohr.com",),
    "paylocity": ("recruiting.paylocity.com",),
    "smartrecruiters": ("jobs.smartrecruiters.com",),
    "gem": ("jobs.gem.com",),
    "dover": ("app.dover.com",),
}

GOOGLE_LOCATION_HINT = '("united states" OR "remote")'


def supported_search_sites_text() -> str:
    return ", ".join(SUPPORTED_SEARCH_SITES)


def normalize_search_terms(terms: list[str] | tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    seen_terms: set[str] = set()
    for raw_term in terms:
        term = raw_term.strip()
        lowered = term.lower()
        if not term or lowered in seen_terms:
            continue
        normalized.append(term)
        seen_terms.add(lowered)
    return normalized


def build_google_query(site: str, role_keywords: list[str] | tuple[str, ...]) -> str:
    domain = SEARCH_SITE_DOMAINS[site]
    normalized_roles = normalize_search_terms(role_keywords)
    if not normalized_roles:
        return f"site:{domain} {GOOGLE_LOCATION_HINT}"

    escaped_roles = [role.replace('"', '\\"') for role in normalized_roles]
    role_clause = " OR ".join(f'"{role}"' for role in escaped_roles)
    return f"site:{domain} ({role_clause}) {GOOGLE_LOCATION_HINT}"


def build_google_queries(
    enabled_sites: list[str] | tuple[str, ...], role_keywords: list[str] | tuple[str, ...]
) -> list[dict[str, str]]:
    queries: list[dict[str, str]] = []
    for site in enabled_sites:
        if site not in SEARCH_SITE_DOMAINS:
            continue
        queries.append(
            {
                "source_key": site,
                "domain": SEARCH_SITE_DOMAINS[site],
                "query": build_google_query(site, role_keywords),
            }
        )
    return queries


def infer_source_from_hostname(hostname: str) -> str:
    normalized = hostname.lower()
    for source, fragments in SEARCH_SITE_HOST_MATCHES.items():
        if any(fragment in normalized for fragment in fragments):
            return source
    return normalized or "unknown"
