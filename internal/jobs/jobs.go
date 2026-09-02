package jobs

import (
	"crypto/sha256"
	"encoding/hex"
	"net/mail"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/ramesh/codex-job-apply/internal/search"
)

var trackingKeys = map[string]bool{
	"fbclid": true, "gclid": true, "gad_source": true, "gh_src": true,
	"gh_jid": true, "mc_cid": true, "mc_eid": true, "ref": true,
	"referrer": true, "source": true, "trk": true,
}

var DefaultRoleKeywords = []string{
	"software engineer", "software developer", "backend engineer", "frontend engineer",
	"full stack engineer", "full-stack engineer", "platform engineer",
}

var stateNames = strings.Fields("alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho illinois indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota mississippi missouri montana nebraska nevada ohio oklahoma oregon pennsylvania rhode-island south-carolina south-dakota tennessee texas utah vermont virginia washington west-virginia wisconsin wyoming district-of-columbia new-hampshire new-jersey new-mexico new-york north-carolina north-dakota")
var stateAbbreviations = strings.Fields("al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv nh nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy dc")

type FreshnessCheck struct {
	RawValue           *string `json:"raw_value"`
	IsRecent           bool    `json:"is_recent"`
	IsVerifiable       bool    `json:"is_verifiable"`
	NormalizedPostedAt *string `json:"normalized_posted_at"`
	Reason             *string `json:"reason"`
}

func UTCNow() time.Time                  { return time.Now().UTC() }
func FormatTimestamp(t time.Time) string { return t.UTC().Format("2006-01-02T15:04:05Z") }
func ptr(v string) *string               { return &v }

func CanonicalizeURL(raw, canonical string) (string, error) {
	source := strings.TrimSpace(raw)
	if strings.TrimSpace(canonical) != "" {
		source = strings.TrimSpace(canonical)
	}
	if !strings.Contains(source, "://") {
		source = "https://" + source
	}
	u, err := url.Parse(source)
	if err != nil {
		return "", err
	}
	u.Scheme = strings.ToLower(u.Scheme)
	u.Host = strings.ToLower(u.Host)
	hostname := strings.ToLower(u.Hostname())
	port := u.Port()
	if port == "" || (u.Scheme == "http" && port == "80") || (u.Scheme == "https" && port == "443") {
		u.Host = hostname
	} else {
		u.Host = hostname + ":" + port
	}
	if u.Path == "" {
		u.Path = "/"
	} else if u.Path != "/" {
		u.Path = strings.TrimRight(u.Path, "/")
	}
	u.Fragment = ""
	values := u.Query()
	for key := range values {
		lower := strings.ToLower(key)
		if strings.HasPrefix(lower, "utm_") || trackingKeys[lower] {
			values.Del(key)
		}
	}
	// Python sorts key/value pairs. url.Values.Encode does the same for normal cases.
	u.RawQuery = values.Encode()
	return u.String(), nil
}

func BuildJobKey(canonical string) string {
	digest := sha256.Sum256([]byte(canonical))
	return "url-" + hex.EncodeToString(digest[:])[:24]
}

func InferSource(rawURL string) string {
	u, err := url.Parse(rawURL)
	if err != nil {
		return "unknown"
	}
	return search.InferSource(u.Hostname())
}

func TitleMatchesRole(title string, roles []string) bool {
	if title == "" {
		return false
	}
	if len(roles) == 0 {
		roles = DefaultRoleKeywords
	}
	lower := strings.ToLower(title)
	normalized := strings.NewReplacer("-", " ", "/", " ").Replace(lower)
	for _, role := range roles {
		candidate := strings.ToLower(role)
		if strings.Contains(lower, candidate) || strings.Contains(normalized, candidate) {
			return true
		}
	}
	return false
}

func LocationMatchesUS(location string, allowed []string) bool {
	if location == "" {
		return false
	}
	lower := strings.ToLower(location)
	for _, item := range allowed {
		if strings.Contains(lower, strings.ToLower(item)) {
			return true
		}
	}
	if strings.Contains(lower, "united states") || strings.Contains(lower, "usa") || strings.Contains(lower, "u.s.") || (strings.Contains(lower, "remote") && (strings.Contains(lower, "us") || strings.Contains(lower, "united states"))) {
		return true
	}
	for _, state := range stateNames {
		if strings.Contains(lower, strings.ReplaceAll(state, "-", " ")) {
			return true
		}
	}
	tokens := strings.FieldsFunc(lower, func(r rune) bool { return strings.ContainsRune(" /-.,()[]", r) })
	for _, token := range tokens {
		for _, abbreviation := range stateAbbreviations {
			if token == abbreviation {
				return true
			}
		}
	}
	return false
}

func EvaluatePostedAt(raw *string, now time.Time) FreshnessCheck {
	if now.IsZero() {
		now = UTCNow()
	}
	if raw == nil || strings.TrimSpace(*raw) == "" {
		return FreshnessCheck{Reason: ptr("missing_posted_date")}
	}
	value := strings.TrimSpace(*raw)
	lower := strings.ToLower(value)
	if lower == "today" || lower == "just now" {
		timestamp := FormatTimestamp(now)
		return FreshnessCheck{RawValue: ptr(value), IsRecent: true, IsVerifiable: true, NormalizedPostedAt: &timestamp, Reason: ptr("relative_today")}
	}
	if lower == "yesterday" {
		return FreshnessCheck{RawValue: ptr(value), Reason: ptr("date_is_only_yesterday")}
	}
	if parsed, ok := parseRelative(value, now); ok {
		delta := now.Sub(parsed)
		timestamp := FormatTimestamp(parsed)
		return FreshnessCheck{RawValue: ptr(value), IsRecent: delta <= 24*time.Hour, IsVerifiable: true, NormalizedPostedAt: &timestamp, Reason: ptr("relative_time")}
	}
	if parsed, ok := parseDateTime(value, now.Location()); ok {
		delta := now.Sub(parsed)
		timestamp := FormatTimestamp(parsed)
		return FreshnessCheck{RawValue: ptr(value), IsRecent: delta >= 0 && delta <= 24*time.Hour, IsVerifiable: true, NormalizedPostedAt: &timestamp, Reason: ptr("absolute_datetime")}
	}
	if parsed, ok := parseDateOnly(value, now.Location()); ok {
		y, m, d := now.Date()
		py, pm, pd := parsed.Date()
		if y == py && m == pm && d == pd {
			timestamp := FormatTimestamp(time.Date(py, pm, pd, 0, 0, 0, 0, now.Location()))
			return FreshnessCheck{RawValue: ptr(value), IsRecent: true, IsVerifiable: true, NormalizedPostedAt: &timestamp, Reason: ptr("same_day_date_only")}
		}
		return FreshnessCheck{RawValue: ptr(value), Reason: ptr("date_only_is_not_same_day")}
	}
	return FreshnessCheck{RawValue: ptr(value), Reason: ptr("unrecognized_posted_date")}
}

func parseRelative(raw string, now time.Time) (time.Time, bool) {
	tokens := strings.Fields(strings.ToLower(raw))
	if len(tokens) < 2 || !contains(tokens, "ago") {
		return time.Time{}, false
	}
	quantity, err := strconv.ParseFloat(tokens[0], 64)
	if err != nil {
		return time.Time{}, false
	}
	var duration time.Duration
	switch {
	case strings.HasPrefix(tokens[1], "hour"):
		duration = time.Duration(quantity * float64(time.Hour))
	case strings.HasPrefix(tokens[1], "day"):
		duration = time.Duration(quantity * float64(24*time.Hour))
	case strings.HasPrefix(tokens[1], "minute"):
		duration = time.Duration(quantity * float64(time.Minute))
	default:
		return time.Time{}, false
	}
	return now.Add(-duration), true
}

func parseDateTime(raw string, location *time.Location) (time.Time, bool) {
	layouts := []string{
		time.RFC3339Nano, time.RFC3339, "2006-01-02 15:04", "2006-01-02 15:04:05",
		"2006-01-02T15:04", "2006-01-02T15:04:05", "2006-01-02T15:04:05.999999",
		"2006-01-02 03:04 PM", "Jan 02, 2006 03:04 PM", "January 02, 2006 03:04 PM",
	}
	for _, layout := range layouts {
		var parsed time.Time
		var err error
		if strings.Contains(layout, "Z07") {
			parsed, err = time.Parse(layout, raw)
		} else {
			parsed, err = time.ParseInLocation(layout, raw, location)
		}
		if err == nil {
			return parsed.UTC(), true
		}
	}
	if date, err := mail.ParseDate(raw); err == nil {
		return date.UTC(), true
	}
	return time.Time{}, false
}

func parseDateOnly(raw string, location *time.Location) (time.Time, bool) {
	for _, layout := range []string{"2006-01-02", "01/02/2006", "Jan 02, 2006", "January 02, 2006", "02 Jan 2006", "02 January 2006"} {
		if parsed, err := time.ParseInLocation(layout, raw, location); err == nil {
			return parsed, true
		}
	}
	return time.Time{}, false
}

func contains(values []string, value string) bool {
	i := sort.SearchStrings(append([]string(nil), values...), value)
	_ = i
	for _, item := range values {
		if item == value {
			return true
		}
	}
	return false
}
