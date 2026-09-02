package workflow

import (
	"fmt"
	"strings"
)

func validateQuery(payload map[string]any) error {
	if err := requireFields(payload, "outcome", "results", "next_page", "query_error"); err != nil {
		return err
	}
	outcome := text(payload["outcome"])
	if outcome != "results_page" && outcome != "exhausted" && outcome != "query_failed" {
		return fmt.Errorf("unsupported query worker outcome: %q", outcome)
	}
	results, ok := payload["results"].([]any)
	if !ok {
		return fmt.Errorf("query worker output must include a results list")
	}
	for _, item := range results {
		if err := validateResultItem(item); err != nil {
			return err
		}
	}
	if payload["next_page"] != nil {
		if _, ok := payload["next_page"].(map[string]any); !ok {
			return fmt.Errorf("field 'next_page' must be an object or null")
		}
	}
	switch outcome {
	case "results_page":
		if len(results) == 0 {
			return fmt.Errorf("results-page outcome must include at least one visible result")
		}
		if payload["query_error"] != nil {
			return fmt.Errorf("field 'query_error' must be null")
		}
	case "exhausted":
		if len(results) > 0 || payload["next_page"] != nil || payload["query_error"] != nil {
			return fmt.Errorf("exhausted outcome has inconsistent fields")
		}
	case "query_failed":
		if len(results) > 0 || payload["next_page"] != nil || strings.TrimSpace(text(payload["query_error"])) == "" {
			return fmt.Errorf("query-failed outcome has inconsistent fields")
		}
	}
	return nil
}
func validateResolve(payload map[string]any) error {
	if err := requireFields(payload, "outcome", "job", "child_results", "skip_reason", "error_message"); err != nil {
		return err
	}
	outcome := text(payload["outcome"])
	if outcome != "resolved_job" && outcome != "expanded" && outcome != "skip_result" && outcome != "result_failed" {
		return fmt.Errorf("unsupported resolve worker outcome: %q", outcome)
	}
	children, ok := payload["child_results"].([]any)
	if !ok {
		return fmt.Errorf("resolve worker output must include a child_results list")
	}
	for _, item := range children {
		if err := validateResultItem(item); err != nil {
			return err
		}
	}
	switch outcome {
	case "resolved_job":
		job, ok := payload["job"].(map[string]any)
		if !ok {
			return fmt.Errorf("resolved job must be an object")
		}
		if err := requireFields(job, "raw_url", "canonical_url", "source", "title", "company", "location", "posted_at", "description_text", "page_url"); err != nil {
			return err
		}
		if strings.TrimSpace(text(job["raw_url"])) == "" || strings.TrimSpace(text(job["source"])) == "" || strings.TrimSpace(text(job["page_url"])) == "" {
			return fmt.Errorf("resolved job required fields must be non-empty")
		}
		if len(children) > 0 || payload["skip_reason"] != nil || payload["error_message"] != nil {
			return fmt.Errorf("resolved-job outcome has inconsistent fields")
		}
	case "expanded":
		if payload["job"] != nil || len(children) == 0 || payload["skip_reason"] != nil || payload["error_message"] != nil {
			return fmt.Errorf("expanded outcome has inconsistent fields")
		}
	case "skip_result":
		if payload["job"] != nil || len(children) > 0 || strings.TrimSpace(text(payload["skip_reason"])) == "" || payload["error_message"] != nil {
			return fmt.Errorf("skip-result outcome has inconsistent fields")
		}
	case "result_failed":
		if payload["job"] != nil || len(children) > 0 || payload["skip_reason"] != nil || strings.TrimSpace(text(payload["error_message"])) == "" {
			return fmt.Errorf("result-failed outcome has inconsistent fields")
		}
	}
	return nil
}
func validateApply(payload map[string]any) error {
	if err := requireFields(payload, "application_status", "confirmation_text", "confirmation_url", "error_message", "findings"); err != nil {
		return err
	}
	status := text(payload["application_status"])
	allowed := map[string]bool{"submitted": true, "failed": true, "blocked": true, "incomplete": true, "duplicate_skipped": true}
	if !allowed[status] {
		return fmt.Errorf("unsupported application status: %q", status)
	}
	findings, ok := payload["findings"].([]any)
	if !ok {
		return fmt.Errorf("apply worker output must include a findings list")
	}
	if (status == "failed" || status == "blocked" || status == "incomplete") && len(findings) == 0 {
		return fmt.Errorf("application status '%s' requires at least one structured finding", status)
	}
	for _, item := range findings {
		finding, ok := item.(map[string]any)
		if !ok {
			return fmt.Errorf("each finding must be an object")
		}
		if err := requireFields(finding, "stage", "category", "summary", "detail", "page_url"); err != nil {
			return err
		}
		if strings.TrimSpace(text(finding["stage"])) == "" || strings.TrimSpace(text(finding["category"])) == "" || strings.TrimSpace(text(finding["summary"])) == "" {
			return fmt.Errorf("finding required fields must be non-empty")
		}
	}
	return nil
}
func validateResume(payload map[string]any) error {
	for _, field := range []string{"summary", "skills", "bullet_blocks"} {
		if _, ok := payload[field]; !ok {
			return fmt.Errorf("resume customization worker output is missing '%s'", field)
		}
	}
	for _, field := range []string{"summary", "skills"} {
		values, ok := payload[field].([]any)
		if !ok || len(values) == 0 {
			return fmt.Errorf("field '%s' must be a non-empty list", field)
		}
		for _, value := range values {
			if strings.TrimSpace(text(value)) == "" {
				return fmt.Errorf("%s items must be non-empty strings", field)
			}
		}
	}
	if _, ok := payload["bullet_blocks"].([]any); !ok {
		return fmt.Errorf("field 'bullet_blocks' must be a list")
	}
	return nil
}
func validateResultItem(value any) error {
	item, ok := value.(map[string]any)
	if !ok {
		return fmt.Errorf("each result item must be an object")
	}
	if err := requireFields(item, "url", "title", "snippet", "visible_date", "page_number", "rank"); err != nil {
		return err
	}
	if strings.TrimSpace(text(item["url"])) == "" {
		return fmt.Errorf("field 'url' must be a non-empty string")
	}
	if _, ok := item["page_number"].(float64); !ok {
		return fmt.Errorf("field 'page_number' must be an integer")
	}
	if _, ok := item["rank"].(float64); !ok {
		return fmt.Errorf("field 'rank' must be an integer")
	}
	return nil
}
func requireFields(payload map[string]any, fields ...string) error {
	for _, field := range fields {
		if _, ok := payload[field]; !ok {
			return fmt.Errorf("worker output is missing '%s'", field)
		}
	}
	return nil
}
