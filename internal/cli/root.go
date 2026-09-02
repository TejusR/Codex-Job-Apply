package cli

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/ramesh/codex-job-apply/internal/codex"
	"github.com/ramesh/codex-job-apply/internal/dashboard"
	"github.com/ramesh/codex-job-apply/internal/profile"
	"github.com/ramesh/codex-job-apply/internal/store"
	"github.com/ramesh/codex-job-apply/internal/workflow"
	"github.com/spf13/cobra"
)

type exitError struct {
	code int
	err  error
}

func (e *exitError) Error() string { return e.err.Error() }

type app struct{ dbPath string }

func Execute(args []string) int {
	a := &app{}
	root := a.root()
	root.SetArgs(args)
	if err := root.Execute(); err != nil {
		var exit *exitError
		if errors.As(err, &exit) {
			return exit.code
		}
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	return 0
}
func (a *app) root() *cobra.Command {
	root := &cobra.Command{Use: "job-apply-bot", Short: "Support tooling for the Codex-driven job application workflow.", SilenceUsage: true, SilenceErrors: true}
	root.CompletionOptions.DisableDefaultCmd = true
	root.PersistentFlags().StringVar(&a.dbPath, "db-path", filepath.FromSlash("data/job_apply_bot.sqlite3"), "SQLite database path")
	root.AddCommand(a.validateProfile(), a.startRun(), a.prepareRun(), a.finishRun(), a.ingestJob(), a.nextJob(), a.nextQuery(), a.claimQuery(), a.completeQuery(), a.failQuery(), a.workflowStatus(), a.recordApplication(), a.recordFinding(), a.discover(), a.apply(), a.runWorkflow(), a.requeue(), a.serveDashboard())
	return root
}
func (a *app) open() (*store.Store, error) { return store.Open(a.dbPath) }
func printJSON(value any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	fmt.Println(string(data))
	return nil
}

func (a *app) validateProfile() *cobra.Command {
	var root string
	cmd := &cobra.Command{Use: "validate-profile", Short: "Validate .env and applicant.md inputs.", RunE: func(cmd *cobra.Command, args []string) error {
		result := profile.Validate(root)
		if err := printJSON(result.Payload()); err != nil {
			return err
		}
		if !result.OK() {
			return &exitError{2, errors.New("profile validation failed")}
		}
		return nil
	}}
	cmd.Flags().StringVar(&root, "repo-root", ".", "Repository root containing .env and applicant.md")
	return cmd
}
func (a *app) startRun() *cobra.Command {
	return &cobra.Command{Use: "start-run", RunE: func(cmd *cobra.Command, args []string) error {
		s, err := a.open()
		if err != nil {
			return err
		}
		defer s.Close()
		row, err := s.StartRun(cmd.Context())
		if err != nil {
			return err
		}
		return printJSON(row)
	}}
}
func (a *app) prepareRun() *cobra.Command {
	var root string
	cmd := &cobra.Command{Use: "prepare-run", RunE: func(cmd *cobra.Command, args []string) error {
		s, err := a.open()
		if err != nil {
			return err
		}
		defer s.Close()
		row, err := s.PrepareRun(cmd.Context(), root)
		if err != nil {
			return err
		}
		return printJSON(row)
	}}
	cmd.Flags().StringVar(&root, "repo-root", ".", "Repository root containing .env and applicant.md")
	return cmd
}
func (a *app) finishRun() *cobra.Command {
	var id int
	var force bool
	cmd := &cobra.Command{Use: "finish-run", RunE: func(cmd *cobra.Command, args []string) error {
		s, err := a.open()
		if err != nil {
			return err
		}
		defer s.Close()
		row, err := s.FinishRun(cmd.Context(), id, force)
		if err != nil {
			return err
		}
		return printJSON(row)
	}}
	cmd.Flags().IntVar(&id, "run-id", 0, "Run id")
	cmd.MarkFlagRequired("run-id")
	cmd.Flags().BoolVar(&force, "force", false, "Finish even when unresolved work remains")
	return cmd
}
func (a *app) ingestJob() *cobra.Command {
	var input store.IngestInput
	var roles, locations string
	cmd := &cobra.Command{Use: "ingest-job", RunE: func(cmd *cobra.Command, args []string) error {
		input.RoleKeywords = profile.ParseCSV(roles)
		input.AllowedLocations = profile.ParseCSV(locations)
		s, err := a.open()
		if err != nil {
			return err
		}
		defer s.Close()
		row, err := s.IngestJob(cmd.Context(), input)
		if err != nil {
			return err
		}
		return printJSON(row)
	}}
	f := cmd.Flags()
	f.IntVar(&input.RunID, "run-id", 0, "Run id")
	f.StringVar(&input.RawURL, "raw-url", "", "Raw job URL")
	f.StringVar(&input.CanonicalURL, "canonical-url", "", "Canonical URL")
	f.StringVar(&input.Source, "source", "", "Source")
	f.StringVar(&input.Title, "title", "", "Title")
	f.StringVar(&input.Company, "company", "", "Company")
	f.StringVar(&input.Location, "location", "", "Location")
	f.StringVar(&input.PostedAt, "posted-at", "", "Posted at")
	f.StringVar(&input.DiscoveredAt, "discovered-at", "", "Discovered at")
	f.StringVar(&roles, "role-keywords", "", "Role keywords")
	f.StringVar(&locations, "allowed-locations", "", "Allowed locations")
	f.BoolVar(&input.AllowUnverifiableFreshness, "allow-unverifiable-freshness", false, "Keep jobs with unverified freshness eligible")
	cmd.MarkFlagRequired("run-id")
	cmd.MarkFlagRequired("raw-url")
	return cmd
}
func (a *app) nextJob() *cobra.Command {
	var mark bool
	cmd := &cobra.Command{Use: "next-job", RunE: func(cmd *cobra.Command, args []string) error {
		s, err := a.open()
		if err != nil {
			return err
		}
		defer s.Close()
		row, err := s.NextJob(cmd.Context(), mark)
		if err != nil {
			return err
		}
		return printJSON(row)
	}}
	cmd.Flags().BoolVar(&mark, "mark-applying", false, "Mark returned job as applying")
	return cmd
}
func (a *app) nextQuery() *cobra.Command  { return a.queryClaimCommand("next-query", false) }
func (a *app) claimQuery() *cobra.Command { return a.queryClaimCommand("claim-query", true) }
func (a *app) queryClaimCommand(use string, claim bool) *cobra.Command {
	var id int
	cmd := &cobra.Command{Use: use, RunE: func(cmd *cobra.Command, args []string) error {
		s, err := a.open()
		if err != nil {
			return err
		}
		defer s.Close()
		var row store.Row
		if claim {
			row, err = s.ClaimQuery(cmd.Context(), id)
		} else {
			row, err = s.NextQuery(cmd.Context(), id)
		}
		if err != nil {
			return err
		}
		return printJSON(row)
	}}
	cmd.Flags().IntVar(&id, "run-id", 0, "Run id")
	cmd.MarkFlagRequired("run-id")
	return cmd
}
func (a *app) completeQuery() *cobra.Command { return a.updateQueryCommand("complete-query", false) }
func (a *app) failQuery() *cobra.Command     { return a.updateQueryCommand("fail-query", true) }
func (a *app) updateQueryCommand(use string, failed bool) *cobra.Command {
	var id int
	var source, message string
	var resultsSeen, jobsIngested int
	cmd := &cobra.Command{Use: use, RunE: func(cmd *cobra.Command, args []string) error {
		s, err := a.open()
		if err != nil {
			return err
		}
		defer s.Close()
		var seenPtr, ingestedPtr *int
		if cmd.Flags().Changed("results-seen") {
			seenPtr = &resultsSeen
		}
		if cmd.Flags().Changed("jobs-ingested") {
			ingestedPtr = &jobsIngested
		}
		status := "completed"
		var messagePtr *string
		if failed {
			status = "failed"
			messagePtr = &message
		}
		row, err := s.UpdateQuery(cmd.Context(), id, source, status, seenPtr, ingestedPtr, nil, messagePtr)
		if err != nil {
			return err
		}
		return printJSON(row)
	}}
	f := cmd.Flags()
	f.IntVar(&id, "run-id", 0, "Run id")
	f.StringVar(&source, "source-key", "", "Source key")
	f.IntVar(&resultsSeen, "results-seen", 0, "Results seen")
	f.IntVar(&jobsIngested, "jobs-ingested", 0, "Jobs ingested")
	cmd.MarkFlagRequired("run-id")
	cmd.MarkFlagRequired("source-key")
	if failed {
		f.StringVar(&message, "message", "", "Error message")
		cmd.MarkFlagRequired("message")
	}
	return cmd
}
func (a *app) workflowStatus() *cobra.Command {
	var id int
	cmd := &cobra.Command{Use: "workflow-status", RunE: func(cmd *cobra.Command, args []string) error {
		s, err := a.open()
		if err != nil {
			return err
		}
		defer s.Close()
		row, err := s.WorkflowStatus(cmd.Context(), id)
		if err != nil {
			return err
		}
		return printJSON(row)
	}}
	cmd.Flags().IntVar(&id, "run-id", 0, "Run id")
	cmd.MarkFlagRequired("run-id")
	return cmd
}
func (a *app) recordApplication() *cobra.Command {
	var input store.ApplicationInput
	var root string
	var runID, customID int
	cmd := &cobra.Command{Use: "record-application", RunE: func(cmd *cobra.Command, args []string) error {
		if cmd.Flags().Changed("run-id") {
			input.RunID = &runID
		}
		if cmd.Flags().Changed("resume-customization-id") {
			input.ResumeCustomizationID = &customID
		}
		if input.ResumePathUsed == "" {
			validation := profile.Validate(root)
			if path := validation.Payload().Profile.ResumePath; path != nil {
				input.ResumePathUsed = *path
			}
		}
		if input.ResumeLabelUsed == "" && input.ResumePathUsed != "" {
			input.ResumeLabelUsed = filepath.Base(input.ResumePathUsed)
		}
		s, err := a.open()
		if err != nil {
			return err
		}
		defer s.Close()
		row, err := s.RecordApplication(cmd.Context(), input)
		if err != nil {
			return err
		}
		return printJSON(row)
	}}
	f := cmd.Flags()
	f.StringVar(&input.JobKey, "job-key", "", "Job key")
	f.StringVar(&input.Status, "status", "", "Application status")
	f.StringVar(&input.ConfirmationText, "confirmation-text", "", "Confirmation text")
	f.StringVar(&input.ConfirmationURL, "confirmation-url", "", "Confirmation URL")
	f.StringVar(&input.ErrorMessage, "error-message", "", "Error message")
	f.IntVar(&runID, "run-id", 0, "Run id")
	f.StringVar(&root, "repo-root", ".", "Repository root")
	f.IntVar(&customID, "resume-customization-id", 0, "Resume customization id")
	f.StringVar(&input.ResumePathUsed, "resume-path-used", "", "Resume path")
	f.StringVar(&input.ResumeLabelUsed, "resume-label-used", "", "Resume label")
	cmd.MarkFlagRequired("job-key")
	cmd.MarkFlagRequired("status")
	return cmd
}
func (a *app) recordFinding() *cobra.Command {
	var input store.FindingInput
	cmd := &cobra.Command{Use: "record-finding", RunE: func(cmd *cobra.Command, args []string) error {
		s, err := a.open()
		if err != nil {
			return err
		}
		defer s.Close()
		row, err := s.RecordFinding(cmd.Context(), input)
		if err != nil {
			return err
		}
		return printJSON(row)
	}}
	f := cmd.Flags()
	f.StringVar(&input.JobKey, "job-key", "", "Job key")
	f.IntVar(&input.RunID, "run-id", 0, "Run id")
	f.StringVar(&input.ApplicationStatus, "application-status", "", "Application status")
	f.StringVar(&input.Stage, "stage", "", "Stage")
	f.StringVar(&input.Category, "category", "", "Category")
	f.StringVar(&input.Summary, "summary", "", "Summary")
	f.StringVar(&input.Detail, "detail", "", "Detail")
	f.StringVar(&input.PageURL, "page-url", "", "Page URL")
	for _, name := range []string{"job-key", "run-id", "application-status", "stage", "category", "summary"} {
		cmd.MarkFlagRequired(name)
	}
	return cmd
}

type workerFlags struct {
	root, codexBin, codexProfile                       string
	queryTimeout, jobTimeout, maxRetries, applyWorkers int
	discoveryWorkers                                   string
}

func (w *workerFlags) add(cmd *cobra.Command, mode string) {
	f := cmd.Flags()
	f.StringVar(&w.root, "repo-root", ".", "Repository root")
	f.StringVar(&w.codexBin, "codex-bin", codex.DefaultCodexBin(), "Codex executable")
	f.StringVar(&w.codexProfile, "codex-profile", "", "Codex profile")
	f.IntVar(&w.maxRetries, "max-worker-retries", 1, "Retries after first worker attempt")
	if mode == "query" {
		f.IntVar(&w.queryTimeout, "timeout-seconds", 0, "Query timeout seconds")
	} else if mode == "job" {
		f.IntVar(&w.jobTimeout, "timeout-seconds", 0, "Job timeout seconds")
	} else {
		f.IntVar(&w.queryTimeout, "query-timeout-seconds", 0, "Query timeout seconds")
		f.IntVar(&w.jobTimeout, "job-timeout-seconds", 0, "Job timeout seconds")
		f.StringVar(&w.discoveryWorkers, "discovery-workers", "auto", "Discovery worker concurrency")
		f.IntVar(&w.applyWorkers, "apply-workers", 5, "Apply worker concurrency")
	}
}
func (a *app) service(w workerFlags, s *store.Store) *workflow.Service {
	discovery := 0
	if w.discoveryWorkers != "" && strings.ToLower(w.discoveryWorkers) != "auto" {
		discovery, _ = strconv.Atoi(w.discoveryWorkers)
	}
	return workflow.New(workflow.Config{Store: s, RepoRoot: w.root, CodexBin: w.codexBin, CodexProfile: w.codexProfile, QueryTimeoutSeconds: w.queryTimeout, JobTimeoutSeconds: w.jobTimeout, MaxWorkerRetries: w.maxRetries, DiscoveryWorkers: discovery, ApplyWorkers: w.applyWorkers})
}
func (a *app) discover() *cobra.Command {
	var w workerFlags
	var id int
	var source string
	cmd := &cobra.Command{Use: "discover-next-candidate-with-codex", RunE: func(cmd *cobra.Command, args []string) error {
		s, err := a.open()
		if err != nil {
			return err
		}
		defer s.Close()
		row, err := a.service(w, s).DiscoverOne(cmd.Context(), id, source)
		if err != nil {
			return err
		}
		return printJSON(row)
	}}
	w.add(cmd, "query")
	cmd.Flags().IntVar(&id, "run-id", 0, "Run id")
	cmd.Flags().StringVar(&source, "source-key", "", "Source key")
	cmd.MarkFlagRequired("run-id")
	cmd.MarkFlagRequired("source-key")
	return cmd
}
func (a *app) apply() *cobra.Command {
	var w workerFlags
	var id int
	var key string
	cmd := &cobra.Command{Use: "apply-job-with-codex", RunE: func(cmd *cobra.Command, args []string) error {
		s, err := a.open()
		if err != nil {
			return err
		}
		defer s.Close()
		row, err := a.service(w, s).ApplyOne(cmd.Context(), id, key)
		if err != nil {
			return err
		}
		return printJSON(row)
	}}
	w.add(cmd, "job")
	cmd.Flags().IntVar(&id, "run-id", 0, "Run id")
	cmd.Flags().StringVar(&key, "job-key", "", "Job key")
	cmd.MarkFlagRequired("run-id")
	cmd.MarkFlagRequired("job-key")
	return cmd
}
func (a *app) runWorkflow() *cobra.Command {
	var w workerFlags
	var id int
	cmd := &cobra.Command{Use: "run-workflow", RunE: func(cmd *cobra.Command, args []string) error {
		s, err := a.open()
		if err != nil {
			return err
		}
		defer s.Close()
		var idPtr *int
		if cmd.Flags().Changed("run-id") {
			idPtr = &id
		}
		row, err := a.service(w, s).Run(cmd.Context(), idPtr)
		if err != nil {
			return err
		}
		return printJSON(row)
	}}
	w.add(cmd, "workflow")
	cmd.Flags().IntVar(&id, "run-id", 0, "Existing run id")
	return cmd
}
func (a *app) requeue() *cobra.Command {
	var id int
	cmd := &cobra.Command{Use: "requeue-runner-failures", RunE: func(cmd *cobra.Command, args []string) error {
		s, err := a.open()
		if err != nil {
			return err
		}
		defer s.Close()
		row, err := s.RequeueRunnerFailures(cmd.Context(), id)
		if err != nil {
			return err
		}
		return printJSON(row)
	}}
	cmd.Flags().IntVar(&id, "run-id", 0, "Run id")
	cmd.MarkFlagRequired("run-id")
	return cmd
}
func (a *app) serveDashboard() *cobra.Command {
	var root, host string
	var port int
	var reload bool
	cmd := &cobra.Command{Use: "serve-dashboard", RunE: func(cmd *cobra.Command, args []string) error {
		if reload && os.Getenv("JOB_APPLY_BOT_RELOAD_CHILD") != "1" {
			return dashboard.RunReloader(cmd.Context(), dashboard.ReloadConfig{RepoRoot: root, DBPath: a.dbPath, Host: host, Port: port})
		}
		s, err := a.open()
		if err != nil {
			return err
		}
		defer s.Close()
		service := &dashboard.Service{Store: s, RepoRoot: root}
		server := &http.Server{Addr: fmt.Sprintf("%s:%d", host, port), Handler: (&dashboard.Server{Service: service}).Handler()}
		return server.ListenAndServe()
	}}
	f := cmd.Flags()
	f.StringVar(&root, "repo-root", ".", "Repository root")
	f.StringVar(&host, "host", "127.0.0.1", "Host interface")
	f.IntVar(&port, "port", 8000, "Port")
	f.BoolVar(&reload, "reload", false, "Enable development reload mode")
	return cmd
}

func WithContext(parent context.Context, args []string) int { _ = parent; return Execute(args) }
