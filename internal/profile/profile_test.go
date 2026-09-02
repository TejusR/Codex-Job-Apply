package profile

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/ramesh/codex-job-apply/internal/search"
)

func profileRoot(t *testing.T, extra string) string {
	t.Helper()
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, "resume.pdf"), []byte("stub"), 0o600)
	env := `APPLICANT_FULL_NAME=Tejus Ramesh
APPLICANT_EMAIL=rameshtejus@gmail.com
APPLICANT_PHONE=(480)-810-7760
APPLICANT_LOCATION=Tempe, AZ
APPLICANT_RESUME_PATH=resume.pdf
APPLICANT_US_WORK_AUTHORIZED=true
APPLICANT_REQUIRES_VISA_SPONSORSHIP=false
` + extra
	os.WriteFile(filepath.Join(root, ".env"), []byte(env), 0o600)
	os.WriteFile(filepath.Join(root, "applicant.md"), []byte("# Applicant Details\n\n## Work Authorization Notes\nProvided\n\n## Reusable Highlights\nProvided\n"), 0o600)
	return root
}
func TestDiscoveryPageConfiguration(t *testing.T) {
	for _, tc := range []struct {
		name, extra string
		want        int
		warning     bool
	}{{"default", "", 5, false}, {"valid", "APPLICANT_DISCOVERY_MAX_PAGES=7\n", 7, false}, {"invalid", "APPLICANT_DISCOVERY_MAX_PAGES=0\n", 5, true}} {
		t.Run(tc.name, func(t *testing.T) {
			result := Validate(profileRoot(t, tc.extra))
			if result.Payload().Profile.DiscoveryMaxPages != tc.want {
				t.Fatalf("got %d", result.Payload().Profile.DiscoveryMaxPages)
			}
			found := false
			for _, w := range result.Warnings {
				found = found || strings.Contains(w, "must be a positive integer")
			}
			if found != tc.warning {
				t.Fatalf("warnings: %#v", result.Warnings)
			}
		})
	}
}
func TestResumeTemplateValidation(t *testing.T) {
	root := profileRoot(t, "APPLICANT_RESUME_TEMPLATE_PATH=resume-template.tex\n")
	os.WriteFile(filepath.Join(root, "resume-template.tex"), []byte("% BEGIN AUTO_SUMMARY\n% END AUTO_SUMMARY\n% BEGIN AUTO_SKILLS\n% END AUTO_SKILLS\n% BEGIN AUTO_BULLETS:example\n% END AUTO_BULLETS:example\n"), 0o600)
	result := Validate(root)
	if got := result.Payload().Profile.ResumeTemplatePath; got == nil || *got != filepath.Join(root, "resume-template.tex") {
		t.Fatalf("unexpected template: %v", got)
	}
	missing := profileRoot(t, "APPLICANT_RESUME_TEMPLATE_PATH=missing.tex\n")
	result = Validate(missing)
	if !result.OK() || !containsWarning(result.Warnings, "APPLICANT_RESUME_TEMPLATE_PATH points to a missing file.") {
		t.Fatalf("unexpected result: %#v", result)
	}
}
func TestSearchSitesAndQueries(t *testing.T) {
	all := Validate(profileRoot(t, ""))
	if !reflect.DeepEqual(all.Payload().Profile.EnabledSearchSites, search.SupportedSites) {
		t.Fatalf("got %#v", all.Payload().Profile.EnabledSearchSites)
	}
	root := profileRoot(t, "APPLICANT_ENABLED_SEARCH_SITES=ashby, jobs.lever.co, app.dover.com, monster\nAPPLICANT_TARGET_ROLE_KEYWORDS=software engineer, backend engineer\n")
	result := Validate(root)
	if !reflect.DeepEqual(result.Payload().Profile.EnabledSearchSites, []string{"ashby", "lever", "dover"}) {
		t.Fatalf("got %#v", result.Payload().Profile.EnabledSearchSites)
	}
	if !containsWarning(result.Warnings, "includes unsupported values: monster") {
		t.Fatalf("warnings: %#v", result.Warnings)
	}
	queries := result.Payload().GoogleSearchQueries
	if len(queries) != 3 || queries[1].SourceKey != "lever" || !strings.Contains(queries[1].Query, `"software engineer" OR "backend engineer"`) {
		t.Fatalf("queries: %#v", queries)
	}
}
func TestMissingInputs(t *testing.T) {
	root := profileRoot(t, "APPLICANT_US_WORK_AUTHORIZED=\nAPPLICANT_REQUIRES_VISA_SPONSORSHIP=\n")
	result := Validate(root)
	if result.OK() || !contains(result.MissingRequiredFields, "APPLICANT_US_WORK_AUTHORIZED") || !contains(result.MissingRequiredFields, "APPLICANT_REQUIRES_VISA_SPONSORSHIP") {
		t.Fatalf("unexpected result: %#v", result)
	}
	missing := Validate(t.TempDir())
	if !contains(missing.MissingRequiredFiles, ".env") || !containsWarning(missing.Warnings, "applicant.md was not found.") {
		t.Fatalf("unexpected missing result: %#v", missing)
	}
}
func contains(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}
func containsWarning(values []string, want string) bool {
	for _, value := range values {
		if strings.Contains(value, want) {
			return true
		}
	}
	return false
}
