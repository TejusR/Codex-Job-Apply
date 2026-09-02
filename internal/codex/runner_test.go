package codex

import (
	"context"
	"os/exec"
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestPlaywrightOnlyOverrides(t *testing.T) {
	overrides, err := playwrightOverrides()
	if err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(overrides, " ")
	for _, want := range []string{`model_reasoning_effort="low"`, `plugins={}`, `mcp_servers={playwright=`} {
		if !strings.Contains(joined, want) {
			t.Fatalf("missing %q in %s", want, joined)
		}
	}
}
func TestThreadIDExtractionAndPayloadRecovery(t *testing.T) {
	stream := "noise\n" + `{"type":"thread.started","thread_id":"thread-123"}` + "\n"
	if got := coalesceThreadID("", stream); got != "thread-123" {
		t.Fatalf("got %q", got)
	}
	validator := func(payload map[string]any) error { return nil }
	payload := loadRecovered("missing.json", `{"status":"ok"}`, "", validator)
	if payload == nil || payload["status"] != "ok" {
		t.Fatalf("payload: %#v", payload)
	}
}
func TestCommandContextTerminatesTimedOutProcess(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	var name string
	var args []string
	if runtime.GOOS == "windows" {
		name = "powershell.exe"
		args = []string{"-NoProfile", "-Command", "Start-Sleep -Seconds 5"}
	} else {
		name = "sh"
		args = []string{"-c", "sleep 5"}
	}
	start := time.Now()
	_, _, _, err := runCommand(ctx, ".", name, args, "")
	if err == nil {
		t.Fatal("expected timeout")
	}
	if time.Since(start) > 3*time.Second {
		t.Fatalf("process was not terminated promptly: %s", time.Since(start))
	}
}
func TestDefaultCodexBinaryName(t *testing.T) {
	value := DefaultCodexBin()
	if strings.TrimSpace(value) == "" {
		t.Fatal("empty codex binary")
	}
	_, _ = exec.LookPath(value)
}
