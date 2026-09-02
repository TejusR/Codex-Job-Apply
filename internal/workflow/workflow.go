package workflow

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/ramesh/codex-job-apply/internal/codex"
	"github.com/ramesh/codex-job-apply/internal/profile"
	resumelib "github.com/ramesh/codex-job-apply/internal/resume"
	"github.com/ramesh/codex-job-apply/internal/store"
)

type Config struct {
	Store                                                                                    *store.Store
	RepoRoot, CodexBin, CodexProfile                                                         string
	QueryTimeoutSeconds, JobTimeoutSeconds, MaxWorkerRetries, DiscoveryWorkers, ApplyWorkers int
}
type Service struct {
	config Config
	runner *codex.Runner
}

func New(config Config) *Service {
	if config.CodexBin == "" {
		config.CodexBin = codex.DefaultCodexBin()
	}
	if config.ApplyWorkers < 1 {
		config.ApplyWorkers = 5
	}
	if config.MaxWorkerRetries < 0 {
		config.MaxWorkerRetries = 0
	}
	root, _ := filepath.Abs(config.RepoRoot)
	config.RepoRoot = root
	return &Service{config: config, runner: &codex.Runner{Store: config.Store, RepoRoot: root, CodexBin: config.CodexBin, CodexProfile: config.CodexProfile, MaxRetries: config.MaxWorkerRetries}}
}

func (s *Service) Run(ctx context.Context, runID *int) (store.Row, error) {
	validation := profile.Validate(s.config.RepoRoot)
	if !validation.OK() {
		return nil, fmt.Errorf("profile validation failed. Missing required items: %s", strings.Join(append(append([]string{}, validation.MissingRequiredFields...), validation.MissingRequiredFiles...), ", "))
	}
	var id int
	if runID == nil {
		prepared, err := s.config.Store.PrepareRun(ctx, s.config.RepoRoot)
		if err != nil {
			return nil, err
		}
		id = toInt(prepared["run_id"])
	} else {
		id = *runID
		if _, err := s.config.Store.WorkflowStatus(ctx, id); err != nil {
			return nil, err
		}
		s.config.Store.RequeueStaleApplyingJobs(ctx, &id)
		s.config.Store.RequeueProcessingSearchResults(ctx, id)
		s.config.Store.ResetWorkerSessions(ctx, id)
	}
	queries, err := s.config.Store.ListRunQueries(ctx, id)
	if err != nil {
		return nil, err
	}
	active := []store.Row{}
	for _, q := range queries {
		status := text(q["status"])
		if status == "pending" || status == "in_progress" {
			active = append(active, q)
		}
	}
	discoveryWorkers := s.config.DiscoveryWorkers
	if discoveryWorkers <= 0 || discoveryWorkers > len(active) {
		discoveryWorkers = len(active)
	}
	done := make(chan struct{})
	errCh := make(chan error, len(active)+s.config.ApplyWorkers)
	var applyWG sync.WaitGroup
	for i := 1; i <= s.config.ApplyWorkers; i++ {
		applyWG.Add(1)
		go func(slot string) {
			defer applyWG.Done()
			if err := s.applyLoop(ctx, id, slot, validation, done); err != nil {
				errCh <- err
			}
		}(fmt.Sprintf("apply-%d", i))
	}
	sem := make(chan struct{}, max(1, discoveryWorkers))
	var discoveryWG sync.WaitGroup
	for _, query := range active {
		source := text(query["source_key"])
		discoveryWG.Add(1)
		go func() {
			defer discoveryWG.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			if err := s.discoveryLoop(ctx, id, source, validation); err != nil {
				errCh <- err
			}
		}()
	}
	discoveryWG.Wait()
	close(done)
	applyWG.Wait()
	close(errCh)
	for err := range errCh {
		if err != nil {
			return nil, err
		}
	}
	return s.config.Store.FinishRun(ctx, id, false)
}

func (s *Service) DiscoverOne(ctx context.Context, runID int, source string) (map[string]any, error) {
	validation := profile.Validate(s.config.RepoRoot)
	if !validation.OK() {
		return nil, errors.New("profile validation failed")
	}
	return s.queryTurn(ctx, runID, source, validation)
}
func (s *Service) ApplyOne(ctx context.Context, runID int, jobKey string) (store.Row, error) {
	validation := profile.Validate(s.config.RepoRoot)
	if !validation.OK() {
		return nil, errors.New("profile validation failed")
	}
	job, err := s.config.Store.GetJob(ctx, jobKey)
	if err != nil {
		return nil, err
	}
	if job == nil {
		return nil, fmt.Errorf("job %s does not exist", jobKey)
	}
	if text(job["status"]) == "ready_to_apply" {
		job, err = s.config.Store.MarkJobApplying(ctx, jobKey)
		if err != nil {
			return nil, err
		}
	}
	return s.applyExisting(ctx, runID, "adhoc-"+codex.SafeFilename(jobKey), job, validation)
}

func (s *Service) discoveryLoop(ctx context.Context, runID int, source string, validation profile.ValidationResult) error {
	s.config.Store.EnsureWorkerSession(ctx, runID, "discovery", source)
	for {
		query, err := s.config.Store.GetQuery(ctx, runID, source)
		if err != nil {
			return err
		}
		if query == nil || text(query["status"]) == "completed" || text(query["status"]) == "failed" {
			return nil
		}
		if _, err = s.queryTurn(ctx, runID, source, validation); err != nil {
			return err
		}
	}
}
func (s *Service) queryTurn(ctx context.Context, runID int, source string, validation profile.ValidationResult) (map[string]any, error) {
	query, err := s.config.Store.GetQuery(ctx, runID, source)
	if err != nil || query == nil {
		if err == nil {
			err = fmt.Errorf("run %d does not have a search query for source '%s'", runID, source)
		}
		return nil, err
	}
	pages := validation.Payload().Profile.DiscoveryMaxPages
	currentPage := 1
	if cursor := decodeMap(query["cursor_json"]); cursor != nil {
		if page := toInt(cursor["page_number"]); page >= 1 {
			currentPage = page
		}
	}
	if currentPage > pages {
		seen, ingested := toInt(query["results_seen"]), toInt(query["jobs_ingested"])
		s.config.Store.UpdateQuery(ctx, runID, source, "completed", &seen, &ingested, nil, nil)
		return map[string]any{"outcome": "exhausted", "results": []any{}, "next_page": nil, "query_error": nil}, nil
	}
	seenCount, ingestedCount := toInt(query["results_seen"]), toInt(query["jobs_ingested"])
	cursorText := optionalText(query["cursor_json"])
	s.config.Store.CheckpointQuery(ctx, runID, source, &seenCount, &ingestedCount, cursorText)
	query, _ = s.config.Store.GetQuery(ctx, runID, source)
	seenURLs, _ := s.config.Store.ListRunSeenURLs(ctx, runID)
	contextValue := map[string]any{"repo_root": s.config.RepoRoot, "docs": docs(s.config.RepoRoot, true), "profile": payloadMap(validation.Payload().Profile), "query": map[string]any{"run_id": query["run_id"], "source_key": query["source_key"], "domain": query["domain"], "query_text": query["query_text"], "status": query["status"], "results_seen": query["results_seen"], "jobs_ingested": query["jobs_ingested"], "cursor": decodeMap(query["cursor_json"])}, "current_run_seen_urls": seenURLs}
	prompt, err := s.prompt("CODEX_QUERY_WORKER_PROMPT.md", contextValue)
	if err != nil {
		return nil, err
	}
	invocation, err := s.runner.Invoke(ctx, codex.Request{RunID: runID, SessionWorkerType: "discovery", SlotKey: source, WorkerType: "query", TargetKey: source, SchemaPath: s.promptPath("CODEX_QUERY_WORKER_SCHEMA.json"), Prompt: prompt, TimeoutSeconds: s.config.QueryTimeoutSeconds, Validator: validateQuery})
	if err != nil {
		message := err.Error()
		s.config.Store.UpdateQuery(ctx, runID, source, "failed", &seenCount, &ingestedCount, cursorText, &message)
		return map[string]any{"outcome": "query_failed", "results": []any{}, "next_page": nil, "query_error": message}, nil
	}
	payload := invocation.Payload
	outcome := text(payload["outcome"])
	switch outcome {
	case "results_page":
		items := resultInputs(payload["results"])
		summary, err := s.config.Store.InsertSearchResults(ctx, runID, source, nil, "google_result", items)
		if err != nil {
			return nil, err
		}
		seenCount += len(items)
		next := decodeMap(payload["next_page"])
		if currentPage >= pages || next == nil || toInt(next["page_number"]) < 1 || toInt(next["page_number"]) > pages {
			next = nil
			s.config.Store.UpdateQuery(ctx, runID, source, "completed", &seenCount, &ingestedCount, nil, nil)
		} else {
			encoded := store.EncodeJSON(next)
			s.config.Store.CheckpointQuery(ctx, runID, source, &seenCount, &ingestedCount, &encoded)
		}
		payload["next_page"] = next
		payload["inserted_count"] = summary["inserted"]
	case "exhausted":
		s.config.Store.UpdateQuery(ctx, runID, source, "completed", &seenCount, &ingestedCount, nil, nil)
	case "query_failed":
		message := text(payload["query_error"])
		s.config.Store.UpdateQuery(ctx, runID, source, "failed", &seenCount, &ingestedCount, cursorText, &message)
	}
	return payload, nil
}

func (s *Service) applyLoop(ctx context.Context, runID int, slot string, validation profile.ValidationResult, done <-chan struct{}) error {
	s.config.Store.EnsureWorkerSession(ctx, runID, "apply", slot)
	for {
		job, err := s.config.Store.NextJob(ctx, true)
		if err != nil {
			return err
		}
		if job != nil {
			if _, err = s.applyExisting(ctx, runID, slot, job, validation); err != nil {
				return err
			}
			continue
		}
		result, err := s.config.Store.ClaimSearchResult(ctx, runID, slot)
		if err != nil {
			return err
		}
		if result != nil {
			if err = s.processSearchResult(ctx, runID, slot, result, validation); err != nil {
				return err
			}
			continue
		}
		status, err := s.config.Store.WorkflowStatus(ctx, runID)
		if err != nil {
			return err
		}
		select {
		case <-done:
			if toInt(status["ready_jobs"]) == 0 && toInt(status["applying_jobs"]) == 0 && toInt(status["search_results_pending"]) == 0 && toInt(status["search_results_processing"]) == 0 {
				s.config.Store.UpdateWorkerSession(ctx, runID, "apply", slot, "idle", nil, nil)
				return nil
			}
		default:
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(200 * time.Millisecond):
		}
	}
}

func (s *Service) processSearchResult(ctx context.Context, runID int, slot string, result store.Row, validation profile.ValidationResult) error {
	id := toInt(result["id"])
	source := text(result["source_key"])
	notes, _ := profile.ParseApplicantMarkdown(filepath.Join(s.config.RepoRoot, "applicant.md"))
	contextValue := map[string]any{"repo_root": s.config.RepoRoot, "docs": docs(s.config.RepoRoot, true), "profile": payloadMap(validation.Payload().Profile), "search_result": result, "applicant_sections": notes}
	prompt, err := s.prompt("CODEX_RESOLVE_WORKER_PROMPT.md", contextValue)
	if err != nil {
		return err
	}
	invocation, err := s.runner.Invoke(ctx, codex.Request{RunID: runID, SessionWorkerType: "apply", SlotKey: slot, WorkerType: "resolve", TargetKey: fmt.Sprintf("result-%d", id), SchemaPath: s.promptPath("CODEX_RESOLVE_WORKER_SCHEMA.json"), Prompt: prompt, TimeoutSeconds: s.config.JobTimeoutSeconds, Validator: validateResolve})
	if err != nil {
		reason := err.Error()
		s.config.Store.UpdateSearchResult(ctx, id, "failed", &reason, nil)
		return nil
	}
	payload := invocation.Payload
	switch text(payload["outcome"]) {
	case "expanded":
		children := resultInputs(payload["child_results"])
		summary, err := s.config.Store.InsertSearchResults(ctx, runID, source, &id, "listing_child", children)
		if err != nil {
			return err
		}
		reason := fmt.Sprintf("expanded_to_%d_child_results", toInt(summary["inserted"]))
		_, err = s.config.Store.UpdateSearchResult(ctx, id, "expanded", &reason, nil)
		return err
	case "skip_result":
		reason := text(payload["skip_reason"])
		_, err = s.config.Store.UpdateSearchResult(ctx, id, "filtered_out", &reason, nil)
		return err
	case "result_failed":
		reason := text(payload["error_message"])
		_, err = s.config.Store.UpdateSearchResult(ctx, id, "failed", &reason, nil)
		return err
	case "resolved_job":
		job := payload["job"].(map[string]any)
		p := validation.Payload().Profile
		ingested, err := s.config.Store.IngestJob(ctx, store.IngestInput{RunID: runID, RawURL: text(job["raw_url"]), CanonicalURL: text(job["canonical_url"]), Source: firstNonEmpty(text(job["source"]), source), Title: text(job["title"]), Company: text(job["company"]), Location: text(job["location"]), PostedAt: text(job["posted_at"]), DescriptionText: text(job["description_text"]), RoleKeywords: p.TargetRoleKeywords, AllowedLocations: p.AllowedLocations, AllowUnverifiableFreshness: true})
		if err != nil {
			return err
		}
		s.config.Store.IncrementQueryJobsIngested(ctx, runID, source, 1)
		jobKey := ingested.JobKey
		if ingested.Status == "ready_to_apply" {
			claimed, err := s.config.Store.MarkJobApplying(ctx, jobKey)
			if err != nil {
				return err
			}
			if claimed != nil && text(claimed["status"]) == "applying" {
				applied, err := s.applyExisting(ctx, runID, slot, claimed, validation)
				if err != nil {
					return err
				}
				application := applied["application"].(store.Row)
				status := applicationSearchStatus(text(application["status"]))
				reason := optionalText(application["error_message"])
				_, err = s.config.Store.UpdateSearchResult(ctx, id, status, reason, &jobKey)
				return err
			}
		}
		status := "filtered_out"
		if ingested.Status == "duplicate_skipped" {
			status = "duplicate_skipped"
		}
		reason := ingested.Action
		if ingested.StatusReason != nil {
			reason = *ingested.StatusReason
		}
		_, err = s.config.Store.UpdateSearchResult(ctx, id, status, &reason, &jobKey)
		return err
	}
	return nil
}

func (s *Service) applyExisting(ctx context.Context, runID int, slot string, job store.Row, validation profile.ValidationResult) (store.Row, error) {
	jobKey := text(job["job_key"])
	resumeRow, resumeErr := s.ensureResume(ctx, runID, slot, job, validation)
	if resumeErr != nil {
		message := resumeErr.Error()
		customID := resumeErrorID(resumeErr)
		application, err := s.config.Store.RecordApplication(ctx, store.ApplicationInput{JobKey: jobKey, Status: "failed", ConfirmationURL: firstNonEmpty(text(job["canonical_url"]), text(job["raw_url"])), ErrorMessage: message, RunID: &runID, ResumeCustomizationID: customID})
		if err != nil {
			return nil, err
		}
		finding, err := s.config.Store.RecordFinding(ctx, store.FindingInput{JobKey: jobKey, RunID: runID, ApplicationStatus: "failed", Stage: "resume", Category: "resume_customization", Summary: "Failed to generate a tailored resume for this job", Detail: message, PageURL: firstNonEmpty(text(job["canonical_url"]), text(job["raw_url"]))})
		return store.Row{"worker_result": map[string]any{"application_status": "failed", "confirmation_text": nil, "confirmation_url": nil, "error_message": message, "findings": []any{finding}}, "application": application, "findings": []any{finding}}, err
	}
	profileMap := payloadMap(validation.Payload().Profile)
	var customID *int
	if resumeRow != nil {
		id := toInt(resumeRow["id"])
		customID = &id
		if path := text(resumeRow["rendered_pdf_path"]); path != "" {
			profileMap["resume_path"] = path
		}
	}
	resumePath := text(profileMap["resume_path"])
	resumeLabel := ""
	if resumePath != "" {
		resumeLabel = filepath.Base(resumePath)
	}
	notes, _ := profile.ParseApplicantMarkdown(filepath.Join(s.config.RepoRoot, "applicant.md"))
	contextValue := map[string]any{"repo_root": s.config.RepoRoot, "docs": docs(s.config.RepoRoot, false), "profile": profileMap, "job": job, "applicant_sections": notes, "allowed_application_statuses": []string{"submitted", "failed", "blocked", "incomplete", "duplicate_skipped"}}
	templateText, err := s.template("CODEX_APPLY_WORKER_PROMPT.md")
	if err != nil {
		return nil, err
	}
	invocation, err := s.runner.Invoke(ctx, codex.Request{RunID: runID, SessionWorkerType: "apply", SlotKey: slot, WorkerType: "apply", TargetKey: jobKey, SchemaPath: s.promptPath("CODEX_APPLY_WORKER_SCHEMA.json"), PromptTemplate: templateText, RuntimeContext: contextValue, TimeoutSeconds: s.config.JobTimeoutSeconds, Bundle: true, Validator: validateApply})
	if err != nil {
		message := err.Error()
		execution, _ := err.(*codex.ExecutionError)
		bundle := ""
		if execution != nil {
			bundle = execution.FailureBundleDir
			codex.WriteFailureManifest(bundle, runID, jobKey, "failed", message, execution.ResultPath, execution.LogPath, nil)
		}
		application, recordErr := s.config.Store.RecordApplication(ctx, store.ApplicationInput{JobKey: jobKey, Status: "failed", ConfirmationURL: firstNonEmpty(text(job["canonical_url"]), text(job["raw_url"])), ErrorMessage: message, RunID: &runID, ResumeCustomizationID: customID, ResumePathUsed: resumePath, ResumeLabelUsed: resumeLabel})
		if recordErr != nil {
			return nil, recordErr
		}
		finding, recordErr := s.config.Store.RecordFinding(ctx, store.FindingInput{JobKey: jobKey, RunID: runID, ApplicationStatus: "failed", Stage: "worker", Category: "codex_worker_error", Summary: "Codex apply worker did not return a valid result", Detail: codex.DetailWithBundle(message, bundle), PageURL: firstNonEmpty(text(job["canonical_url"]), text(job["raw_url"]))})
		return store.Row{"worker_result": map[string]any{"application_status": "failed", "confirmation_text": nil, "confirmation_url": nil, "error_message": message, "findings": []any{finding}}, "application": application, "findings": []any{finding}}, recordErr
	}
	payload := invocation.Payload
	status := text(payload["application_status"])
	application, err := s.config.Store.RecordApplication(ctx, store.ApplicationInput{JobKey: jobKey, Status: status, ConfirmationText: text(payload["confirmation_text"]), ConfirmationURL: text(payload["confirmation_url"]), ErrorMessage: text(payload["error_message"]), RunID: &runID, ResumeCustomizationID: customID, ResumePathUsed: resumePath, ResumeLabelUsed: resumeLabel})
	if err != nil {
		return nil, err
	}
	findings := []store.Row{}
	if status == "failed" || status == "blocked" || status == "incomplete" {
		manifest := codex.WriteFailureManifest(invocation.FailureBundleDir, runID, jobKey, status, text(payload["error_message"]), invocation.ResultPath, invocation.LogPath, payload)
		bundle := ""
		if manifest != "" {
			bundle = filepath.Dir(manifest)
		}
		for _, item := range payload["findings"].([]any) {
			value := item.(map[string]any)
			finding, err := s.config.Store.RecordFinding(ctx, store.FindingInput{JobKey: jobKey, RunID: runID, ApplicationStatus: status, Stage: text(value["stage"]), Category: text(value["category"]), Summary: text(value["summary"]), Detail: codex.DetailWithBundle(text(value["detail"]), bundle), PageURL: text(value["page_url"])})
			if err != nil {
				return nil, err
			}
			findings = append(findings, finding)
		}
	} else {
		codex.CleanupFailureBundle(invocation.FailureBundleDir)
	}
	return store.Row{"worker_result": payload, "application": application, "findings": findings}, nil
}

type resumeFailure struct {
	error
	id int
}

func (e *resumeFailure) CustomizationID() int { return e.id }
func (s *Service) ensureResume(ctx context.Context, runID int, slot string, job store.Row, validation profile.ValidationResult) (store.Row, error) {
	templatePathPtr := validation.Payload().Profile.ResumeTemplatePath
	if templatePathPtr == nil {
		return nil, nil
	}
	templatePath := *templatePathPtr
	if _, err := os.Stat(templatePath); err != nil {
		return nil, err
	}
	hash := resumelib.DescriptionHash(text(job["description_text"]))
	existing, err := s.config.Store.FindLatestResumeCustomization(ctx, text(job["job_key"]), hash)
	if err != nil {
		return nil, err
	}
	if existing != nil && text(existing["status"]) == "succeeded" {
		if _, err := os.Stat(text(existing["rendered_pdf_path"])); err == nil {
			return existing, nil
		}
	}
	template, err := resumelib.ParseTemplate(templatePath)
	if err != nil {
		return nil, err
	}
	rows, _ := s.config.Store.QueryRows(ctx, `SELECT id FROM resume_customizations WHERE job_key=?`, job["job_key"])
	outputDir := filepath.Join(filepath.Dir(s.config.Store.Path()), "resume_customizations", text(job["job_key"]), fmt.Sprintf("v%d", len(rows)+1))
	if err = os.MkdirAll(outputDir, 0o755); err != nil {
		return nil, err
	}
	created, err := s.config.Store.CreateResumeCustomization(ctx, store.ResumeCustomizationInput{JobKey: text(job["job_key"]), RunID: &runID, Status: "running", SourceTemplatePath: templatePath, JobDescriptionHash: hash})
	if err != nil {
		return nil, err
	}
	id := toInt(created["id"])
	notes, _ := profile.ParseApplicantMarkdown(validation.ApplicantMDPath)
	contextValue := map[string]any{"job": map[string]any{"job_key": job["job_key"], "title": job["title"], "company": job["company"], "location": job["location"], "description_text": job["description_text"], "canonical_url": job["canonical_url"]}, "profile": payloadMap(validation.Payload().Profile), "applicant_sections": notes, "template_contract": map[string]any{"summary_block": "AUTO_SUMMARY", "skills_block": "AUTO_SKILLS", "bullet_block_slugs": template.BulletSlugs(), "immutability_rules": []string{"Do not change employers, job titles, dates, schools, degrees, or locations.", "Only customize summary, skills, and marked bullet blocks.", "Keep every claim grounded in the existing resume facts and applicant notes."}}}
	prompt, err := s.prompt("CODEX_RESUME_CUSTOMIZER_PROMPT.md", contextValue)
	if err != nil {
		return nil, err
	}
	invocation, err := s.runner.Invoke(ctx, codex.Request{RunID: runID, SessionWorkerType: "resume", SlotKey: slot + "-" + codex.SafeFilename(text(job["job_key"])), WorkerType: "resume_customization", TargetKey: text(job["job_key"]), SchemaPath: s.promptPath("CODEX_RESUME_CUSTOMIZER_SCHEMA.json"), Prompt: prompt, TimeoutSeconds: s.config.JobTimeoutSeconds, Validator: validateResume})
	if err != nil {
		s.config.Store.UpdateResumeCustomization(ctx, id, map[string]any{"status": "failed", "error_message": err.Error()})
		return nil, &resumeFailure{err, id}
	}
	data, _ := json.Marshal(invocation.Payload)
	var custom resumelib.Customization
	if err = json.Unmarshal(data, &custom); err != nil {
		return nil, &resumeFailure{err, id}
	}
	if err = resumelib.Validate(custom, template); err != nil {
		return nil, &resumeFailure{err, id}
	}
	texPath := filepath.Join(outputDir, "resume.tex")
	if err = os.WriteFile(texPath, []byte(resumelib.Render(template, custom)), 0o600); err != nil {
		return nil, &resumeFailure{err, id}
	}
	pdfPath, compiler, err := resumelib.Compile(ctx, texPath, outputDir)
	if err != nil {
		s.config.Store.UpdateResumeCustomization(ctx, id, map[string]any{"status": "failed", "rendered_tex_path": texPath, "customization_payload_json": string(data), "error_message": err.Error()})
		return nil, &resumeFailure{err, id}
	}
	preview := resumelib.Preview(custom, text(job["title"]), text(job["company"]))
	return s.config.Store.UpdateResumeCustomization(ctx, id, map[string]any{"status": "succeeded", "rendered_tex_path": texPath, "rendered_pdf_path": pdfPath, "preview_content": preview, "customization_payload_json": string(data), "compiler": compiler, "error_message": nil})
}

func (s *Service) prompt(name string, contextValue any) (string, error) {
	template, err := s.template(name)
	if err != nil {
		return "", err
	}
	return codex.ComposePrompt(template, contextValue), nil
}
func (s *Service) template(name string) (string, error) {
	return codex.LoadTemplate(s.promptPath(name))
}
func (s *Service) promptPath(name string) string {
	return filepath.Join(s.config.RepoRoot, "PROMPTS", name)
}
func docs(root string, searchSpec bool) map[string]any {
	out := map[string]any{"workflow": filepath.Join(root, "WORKFLOW.md"), "application_rules": filepath.Join(root, "APPLICATION_RULES.md"), "mcp_setup": filepath.Join(root, "MCP_SETUP.md"), "env": filepath.Join(root, ".env"), "applicant_md": filepath.Join(root, "applicant.md")}
	if searchSpec {
		out["search_spec"] = filepath.Join(root, "SEARCH_SPEC.md")
	}
	return out
}
func resultInputs(value any) []store.SearchResultInput {
	out := []store.SearchResultInput{}
	values, _ := value.([]any)
	for _, item := range values {
		row, _ := item.(map[string]any)
		out = append(out, store.SearchResultInput{URL: text(row["url"]), Title: text(row["title"]), Snippet: text(row["snippet"]), VisibleDate: text(row["visible_date"]), PageNumber: toInt(row["page_number"]), Rank: toInt(row["rank"])})
	}
	return out
}
func payloadMap(value any) map[string]any {
	data, _ := json.Marshal(value)
	out := map[string]any{}
	json.Unmarshal(data, &out)
	return out
}
func decodeMap(value any) map[string]any {
	if row, ok := value.(map[string]any); ok {
		return row
	}
	raw := text(value)
	if raw == "" {
		return nil
	}
	out := map[string]any{}
	if json.Unmarshal([]byte(raw), &out) != nil {
		return nil
	}
	return out
}
func optionalText(value any) *string {
	raw := strings.TrimSpace(text(value))
	if raw == "" {
		return nil
	}
	return &raw
}
func text(value any) string {
	if value == nil {
		return ""
	}
	return fmt.Sprint(value)
}
func toInt(value any) int {
	switch n := value.(type) {
	case int:
		return n
	case int64:
		return int(n)
	case float64:
		return int(n)
	case json.Number:
		i, _ := n.Int64()
		return int(i)
	case string:
		i, _ := strconv.Atoi(n)
		return i
	}
	return 0
}
func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return value
		}
	}
	return ""
}
func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}
func applicationSearchStatus(status string) string {
	switch status {
	case "submitted":
		return "applied"
	case "blocked", "incomplete", "failed":
		return status
	case "duplicate_skipped":
		return "duplicate_skipped"
	}
	return "failed"
}
func resumeErrorID(err error) *int {
	var value interface{ CustomizationID() int }
	if errors.As(err, &value) {
		id := value.CustomizationID()
		return &id
	}
	return nil
}
