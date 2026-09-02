package dashboard

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

type ReloadConfig struct {
	RepoRoot, DBPath, Host string
	Port                   int
}

// RunReloader rebuilds and restarts the dashboard backend when Go source or
// module files change. Frontend HMR remains owned by Vite.
func RunReloader(parent context.Context, config ReloadConfig) error {
	ctx, stop := signal.NotifyContext(parent, os.Interrupt)
	defer stop()
	goBin, err := findGo()
	if err != nil {
		return err
	}
	snapshot := sourceSnapshot(config.RepoRoot)
	generation := 0
	binary, err := buildReloadBinary(ctx, goBin, config, generation)
	if err != nil {
		return err
	}
	child, done, err := startReloadChild(ctx, binary, config, generation)
	if err != nil {
		return err
	}
	defer stopChild(child)
	ticker := time.NewTicker(750 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case err := <-done:
			if ctx.Err() != nil {
				return nil
			}
			return fmt.Errorf("reloaded dashboard server exited: %w", err)
		case <-ticker.C:
			next := sourceSnapshot(config.RepoRoot)
			if snapshotsEqual(snapshot, next) {
				continue
			}
			generation++
			binary, buildErr := buildReloadBinary(ctx, goBin, config, generation)
			if buildErr != nil {
				fmt.Fprintf(os.Stderr, "dashboard rebuild failed: %v\n", buildErr)
				snapshot = next
				continue
			}
			stopChild(child)
			select {
			case <-done:
			case <-time.After(2 * time.Second):
			}
			newChild, newDone, startErr := startReloadChild(ctx, binary, config, generation)
			if startErr != nil {
				return startErr
			}
			child, done, snapshot = newChild, newDone, next
		}
	}
}
func buildReloadBinary(ctx context.Context, goBin string, config ReloadConfig, generation int) (string, error) {
	dir := filepath.Join(config.RepoRoot, ".tmp", "dashboard-reload")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	name := fmt.Sprintf("job-apply-bot-%d", generation)
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	binary := filepath.Join(dir, name)
	build := exec.CommandContext(ctx, goBin, "build", "-o", binary, "./cmd/job-apply-bot")
	build.Dir = config.RepoRoot
	build.Stdout = os.Stdout
	build.Stderr = os.Stderr
	if err := build.Run(); err != nil {
		return "", err
	}
	return binary, nil
}
func startReloadChild(ctx context.Context, binary string, config ReloadConfig, generation int) (*exec.Cmd, <-chan error, error) {
	child := exec.CommandContext(ctx, binary, "--db-path", config.DBPath, "serve-dashboard", "--repo-root", config.RepoRoot, "--host", config.Host, "--port", fmt.Sprint(config.Port))
	child.Dir = config.RepoRoot
	child.Env = append(os.Environ(), "JOB_APPLY_BOT_RELOAD_CHILD=1")
	child.Stdout = os.Stdout
	child.Stderr = os.Stderr
	if err := child.Start(); err != nil {
		return nil, nil, err
	}
	done := make(chan error, 1)
	go func() { done <- child.Wait() }()
	fmt.Fprintf(os.Stderr, "dashboard reload server started (generation %d)\n", generation)
	return child, done, nil
}
func stopChild(cmd *exec.Cmd) {
	if cmd == nil || cmd.Process == nil {
		return
	}
	_ = cmd.Process.Kill()
}
func findGo() (string, error) {
	if path, err := exec.LookPath("go"); err == nil {
		return path, nil
	}
	if runtime.GOOS == "windows" {
		candidate := filepath.Join(os.Getenv("ProgramFiles"), "Go", "bin", "go.exe")
		if _, err := os.Stat(candidate); err == nil {
			return candidate, nil
		}
	}
	return "", fmt.Errorf("Go toolchain was not found; --reload requires Go in PATH")
}
func sourceSnapshot(root string) map[string]time.Time {
	out := map[string]time.Time{}
	for _, relative := range []string{"cmd", "internal"} {
		base := filepath.Join(root, relative)
		filepath.WalkDir(base, func(path string, entry os.DirEntry, err error) error {
			if err != nil || entry.IsDir() {
				return nil
			}
			if strings.HasSuffix(path, ".go") {
				if info, statErr := entry.Info(); statErr == nil {
					out[path] = info.ModTime()
				}
			}
			return nil
		})
	}
	for _, name := range []string{"go.mod", "go.sum"} {
		path := filepath.Join(root, name)
		if info, err := os.Stat(path); err == nil {
			out[path] = info.ModTime()
		}
	}
	return out
}
func snapshotsEqual(a, b map[string]time.Time) bool {
	if len(a) != len(b) {
		return false
	}
	for key, value := range a {
		other, ok := b[key]
		if !ok || !value.Equal(other) {
			return false
		}
	}
	return true
}
