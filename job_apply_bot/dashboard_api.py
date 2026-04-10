from __future__ import annotations

from pathlib import Path
import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .dashboard_models import (
    FinishRunRequest,
    JobDetail,
    JobListResponse,
    RequeueRunnerFailuresResponse,
    RunActionResponse,
    RunDetail,
    RunsResponse,
)
from .dashboard_service import (
    DashboardConflictError,
    DashboardError,
    DashboardNotFoundError,
    finish_run_from_dashboard,
    get_job_detail,
    get_run_detail,
    list_jobs,
    list_runs_overview,
    requeue_failed_jobs_for_run,
    resume_run_workflow,
    start_run_workflow,
)


REPO_ROOT_ENV = "JOB_APPLY_BOT_DASHBOARD_REPO_ROOT"
DB_PATH_ENV = "JOB_APPLY_BOT_DASHBOARD_DB_PATH"


def create_app(
    *,
    repo_root: Path | None = None,
    db_path: Path | None = None,
) -> FastAPI:
    resolved_repo_root = _resolve_repo_root(repo_root)
    resolved_db_path = _resolve_db_path(db_path, repo_root=resolved_repo_root)
    app = FastAPI(
        title="Job Apply Bot Dashboard",
        version="0.1.0",
        description="Local operator dashboard for workflow runs and job tracking.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/runs", response_model=RunsResponse)
    def get_runs() -> RunsResponse:
        return list_runs_overview(resolved_db_path)

    @app.post("/api/runs", response_model=RunActionResponse)
    def start_run() -> RunActionResponse:
        return _handle_dashboard_call(
            lambda: start_run_workflow(resolved_db_path, repo_root=resolved_repo_root)
        )

    @app.get("/api/runs/{run_id}", response_model=RunDetail)
    def get_run(run_id: int) -> RunDetail:
        return _handle_dashboard_call(
            lambda: get_run_detail(resolved_db_path, resolved_repo_root, run_id)
        )

    @app.post("/api/runs/{run_id}/resume", response_model=RunActionResponse)
    def resume_run(run_id: int) -> RunActionResponse:
        return _handle_dashboard_call(
            lambda: resume_run_workflow(
                resolved_db_path,
                repo_root=resolved_repo_root,
                run_id=run_id,
            )
        )

    @app.post(
        "/api/runs/{run_id}/requeue-runner-failures",
        response_model=RequeueRunnerFailuresResponse,
    )
    def requeue_runner_failures(run_id: int) -> RequeueRunnerFailuresResponse:
        return _handle_dashboard_call(
            lambda: requeue_failed_jobs_for_run(resolved_db_path, run_id=run_id)
        )

    @app.post("/api/runs/{run_id}/finish", response_model=RunActionResponse)
    def finish_existing_run(run_id: int, request: FinishRunRequest) -> RunActionResponse:
        return _handle_dashboard_call(
            lambda: finish_run_from_dashboard(
                resolved_db_path,
                run_id=run_id,
                force=request.force,
            )
        )

    @app.get("/api/jobs", response_model=JobListResponse)
    def get_jobs(
        run_id: int | None = Query(default=None),
        status: str | None = Query(default=None),
        source: str | None = Query(default=None),
        q: str | None = Query(default=None),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> JobListResponse:
        return _handle_dashboard_call(
            lambda: list_jobs(
                resolved_db_path,
                repo_root=resolved_repo_root,
                run_id=run_id,
                status=status,
                source=source,
                q=q,
                page=page,
                page_size=page_size,
            )
        )

    @app.get("/api/jobs/{job_key}", response_model=JobDetail)
    def get_job(job_key: str) -> JobDetail:
        return _handle_dashboard_call(
            lambda: get_job_detail(
                resolved_db_path,
                repo_root=resolved_repo_root,
                job_key=job_key,
            )
        )

    _mount_frontend(app, repo_root=resolved_repo_root)
    return app


def _resolve_repo_root(repo_root: Path | None) -> Path:
    if repo_root is not None:
        return repo_root.resolve()
    env_value = os.environ.get(REPO_ROOT_ENV)
    if env_value:
        return Path(env_value).resolve()
    return Path.cwd().resolve()


def _resolve_db_path(db_path: Path | None, *, repo_root: Path) -> Path:
    if db_path is not None:
        return db_path.resolve()
    env_value = os.environ.get(DB_PATH_ENV)
    if env_value:
        return Path(env_value).resolve()
    return (repo_root / "data" / "job_apply_bot.sqlite3").resolve()


def _handle_dashboard_call(func):
    try:
        return func()
    except DashboardNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DashboardConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DashboardError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _mount_frontend(app: FastAPI, *, repo_root: Path) -> None:
    dist_dir = repo_root / "frontend" / "dist"
    assets_dir = dist_dir / "assets"
    index_path = dist_dir / "index.html"

    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    if not index_path.exists():

        @app.get("/", include_in_schema=False)
        def dashboard_not_built() -> HTMLResponse:
            return HTMLResponse(
                """
                <html>
                  <head><title>Dashboard Not Built</title></head>
                  <body style="font-family: Segoe UI, sans-serif; padding: 40px;">
                    <h1>Dashboard frontend not built yet.</h1>
                    <p>Run <code>npm install</code> and <code>npm run build</code> inside <code>frontend/</code>, or use <code>npm run dev</code> for local development.</p>
                  </body>
                </html>
                """.strip()
            )

        return

    @app.get("/", include_in_schema=False)
    def serve_index() -> FileResponse:
        return FileResponse(index_path)

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = dist_dir / full_path
        if candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_path)
