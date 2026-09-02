package cli

import (
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"testing"
)

func captureExecute(t *testing.T, args ...string) (int, map[string]any) {
	t.Helper()
	old := os.Stdout
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	os.Stdout = writer
	code := Execute(args)
	writer.Close()
	os.Stdout = old
	data, _ := io.ReadAll(reader)
	payload := map[string]any{}
	json.Unmarshal(data, &payload)
	return code, payload
}
func cliProfileRoot(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, "resume.pdf"), []byte("pdf"), 0o600)
	os.WriteFile(filepath.Join(root, ".env"), []byte(`APPLICANT_FULL_NAME=Test Person
APPLICANT_EMAIL=test@example.com
APPLICANT_PHONE=555
APPLICANT_LOCATION=Tempe, AZ
APPLICANT_RESUME_PATH=resume.pdf
APPLICANT_US_WORK_AUTHORIZED=true
APPLICANT_REQUIRES_VISA_SPONSORSHIP=false
`), 0o600)
	os.WriteFile(filepath.Join(root, "applicant.md"), []byte("## Work Authorization Notes\nYes\n\n## Reusable Highlights\nYes\n"), 0o600)
	return root
}
func TestValidateProfileExitCodesAndJSON(t *testing.T) {
	code, payload := captureExecute(t, "validate-profile", "--repo-root", cliProfileRoot(t))
	if code != 0 || payload["ok"] != true {
		t.Fatalf("code=%d payload=%#v", code, payload)
	}
	code, payload = captureExecute(t, "validate-profile", "--repo-root", t.TempDir())
	if code != 2 || payload["ok"] != false {
		t.Fatalf("code=%d payload=%#v", code, payload)
	}
}
func TestRunLifecycleCommands(t *testing.T) {
	database := filepath.Join(t.TempDir(), "jobs.sqlite3")
	code, payload := captureExecute(t, "--db-path", database, "start-run")
	if code != 0 || payload["id"].(float64) != 1 {
		t.Fatalf("code=%d payload=%#v", code, payload)
	}
	code, payload = captureExecute(t, "--db-path", database, "workflow-status", "--run-id", "1")
	if code != 0 || payload["drained"] != true {
		t.Fatalf("code=%d payload=%#v", code, payload)
	}
	code, payload = captureExecute(t, "--db-path", database, "finish-run", "--run-id", "1")
	if code != 0 || payload["finished_at"] == nil {
		t.Fatalf("code=%d payload=%#v", code, payload)
	}
}
func TestPublicCommandsAndWorkerDefaults(t *testing.T) {
	root := (&app{}).root()
	for _, name := range []string{"validate-profile", "start-run", "prepare-run", "finish-run", "ingest-job", "next-job", "next-query", "claim-query", "complete-query", "fail-query", "workflow-status", "record-application", "record-finding", "discover-next-candidate-with-codex", "apply-job-with-codex", "run-workflow", "requeue-runner-failures", "serve-dashboard"} {
		if _, _, err := root.Find([]string{name}); err != nil {
			t.Fatalf("missing command %s: %v", name, err)
		}
	}
	command, _, _ := root.Find([]string{"run-workflow"})
	for name, want := range map[string]string{"query-timeout-seconds": "0", "job-timeout-seconds": "0", "max-worker-retries": "1", "discovery-workers": "auto", "apply-workers": "5"} {
		if got := command.Flags().Lookup(name).DefValue; got != want {
			t.Fatalf("%s default=%q want=%q", name, got, want)
		}
	}
}
