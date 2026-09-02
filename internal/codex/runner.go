package codex

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/pelletier/go-toml/v2"
	"github.com/ramesh/codex-job-apply/internal/store"
)

type Runner struct {
	Store                            *store.Store
	RepoRoot, CodexBin, CodexProfile string
	MaxRetries                       int
}
type Request struct {
	RunID                                                                 int
	SessionWorkerType, SlotKey, WorkerType, TargetKey, SchemaPath, Prompt string
	PromptTemplate                                                        string
	RuntimeContext                                                        map[string]any
	TimeoutSeconds                                                        int
	Bundle                                                                bool
	Validator                                                             func(map[string]any) error
}
type Result struct {
	Payload                                         map[string]any
	FailureBundleDir, ResultPath, LogPath, ThreadID string
}
type ExecutionError struct{ Message, FailureBundleDir, ResultPath, LogPath, ThreadID string }

func (e *ExecutionError) Error() string { return e.Message }

var safeFilenamePattern = regexp.MustCompile(`[^A-Za-z0-9._-]+`)

func DefaultCodexBin() string {
	if runtime.GOOS == "windows" {
		if path, err := exec.LookPath("codex.cmd"); err == nil {
			return path
		}
	}
	if path, err := exec.LookPath("codex"); err == nil {
		return path
	}
	if runtime.GOOS == "windows" {
		return "codex.cmd"
	}
	return "codex"
}

func (r *Runner) Invoke(ctx context.Context, req Request) (Result, error) {
	if _, err := os.Stat(req.SchemaPath); err != nil {
		return Result{}, &ExecutionError{Message: "Schema file is missing: " + req.SchemaPath}
	}
	if _, err := r.Store.EnsureWorkerSession(ctx, req.RunID, req.SessionWorkerType, req.SlotKey); err != nil {
		return Result{}, err
	}
	artifactDir := filepath.Join(filepath.Dir(r.Store.Path()), "codex_worker_artifacts", fmt.Sprintf("run-%d", req.RunID), req.WorkerType)
	if err := os.MkdirAll(artifactDir, 0o755); err != nil {
		return Result{}, err
	}
	safeTarget := SafeFilename(req.TargetKey)
	sequence := nextSequence(artifactDir, safeTarget)
	session, _ := r.Store.UpdateWorkerSession(ctx, req.RunID, req.SessionWorkerType, req.SlotKey, "idle", nil, nil)
	threadID := optionalString(session["thread_id"])
	last := &ExecutionError{Message: fmt.Sprintf("Codex %s worker did not complete.", req.WorkerType), ThreadID: threadID}
	for attempt := 1; attempt <= r.MaxRetries+1; attempt++ {
		resultPath := filepath.Join(artifactDir, fmt.Sprintf("%s.invocation-%d.attempt-%d.result.json", safeTarget, sequence, attempt))
		logPath := filepath.Join(artifactDir, fmt.Sprintf("%s.invocation-%d.attempt-%d.log.txt", safeTarget, sequence, attempt))
		os.Remove(resultPath)
		os.Remove(logPath)
		prompt := req.Prompt
		bundleDir := ""
		if req.Bundle {
			bundleDir = filepath.Join(artifactDir, fmt.Sprintf("%s.invocation-%d.attempt-%d.failure", safeTarget, sequence, attempt))
			if err := os.MkdirAll(bundleDir, 0o755); err != nil {
				return Result{}, err
			}
			if req.RuntimeContext != nil {
				contextValue := cloneMap(req.RuntimeContext)
				contextValue["failure_bundle"] = failureBundlePaths(bundleDir)
				prompt = ComposePrompt(req.PromptTemplate, contextValue)
				data, _ := json.MarshalIndent(contextValue, "", "  ")
				if err := os.WriteFile(filepath.Join(bundleDir, "runtime_context.json"), data, 0o600); err != nil {
					return Result{}, err
				}
			}
			if err := os.WriteFile(filepath.Join(bundleDir, "prompt.txt"), []byte(prompt), 0o600); err != nil {
				return Result{}, err
			}
		}
		row, err := r.Store.StartWorkerAttempt(ctx, req.RunID, req.WorkerType, req.TargetKey, attempt, resultPath, logPath)
		if err != nil {
			return Result{}, err
		}
		attemptID := toInt(row["id"])
		r.Store.UpdateWorkerSession(ctx, req.RunID, req.SessionWorkerType, req.SlotKey, "running", nullable(threadID), nil)
		args, commandText, err := r.buildCommand(resultPath, req.SchemaPath, threadID)
		if err != nil {
			return Result{}, err
		}
		runCtx := ctx
		cancel := func() {}
		if req.TimeoutSeconds > 0 {
			runCtx, cancel = context.WithTimeout(ctx, timeDurationSeconds(req.TimeoutSeconds))
		}
		stdout, stderr, exitCode, runErr := runCommand(runCtx, r.RepoRoot, r.CodexBin, args, prompt)
		cancel()
		threadID = coalesceThreadID(threadID, stdout, stderr)
		writeLog(logPath, commandText, prompt, stdout, stderr, exitCode, errorText(runErr))
		last = &ExecutionError{FailureBundleDir: bundleDir, ResultPath: resultPath, LogPath: logPath, ThreadID: threadID}
		if runErr != nil {
			if errors.Is(runCtx.Err(), context.DeadlineExceeded) {
				last.Message = fmt.Sprintf("Codex %s worker timed out after %d seconds.", req.WorkerType, req.TimeoutSeconds)
				payload := loadRecovered(resultPath, stdout, stderr, req.Validator)
				if payload != nil {
					r.Store.FinishWorkerAttempt(ctx, attemptID, "succeeded", nil, nil)
					r.Store.UpdateWorkerSession(ctx, req.RunID, req.SessionWorkerType, req.SlotKey, "idle", nullable(threadID), nil)
					return Result{payload, bundleDir, resultPath, logPath, threadID}, nil
				}
				r.Store.FinishWorkerAttempt(ctx, attemptID, "timed_out", nil, &last.Message)
				r.Store.UpdateWorkerSession(ctx, req.RunID, req.SessionWorkerType, req.SlotKey, "idle", nullable(threadID), &last.Message)
				writeFailureManifest(bundleDir, req.RunID, req.TargetKey, "", last.Message, resultPath, logPath, nil)
				return Result{}, last
			}
			last.Message = fmt.Sprintf("Codex %s worker exited with code %d.", req.WorkerType, exitCode)
			r.Store.FinishWorkerAttempt(ctx, attemptID, "cli_failed", &exitCode, &last.Message)
			r.Store.UpdateWorkerSession(ctx, req.RunID, req.SessionWorkerType, req.SlotKey, "idle", nullable(threadID), &last.Message)
			writeFailureManifest(bundleDir, req.RunID, req.TargetKey, "", last.Message, resultPath, logPath, nil)
			continue
		}
		payload, err := loadPayload(resultPath, req.Validator)
		if err != nil {
			last.Message = fmt.Sprintf("Codex %s worker returned invalid output: %v", req.WorkerType, err)
			status := "invalid_output"
			if os.IsNotExist(err) {
				status = "missing_output"
			}
			r.Store.FinishWorkerAttempt(ctx, attemptID, status, &exitCode, &last.Message)
			r.Store.UpdateWorkerSession(ctx, req.RunID, req.SessionWorkerType, req.SlotKey, "idle", nullable(threadID), &last.Message)
			writeFailureManifest(bundleDir, req.RunID, req.TargetKey, "", last.Message, resultPath, logPath, nil)
			continue
		}
		r.Store.FinishWorkerAttempt(ctx, attemptID, "succeeded", &exitCode, nil)
		r.Store.UpdateWorkerSession(ctx, req.RunID, req.SessionWorkerType, req.SlotKey, "idle", nullable(threadID), nil)
		return Result{payload, bundleDir, resultPath, logPath, threadID}, nil
	}
	return Result{}, last
}

func (r *Runner) buildCommand(resultPath, schemaPath, threadID string) ([]string, []string, error) {
	overrides, err := playwrightOverrides()
	if err != nil {
		return nil, nil, err
	}
	args := []string{"exec", "--dangerously-bypass-approvals-and-sandbox", "--color", "never", "-C", r.RepoRoot, "--json", "-o", resultPath}
	for _, override := range overrides {
		args = append(args, "-c", override)
	}
	if r.CodexProfile != "" {
		args = append(args, "-p", r.CodexProfile)
	}
	if threadID != "" {
		args = append(args, "resume", threadID, "-")
	} else {
		args = append(args, "--output-schema", schemaPath)
	}
	return args, append([]string{r.CodexBin}, args...), nil
}

func playwrightOverrides() ([]string, error) {
	config := map[string]any{"command": "npx", "args": []any{"@playwright/mcp@latest"}}
	home, err := os.UserHomeDir()
	if err == nil {
		if data, readErr := os.ReadFile(filepath.Join(home, ".codex", "config.toml")); readErr == nil {
			var root map[string]any
			if toml.Unmarshal(data, &root) == nil {
				if servers, ok := root["mcp_servers"].(map[string]any); ok {
					if value, ok := servers["playwright"].(map[string]any); ok {
						config = normalizePlaywright(value)
					}
				}
			}
		}
	}
	return []string{`model_reasoning_effort="low"`, `plugins={}`, "mcp_servers=" + tomlLiteral(map[string]any{"playwright": config})}, nil
}
func normalizePlaywright(value map[string]any) map[string]any {
	out := map[string]any{"command": "npx", "args": []any{"@playwright/mcp@latest"}}
	if command, ok := value["command"].(string); ok && strings.TrimSpace(command) != "" {
		out["command"] = strings.TrimSpace(command)
	}
	if args, ok := value["args"].([]any); ok {
		valid := []any{}
		for _, arg := range args {
			if text, ok := arg.(string); ok {
				valid = append(valid, text)
			}
		}
		if len(valid) == len(args) {
			out["args"] = valid
		}
	}
	if env, ok := value["env"].(map[string]any); ok {
		normalized := map[string]any{}
		for key, item := range env {
			switch item.(type) {
			case string, bool, int64, float64:
				normalized[key] = item
			}
		}
		if len(normalized) > 0 {
			out["env"] = normalized
		}
	}
	return out
}
func tomlLiteral(value any) string {
	switch v := value.(type) {
	case nil:
		return `""`
	case bool:
		if v {
			return "true"
		}
		return "false"
	case int:
		return strconv.Itoa(v)
	case int64:
		return strconv.FormatInt(v, 10)
	case float64:
		return strconv.FormatFloat(v, 'g', -1, 64)
	case string:
		data, _ := json.Marshal(v)
		return string(data)
	case []any:
		parts := []string{}
		for _, item := range v {
			parts = append(parts, tomlLiteral(item))
		}
		return "[" + strings.Join(parts, ",") + "]"
	case []string:
		items := make([]any, len(v))
		for i := range v {
			items[i] = v[i]
		}
		return tomlLiteral(items)
	case map[string]any:
		keys := make([]string, 0, len(v))
		for key := range v {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		parts := []string{}
		for _, key := range keys {
			parts = append(parts, key+"="+tomlLiteral(v[key]))
		}
		return "{" + strings.Join(parts, ",") + "}"
	default:
		return fmt.Sprint(v)
	}
}

func runCommand(ctx context.Context, cwd, name string, args []string, input string) (string, string, int, error) {
	actualName := name
	actualArgs := args
	if runtime.GOOS == "windows" && (strings.HasSuffix(strings.ToLower(name), ".cmd") || strings.HasSuffix(strings.ToLower(name), ".bat")) {
		actualName = "cmd.exe"
		actualArgs = append([]string{"/d", "/c", name}, args...)
	}
	cmd := exec.CommandContext(ctx, actualName, actualArgs...)
	cmd.Dir = cwd
	cmd.Stdin = strings.NewReader(input)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	exitCode := 0
	if err != nil {
		exitCode = -1
		if exit, ok := err.(*exec.ExitError); ok {
			exitCode = exit.ExitCode()
		}
	}
	return stdout.String(), stderr.String(), exitCode, err
}
func loadPayload(path string, validator func(map[string]any) error) (map[string]any, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var payload map[string]any
	if err = json.Unmarshal(data, &payload); err != nil {
		return nil, err
	}
	if validator != nil {
		if err = validator(payload); err != nil {
			return nil, err
		}
	}
	return payload, nil
}
func loadRecovered(path, stdout, stderr string, validator func(map[string]any) error) map[string]any {
	if payload, err := loadPayload(path, validator); err == nil {
		return payload
	}
	var last map[string]any
	for _, stream := range []string{stdout, stderr} {
		scanner := bufio.NewScanner(strings.NewReader(stream))
		for scanner.Scan() {
			var payload map[string]any
			if json.Unmarshal([]byte(strings.TrimSpace(scanner.Text())), &payload) == nil && (validator == nil || validator(payload) == nil) {
				last = payload
			}
		}
	}
	return last
}
func coalesceThreadID(current string, streams ...string) string {
	if current != "" {
		return current
	}
	for _, stream := range streams {
		scanner := bufio.NewScanner(strings.NewReader(stream))
		for scanner.Scan() {
			var payload map[string]any
			if json.Unmarshal([]byte(strings.TrimSpace(scanner.Text())), &payload) == nil && payload["type"] == "thread.started" {
				if id, ok := payload["thread_id"].(string); ok && strings.TrimSpace(id) != "" {
					return id
				}
			}
		}
	}
	return ""
}

func ComposePrompt(template string, contextValue any) string {
	data, _ := json.MarshalIndent(contextValue, "", "  ")
	return strings.TrimSpace(template) + "\n\nRuntime context follows. Treat it as authoritative input for this run.\nReturn only JSON that matches the provided schema.\n\n```json\n" + string(data) + "\n```\n"
}
func LoadTemplate(path string) (string, error) {
	data, err := os.ReadFile(path)
	return string(data), err
}
func SafeFilename(value string) string {
	value = strings.Trim(safeFilenamePattern.ReplaceAllString(value, "-"), "-")
	if value == "" {
		return "worker"
	}
	return value
}
func CleanupFailureBundle(path string) {
	if path != "" {
		os.RemoveAll(path)
	}
}
func DetailWithBundle(detail, bundle string) string {
	if bundle == "" {
		return detail
	}
	line := "Failure bundle: " + bundle
	if strings.Contains(detail, line) {
		return detail
	}
	if strings.TrimSpace(detail) == "" {
		return line
	}
	return detail + "\n\n" + line
}
func WriteFailureManifest(bundle string, runID int, jobKey, status, errorMessage, resultPath, logPath string, payload any) string {
	return writeFailureManifest(bundle, runID, jobKey, status, errorMessage, resultPath, logPath, payload)
}
func writeFailureManifest(bundle string, runID int, jobKey, status, errorMessage, resultPath, logPath string, payload any) string {
	if bundle == "" {
		return ""
	}
	os.MkdirAll(bundle, 0o755)
	artifacts := map[string]any{}
	for key, value := range failureBundlePaths(bundle) {
		if key == "bundle_dir" {
			continue
		}
		artifactPath := fmt.Sprint(value)
		_, err := os.Stat(artifactPath)
		artifacts[key] = map[string]any{"path": artifactPath, "exists": err == nil}
	}
	manifest := map[string]any{"run_id": runID, "job_key": jobKey, "application_status": nullableValue(status), "error_message": nullableValue(errorMessage), "worker_result": payload, "result_path": nullableValue(resultPath), "log_path": nullableValue(logPath), "artifacts": artifacts}
	data, _ := json.MarshalIndent(manifest, "", "  ")
	path := filepath.Join(bundle, "failure_manifest.json")
	os.WriteFile(path, data, 0o600)
	return path
}
func failureBundlePaths(bundle string) map[string]any {
	return map[string]any{
		"bundle_dir":                 bundle,
		"runtime_context_path":       filepath.Join(bundle, "runtime_context.json"),
		"prompt_path":                filepath.Join(bundle, "prompt.txt"),
		"failure_manifest_path":      filepath.Join(bundle, "failure_manifest.json"),
		"playwright_snapshot_path":   filepath.Join(bundle, "playwright_snapshot.md"),
		"playwright_screenshot_path": filepath.Join(bundle, "playwright_screenshot.png"),
		"page_html_path":             filepath.Join(bundle, "page_html.html"),
		"console_path":               filepath.Join(bundle, "console.json"),
		"network_path":               filepath.Join(bundle, "network.json"),
	}
}
func cloneMap(value map[string]any) map[string]any {
	out := make(map[string]any, len(value))
	for key, item := range value {
		out[key] = item
	}
	return out
}
func writeLog(path string, command []string, prompt, stdout, stderr string, exitCode int, errorMessage string) {
	parts := []string{"COMMAND:", jsonString(command), "", fmt.Sprintf("EXIT_CODE: %d", exitCode)}
	if errorMessage != "" {
		parts = append(parts, "", "ERROR:", errorMessage)
	}
	parts = append(parts, "", "PROMPT:", prompt, "", "STDOUT:", stdout, "", "STDERR:", stderr, "")
	os.WriteFile(path, []byte(strings.Join(parts, "\n")), 0o600)
}
func nextSequence(dir, target string) int {
	pattern := regexp.MustCompile(`^` + regexp.QuoteMeta(target) + `\.invocation-(\d+)\.attempt-\d+\.(?:result\.json|log\.txt)$`)
	highest := 0
	entries, _ := os.ReadDir(dir)
	for _, entry := range entries {
		match := pattern.FindStringSubmatch(entry.Name())
		if len(match) > 1 {
			n, _ := strconv.Atoi(match[1])
			if n > highest {
				highest = n
			}
		}
	}
	return highest + 1
}
func optionalString(v any) string {
	if v == nil {
		return ""
	}
	return strings.TrimSpace(fmt.Sprint(v))
}
func nullable(v string) *string {
	if strings.TrimSpace(v) == "" {
		return nil
	}
	return &v
}
func nullableValue(v string) any {
	if strings.TrimSpace(v) == "" {
		return nil
	}
	return v
}
func toInt(v any) int {
	switch n := v.(type) {
	case int64:
		return int(n)
	case float64:
		return int(n)
	case int:
		return n
	}
	return 0
}
func errorText(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}
func jsonString(v any) string                 { data, _ := json.Marshal(v); return string(data) }
func timeDurationSeconds(v int) time.Duration { return time.Duration(v) * time.Second }
