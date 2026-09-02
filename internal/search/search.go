package search

import (
	"fmt"
	"strings"
)

var SupportedSites = []string{
	"jobright", "greenhouse", "ashby", "workable", "jobvite", "jazz", "adp",
	"lever", "bamboohr", "paylocity", "smartrecruiters", "gem", "dover",
}

var SiteDomains = map[string]string{
	"jobright": "jobright.ai", "greenhouse": "boards.greenhouse.io",
	"ashby": "jobs.ashbyhq.com", "workable": "apply.workable.com",
	"jobvite": "jobs.jobvite.com", "jazz": "app.jazz.co",
	"adp": "recruiting.adp.com", "lever": "jobs.lever.co",
	"bamboohr": "bamboohr.com", "paylocity": "recruiting.paylocity.com",
	"smartrecruiters": "jobs.smartrecruiters.com", "gem": "jobs.gem.com",
	"dover": "app.dover.com",
}

var SiteAliases = map[string]string{
	"jobright": "jobright", "jobright.ai": "jobright",
	"greenhouse": "greenhouse", "boards.greenhouse.io": "greenhouse", "job-boards.greenhouse.io": "greenhouse",
	"ashby": "ashby", "ashbyhq": "ashby", "jobs.ashbyhq.com": "ashby",
	"workable": "workable", "apply.workable.com": "workable",
	"jobvite": "jobvite", "jobs.jobvite.com": "jobvite",
	"jazz": "jazz", "app.jazz.co": "jazz",
	"adp": "adp", "recruiting.adp.com": "adp",
	"lever": "lever", "jobs.lever.co": "lever",
	"bamboohr": "bamboohr", "bamboohr.com": "bamboohr",
	"paylocity": "paylocity", "recruiting.paylocity.com": "paylocity",
	"smartrecruiters": "smartrecruiters", "jobs.smartrecruiters.com": "smartrecruiters",
	"gem": "gem", "jobs.gem.com": "gem",
	"dover": "dover", "app.dover.com": "dover",
}

var hostMatches = []struct {
	Source string
	Parts  []string
}{
	{"jobright", []string{"jobright.ai"}},
	{"greenhouse", []string{"greenhouse.io", "greenhouse"}},
	{"ashby", []string{"ashbyhq.com", "ashbyhq"}},
	{"workable", []string{"apply.workable.com"}},
	{"jobvite", []string{"jobs.jobvite.com"}},
	{"jazz", []string{"app.jazz.co"}},
	{"adp", []string{"recruiting.adp.com"}},
	{"lever", []string{"jobs.lever.co"}},
	{"bamboohr", []string{"bamboohr.com"}},
	{"paylocity", []string{"recruiting.paylocity.com"}},
	{"smartrecruiters", []string{"jobs.smartrecruiters.com"}},
	{"gem", []string{"jobs.gem.com"}},
	{"dover", []string{"app.dover.com"}},
}

const googleLocationHint = `("united states" OR "remote")`

type Query struct {
	SourceKey string `json:"source_key"`
	Domain    string `json:"domain"`
	Query     string `json:"query"`
}

func SupportedSitesText() string { return strings.Join(SupportedSites, ", ") }

func NormalizeTerms(terms []string) []string {
	seen := map[string]bool{}
	out := make([]string, 0, len(terms))
	for _, raw := range terms {
		term := strings.TrimSpace(raw)
		key := strings.ToLower(term)
		if term == "" || seen[key] {
			continue
		}
		seen[key] = true
		out = append(out, term)
	}
	return out
}

func BuildGoogleQuery(site string, roles []string) string {
	domain := SiteDomains[site]
	roles = NormalizeTerms(roles)
	if len(roles) == 0 {
		return fmt.Sprintf("site:%s %s", domain, googleLocationHint)
	}
	quoted := make([]string, 0, len(roles))
	for _, role := range roles {
		quoted = append(quoted, `"`+strings.ReplaceAll(role, `"`, `\"`)+`"`)
	}
	return fmt.Sprintf("site:%s (%s) %s", domain, strings.Join(quoted, " OR "), googleLocationHint)
}

func BuildGoogleQueries(sites, roles []string) []Query {
	out := make([]Query, 0, len(sites))
	for _, site := range sites {
		domain, ok := SiteDomains[site]
		if !ok {
			continue
		}
		out = append(out, Query{SourceKey: site, Domain: domain, Query: BuildGoogleQuery(site, roles)})
	}
	return out
}

func InferSource(host string) string {
	normalized := strings.ToLower(host)
	for _, item := range hostMatches {
		for _, part := range item.Parts {
			if strings.Contains(normalized, part) {
				return item.Source
			}
		}
	}
	if normalized == "" {
		return "unknown"
	}
	return normalized
}
