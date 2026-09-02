package dashboard

import (
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"mime"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"strconv"
	"strings"
)

//go:embed all:web
var webAssets embed.FS

type Server struct{ Service *Service }

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/health", func(w http.ResponseWriter, r *http.Request) { writeJSON(w, 200, map[string]string{"status": "ok"}) })
	mux.HandleFunc("GET /api/runs", s.wrap(func(r *http.Request) (any, error) { return s.Service.Runs(r.Context()) }))
	mux.HandleFunc("POST /api/runs", s.wrap(func(r *http.Request) (any, error) { return s.Service.StartRun(r.Context()) }))
	mux.HandleFunc("GET /api/runs/{run_id}", s.wrap(func(r *http.Request) (any, error) { return s.Service.RunDetail(r.Context(), pathInt(r, "run_id")) }))
	mux.HandleFunc("POST /api/runs/{run_id}/resume", s.wrap(func(r *http.Request) (any, error) { return s.Service.ResumeRun(r.Context(), pathInt(r, "run_id")) }))
	mux.HandleFunc("POST /api/runs/{run_id}/requeue-runner-failures", s.wrap(func(r *http.Request) (any, error) { return s.Service.Requeue(r.Context(), pathInt(r, "run_id")) }))
	mux.HandleFunc("POST /api/runs/{run_id}/finish", s.wrap(func(r *http.Request) (any, error) {
		var body struct {
			Force bool `json:"force"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			return nil, &HTTPError{400, "Invalid JSON request body."}
		}
		return s.Service.Finish(r.Context(), pathInt(r, "run_id"), body.Force)
	}))
	mux.HandleFunc("GET /api/jobs", s.wrap(func(r *http.Request) (any, error) {
		query := r.URL.Query()
		var runID *int
		if raw := query.Get("run_id"); raw != "" {
			id, err := strconv.Atoi(raw)
			if err != nil {
				return nil, &HTTPError{400, "Invalid run_id."}
			}
			runID = &id
		}
		page := queryInt(query.Get("page"), 1)
		size := queryInt(query.Get("page_size"), 20)
		return s.Service.Jobs(r.Context(), JobsQuery{RunID: runID, Status: query.Get("status"), Source: query.Get("source"), Q: query.Get("q"), Page: page, PageSize: size})
	}))
	mux.HandleFunc("GET /api/jobs/{job_key}", s.wrap(func(r *http.Request) (any, error) { return s.Service.JobDetail(r.Context(), r.PathValue("job_key")) }))
	mux.HandleFunc("GET /api/resume-customizations/{customization_id}", s.wrap(func(r *http.Request) (any, error) {
		return s.Service.ResumeCustomization(r.Context(), pathInt(r, "customization_id"))
	}))
	mux.HandleFunc("GET /api/resume-customizations/{customization_id}/file", func(w http.ResponseWriter, r *http.Request) {
		detail, err := s.Service.ResumeCustomization(r.Context(), pathInt(r, "customization_id"))
		if err != nil {
			s.writeError(w, err)
			return
		}
		pdf := text(detail["rendered_pdf_path"])
		if pdf == "" {
			s.writeError(w, &HTTPError{404, fmt.Sprintf("Rendered resume file for customization %d was not found.", pathInt(r, "customization_id"))})
			return
		}
		if _, err = os.Stat(pdf); err != nil {
			s.writeError(w, &HTTPError{404, fmt.Sprintf("Rendered resume file for customization %d was not found.", pathInt(r, "customization_id"))})
			return
		}
		w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s"`, filepath.Base(pdf)))
		http.ServeFile(w, r, pdf)
	})
	mux.HandleFunc("/", s.serveSPA)
	return cors(mux)
}
func (s *Server) wrap(fn func(*http.Request) (any, error)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		value, err := fn(r)
		if err != nil {
			s.writeError(w, err)
			return
		}
		writeJSON(w, 200, value)
	}
}
func (s *Server) writeError(w http.ResponseWriter, err error) {
	status := 400
	var httpErr *HTTPError
	if errors.As(err, &httpErr) {
		status = httpErr.Status
	}
	writeJSON(w, status, map[string]string{"detail": err.Error()})
}
func (s *Server) serveSPA(w http.ResponseWriter, r *http.Request) {
	if strings.HasPrefix(r.URL.Path, "/api/") {
		s.writeError(w, &HTTPError{404, "Not found"})
		return
	}
	name := strings.TrimPrefix(path.Clean(r.URL.Path), "/")
	if name == "." || name == "" {
		name = "index.html"
	}
	diskPath := filepath.Join(s.Service.RepoRoot, "internal", "dashboard", "web", "dist", filepath.FromSlash(name))
	if data, diskErr := os.ReadFile(diskPath); diskErr == nil {
		if media := mime.TypeByExtension(filepath.Ext(name)); media != "" {
			w.Header().Set("Content-Type", media)
		}
		w.Write(data)
		return
	}
	if name != "index.html" {
		if data, diskErr := os.ReadFile(filepath.Join(s.Service.RepoRoot, "internal", "dashboard", "web", "dist", "index.html")); diskErr == nil {
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			w.Write(data)
			return
		}
	}
	assetPath := "web/dist/" + name
	data, err := webAssets.ReadFile(assetPath)
	if err != nil && name != "index.html" {
		data, err = webAssets.ReadFile("web/dist/index.html")
	}
	if err != nil {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.WriteHeader(200)
		w.Write([]byte(`<html><head><title>Dashboard Not Built</title></head><body><h1>Dashboard frontend not built yet.</h1><p>Run <code>npm install</code> and <code>npm run build</code> inside <code>frontend/</code>, or use <code>npm run dev</code>.</p></body></html>`))
		return
	}
	if media := mime.TypeByExtension(filepath.Ext(name)); media != "" {
		w.Header().Set("Content-Type", media)
	}
	w.Write(data)
}
func cors(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		origin := r.Header.Get("Origin")
		if origin == "http://127.0.0.1:5173" || origin == "http://localhost:5173" {
			w.Header().Set("Access-Control-Allow-Origin", origin)
			w.Header().Set("Vary", "Origin")
		}
		w.Header().Set("Access-Control-Allow-Headers", "*")
		w.Header().Set("Access-Control-Allow-Methods", "*")
		if r.Method == http.MethodOptions {
			w.WriteHeader(204)
			return
		}
		next.ServeHTTP(w, r)
	})
}
func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	encoder := json.NewEncoder(w)
	encoder.SetEscapeHTML(false)
	encoder.Encode(value)
}
func pathInt(r *http.Request, name string) int {
	value, _ := strconv.Atoi(r.PathValue(name))
	return value
}
func queryInt(raw string, fallback int) int {
	value, err := strconv.Atoi(raw)
	if err != nil {
		return fallback
	}
	return value
}
func AssetFS() (fs.FS, error) { return fs.Sub(webAssets, "web/dist") }
