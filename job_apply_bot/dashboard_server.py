from __future__ import annotations

from pathlib import Path
import os

from .dashboard_api import DB_PATH_ENV, REPO_ROOT_ENV, create_app


def serve_dashboard(
    *,
    repo_root: Path,
    db_path: Path,
    host: str,
    port: int,
    reload: bool,
) -> int:
    import uvicorn

    os.environ[REPO_ROOT_ENV] = str(repo_root.resolve())
    os.environ[DB_PATH_ENV] = str(db_path.resolve())
    if reload:
        uvicorn.run(
            "job_apply_bot.dashboard_api:create_app",
            factory=True,
            host=host,
            port=port,
            reload=True,
            reload_dirs=[str(repo_root.resolve())],
        )
        return 0

    app = create_app(repo_root=repo_root.resolve(), db_path=db_path.resolve())
    uvicorn.run(app, host=host, port=port)
    return 0
