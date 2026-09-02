package profile

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"github.com/ramesh/codex-job-apply/internal/search"
)

const DefaultDiscoveryMaxPages = 5

var requiredEnvKeys = []string{
	"APPLICANT_FULL_NAME", "APPLICANT_EMAIL", "APPLICANT_PHONE", "APPLICANT_LOCATION",
	"APPLICANT_RESUME_PATH", "APPLICANT_US_WORK_AUTHORIZED", "APPLICANT_REQUIRES_VISA_SPONSORSHIP",
}
var optionalEnvKeys = []string{
	"APPLICANT_OPEN_TO_RELOCATION", "APPLICANT_LINKEDIN_URL", "APPLICANT_GITHUB_URL",
	"APPLICANT_PORTFOLIO_URL", "APPLICANT_COVER_LETTER_PATH", "APPLICANT_RESUME_TEMPLATE_PATH",
	"APPLICANT_CURRENT_VISA_STATUS", "APPLICANT_TARGET_ROLE_KEYWORDS", "APPLICANT_ALLOWED_LOCATIONS",
	"APPLICANT_REMOTE_PREFERENCE", "APPLICANT_ENABLED_SEARCH_SITES", "APPLICANT_DISCOVERY_MAX_PAGES",
}
var unknownTokens = map[string]bool{"": true, "unknown": true, "todo": true, "tbd": true, "n/a": true, "na": true, "fill-me": true}

type ValidationResult struct {
	EnvPath               string
	ApplicantMDPath       string
	EnvValues             map[string]string
	ApplicantSections     map[string]string
	MissingRequiredFields []string
	MissingOptionalFields []string
	MissingRequiredFiles  []string
	Warnings              []string
}

type Profile struct {
	FullName                *string  `json:"full_name"`
	Email                   *string  `json:"email"`
	Phone                   *string  `json:"phone"`
	Location                *string  `json:"location"`
	OpenToRelocation        *bool    `json:"open_to_relocation"`
	LinkedInURL             *string  `json:"linkedin_url"`
	GitHubURL               *string  `json:"github_url"`
	PortfolioURL            *string  `json:"portfolio_url"`
	ResumePath              *string  `json:"resume_path"`
	CoverLetterPath         *string  `json:"cover_letter_path"`
	ResumeTemplatePath      *string  `json:"resume_template_path"`
	USWorkAuthorized        *bool    `json:"us_work_authorized"`
	RequiresVisaSponsorship *bool    `json:"requires_visa_sponsorship"`
	CurrentVisaStatus       *string  `json:"current_visa_status"`
	TargetRoleKeywords      []string `json:"target_role_keywords"`
	AllowedLocations        []string `json:"allowed_locations"`
	RemotePreference        *string  `json:"remote_preference"`
	EnabledSearchSites      []string `json:"enabled_search_sites"`
	DiscoveryMaxPages       int      `json:"discovery_max_pages"`
}

type Payload struct {
	OK                        bool           `json:"ok"`
	EnvPath                   string         `json:"env_path"`
	ApplicantMDPath           string         `json:"applicant_md_path"`
	MissingRequiredFields     []string       `json:"missing_required_fields"`
	MissingOptionalFields     []string       `json:"missing_optional_fields"`
	MissingRequiredFiles      []string       `json:"missing_required_files"`
	Warnings                  []string       `json:"warnings"`
	GoogleSearchQueries       []search.Query `json:"google_search_queries"`
	Profile                   Profile        `json:"profile"`
	ApplicantMarkdownSections []string       `json:"applicant_markdown_sections"`
}

func NormalizeValue(raw string) *string {
	value := strings.TrimSpace(raw)
	if unknownTokens[strings.ToLower(value)] {
		return nil
	}
	return &value
}

func ParseEnvFile(path string) (map[string]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	values := map[string]string{}
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") || !strings.Contains(line, "=") {
			continue
		}
		parts := strings.SplitN(line, "=", 2)
		key, value := strings.TrimSpace(parts[0]), strings.TrimSpace(parts[1])
		if len(value) >= 2 && ((value[0] == '"' && value[len(value)-1] == '"') || (value[0] == '\'' && value[len(value)-1] == '\'')) {
			value = value[1 : len(value)-1]
		}
		values[key] = value
	}
	return values, scanner.Err()
}

func ParseBool(raw string) *bool {
	v := NormalizeValue(raw)
	if v == nil {
		return nil
	}
	switch strings.ToLower(*v) {
	case "true", "1", "yes", "y":
		value := true
		return &value
	case "false", "0", "no", "n":
		value := false
		return &value
	default:
		return nil
	}
}

func ParseCSV(raw string) []string {
	v := NormalizeValue(raw)
	if v == nil {
		return []string{}
	}
	parts := strings.Split(*v, ",")
	out := make([]string, 0, len(parts))
	for _, part := range parts {
		if item := strings.TrimSpace(part); item != "" {
			out = append(out, item)
		}
	}
	return out
}

func ParseSearchSites(raw string) []string {
	v := NormalizeValue(raw)
	if v == nil {
		return append([]string(nil), search.SupportedSites...)
	}
	out := []string{}
	seen := map[string]bool{}
	for _, part := range strings.Split(*v, ",") {
		canonical, ok := search.SiteAliases[strings.ToLower(strings.TrimSpace(part))]
		if ok && !seen[canonical] {
			seen[canonical] = true
			out = append(out, canonical)
		}
	}
	return out
}

func ParseDiscoveryMaxPages(raw string) (int, *string) {
	v := NormalizeValue(raw)
	if v == nil {
		return DefaultDiscoveryMaxPages, nil
	}
	n, err := strconv.Atoi(*v)
	if err != nil || n < 1 {
		message := fmt.Sprintf("APPLICANT_DISCOVERY_MAX_PAGES must be a positive integer. Using default %d.", DefaultDiscoveryMaxPages)
		return DefaultDiscoveryMaxPages, &message
	}
	return n, nil
}

func InvalidSearchSites(raw string) []string {
	v := NormalizeValue(raw)
	if v == nil {
		return []string{}
	}
	out := []string{}
	for _, part := range strings.Split(*v, ",") {
		item := strings.TrimSpace(part)
		if item == "" {
			continue
		}
		if _, ok := search.SiteAliases[strings.ToLower(item)]; !ok {
			out = append(out, item)
		}
	}
	return out
}

func ResolvePath(root, raw string) *string {
	v := NormalizeValue(raw)
	if v == nil {
		return nil
	}
	path := *v
	if !filepath.IsAbs(path) {
		path = filepath.Join(root, path)
	}
	path, err := filepath.Abs(path)
	if err != nil {
		return nil
	}
	return &path
}

func ParseApplicantMarkdown(path string) (map[string]string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	sections := map[string][]string{"document": {}}
	current := "document"
	for _, raw := range strings.Split(strings.ReplaceAll(string(data), "\r\n", "\n"), "\n") {
		line := strings.TrimRight(raw, "\r")
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "# ") || strings.HasPrefix(trimmed, "## ") {
			current = strings.ToLower(strings.TrimSpace(strings.TrimLeft(trimmed, "#")))
			if _, ok := sections[current]; !ok {
				sections[current] = []string{}
			}
			continue
		}
		sections[current] = append(sections[current], strings.TrimRight(line, " \t"))
	}
	out := map[string]string{}
	for key, lines := range sections {
		if value := strings.TrimSpace(strings.Join(lines, "\n")); value != "" {
			out[key] = value
		}
	}
	return out, nil
}

func Validate(root string) ValidationResult {
	root, _ = filepath.Abs(root)
	result := ValidationResult{
		EnvPath: filepath.Join(root, ".env"), ApplicantMDPath: filepath.Join(root, "applicant.md"),
		EnvValues: map[string]string{}, ApplicantSections: map[string]string{},
		MissingRequiredFields: []string{}, MissingOptionalFields: []string{}, MissingRequiredFiles: []string{}, Warnings: []string{},
	}
	values, err := ParseEnvFile(result.EnvPath)
	if err != nil {
		result.MissingRequiredFiles = append(result.MissingRequiredFiles, ".env")
	} else {
		result.EnvValues = values
		for _, key := range requiredEnvKeys {
			if NormalizeValue(values[key]) == nil {
				result.MissingRequiredFields = append(result.MissingRequiredFields, key)
			}
		}
		for _, key := range optionalEnvKeys {
			if NormalizeValue(values[key]) == nil {
				result.MissingOptionalFields = append(result.MissingOptionalFields, key)
			}
		}
		if path := ResolvePath(root, values["APPLICANT_RESUME_PATH"]); path == nil || !fileExists(*path) {
			result.MissingRequiredFiles = append(result.MissingRequiredFiles, "APPLICANT_RESUME_PATH")
		}
		if path := ResolvePath(root, values["APPLICANT_COVER_LETTER_PATH"]); path != nil && !fileExists(*path) {
			result.Warnings = append(result.Warnings, "APPLICANT_COVER_LETTER_PATH points to a missing file.")
		}
		if path := ResolvePath(root, values["APPLICANT_RESUME_TEMPLATE_PATH"]); path != nil && !fileExists(*path) {
			result.Warnings = append(result.Warnings, "APPLICANT_RESUME_TEMPLATE_PATH points to a missing file.")
		}
		if invalid := InvalidSearchSites(values["APPLICANT_ENABLED_SEARCH_SITES"]); len(invalid) > 0 {
			result.Warnings = append(result.Warnings, "APPLICANT_ENABLED_SEARCH_SITES includes unsupported values: "+strings.Join(invalid, ", ")+". Supported values: "+search.SupportedSitesText()+".")
		}
		if NormalizeValue(values["APPLICANT_ENABLED_SEARCH_SITES"]) != nil && len(ParseSearchSites(values["APPLICANT_ENABLED_SEARCH_SITES"])) == 0 {
			result.Warnings = append(result.Warnings, "APPLICANT_ENABLED_SEARCH_SITES does not enable any supported sites.")
		}
		if _, warning := ParseDiscoveryMaxPages(values["APPLICANT_DISCOVERY_MAX_PAGES"]); warning != nil {
			result.Warnings = append(result.Warnings, *warning)
		}
	}
	sections, err := ParseApplicantMarkdown(result.ApplicantMDPath)
	if err != nil {
		result.Warnings = append(result.Warnings, "applicant.md was not found.")
	} else {
		result.ApplicantSections = sections
		if len(sections) > 0 {
			if _, ok := sections["work authorization notes"]; !ok {
				result.Warnings = append(result.Warnings, "applicant.md is missing a 'Work Authorization Notes' section.")
			}
			if _, ok := sections["reusable highlights"]; !ok {
				result.Warnings = append(result.Warnings, "applicant.md is missing a 'Reusable Highlights' section.")
			}
		}
	}
	return result
}

func (r ValidationResult) OK() bool {
	return len(r.MissingRequiredFields) == 0 && len(r.MissingRequiredFiles) == 0
}

func (r ValidationResult) Payload() Payload {
	values := r.EnvValues
	root := filepath.Dir(r.EnvPath)
	pages, _ := ParseDiscoveryMaxPages(values["APPLICANT_DISCOVERY_MAX_PAGES"])
	sites := ParseSearchSites(values["APPLICANT_ENABLED_SEARCH_SITES"])
	roles := ParseCSV(values["APPLICANT_TARGET_ROLE_KEYWORDS"])
	sections := make([]string, 0, len(r.ApplicantSections))
	for key := range r.ApplicantSections {
		sections = append(sections, key)
	}
	sort.Strings(sections)
	return Payload{
		OK: r.OK(), EnvPath: r.EnvPath, ApplicantMDPath: r.ApplicantMDPath,
		MissingRequiredFields: r.MissingRequiredFields, MissingOptionalFields: r.MissingOptionalFields,
		MissingRequiredFiles: r.MissingRequiredFiles, Warnings: r.Warnings,
		GoogleSearchQueries: search.BuildGoogleQueries(sites, roles), ApplicantMarkdownSections: sections,
		Profile: Profile{
			FullName: NormalizeValue(values["APPLICANT_FULL_NAME"]), Email: NormalizeValue(values["APPLICANT_EMAIL"]),
			Phone: NormalizeValue(values["APPLICANT_PHONE"]), Location: NormalizeValue(values["APPLICANT_LOCATION"]),
			OpenToRelocation: ParseBool(values["APPLICANT_OPEN_TO_RELOCATION"]), LinkedInURL: NormalizeValue(values["APPLICANT_LINKEDIN_URL"]),
			GitHubURL: NormalizeValue(values["APPLICANT_GITHUB_URL"]), PortfolioURL: NormalizeValue(values["APPLICANT_PORTFOLIO_URL"]),
			ResumePath: ResolvePath(root, values["APPLICANT_RESUME_PATH"]), CoverLetterPath: ResolvePath(root, values["APPLICANT_COVER_LETTER_PATH"]),
			ResumeTemplatePath: ResolvePath(root, values["APPLICANT_RESUME_TEMPLATE_PATH"]), USWorkAuthorized: ParseBool(values["APPLICANT_US_WORK_AUTHORIZED"]),
			RequiresVisaSponsorship: ParseBool(values["APPLICANT_REQUIRES_VISA_SPONSORSHIP"]), CurrentVisaStatus: NormalizeValue(values["APPLICANT_CURRENT_VISA_STATUS"]),
			TargetRoleKeywords: roles, AllowedLocations: ParseCSV(values["APPLICANT_ALLOWED_LOCATIONS"]), RemotePreference: NormalizeValue(values["APPLICANT_REMOTE_PREFERENCE"]),
			EnabledSearchSites: sites, DiscoveryMaxPages: pages,
		},
	}
}

func fileExists(path string) bool { info, err := os.Stat(path); return err == nil && !info.IsDir() }
